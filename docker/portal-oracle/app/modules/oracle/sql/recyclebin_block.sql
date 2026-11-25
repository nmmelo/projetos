-- Bloco PL/SQL do purge da recyclebin por schema, executado pelo portal como o shrink (mesmo acompanhamento:
-- V$SESSION.ACTION / V$SESSION_LONGOPS com OPNAME 'SHRINK_TS' e linhas em direto por DBMS_PIPE '&p_pipe').
-- Marcadores substituidos pelo portal com valores ja validados:
--   &p_modo    ANALISE (so mostra) | EXECUTAR (faz o PURGE)
--   &p_schemas lista de schemas entre plicas, separados por virgulas (vazio = todos os que tem objetos)
-- Para cada schema: PURGE TABLESPACE "<ts>" USER "<schema>" por tablespace; o que ficar (ex. sem tablespace,
-- ou sem privilegio para o purge por tablespace) e tentado objeto a objeto (PURGE TABLE / PURGE INDEX).
DECLARE
  c_exec    CONSTANT BOOLEAN := UPPER(TRIM('&p_modo')) = 'EXECUTAR';
  c_lista   sys.odcivarchar2list := sys.odcivarchar2list(&p_schemas);
  c_todos   PLS_INTEGER := CASE WHEN c_lista.COUNT = 0 THEN 1 ELSE 0 END;

  g_pipe     VARCHAR2(128) := '&p_pipe';
  g_pipe_ok  BOOLEAN       := g_pipe IS NOT NULL;
  g_rindex   BINARY_INTEGER := DBMS_APPLICATION_INFO.SET_SESSION_LONGOPS_NOHINT;
  g_slno     BINARY_INTEGER;
  g_base     NUMBER := 0;
  g_fatia    NUMBER := 1000;

  v_bs      NUMBER;
  v_ok      PLS_INTEGER := 0;
  v_err     PLS_INTEGER := 0;
  g_tot_ok  PLS_INTEGER := 0;
  g_tot_err PLS_INTEGER := 0;

  TYPE t_txt IS TABLE OF VARCHAR2(128);
  g_donos t_txt;

  TYPE r_res IS RECORD (ts VARCHAR2(128), antes NUMBER, depois NUMBER, ganho NUMBER,
                        reorg VARCHAR2(3), erros NUMBER, estado VARCHAR2(200));
  TYPE t_res IS TABLE OF r_res INDEX BY PLS_INTEGER;
  g_res t_res;

  PROCEDURE add_res(ts VARCHAR2, antes NUMBER, depois NUMBER, ganho NUMBER, erros NUMBER, estado VARCHAR2) IS
    n PLS_INTEGER := g_res.COUNT + 1;
  BEGIN
    g_res(n).ts := ts; g_res(n).antes := antes; g_res(n).depois := depois; g_res(n).ganho := ganho;
    g_res(n).reorg := '-'; g_res(n).erros := erros; g_res(n).estado := SUBSTR(estado, 1, 200);
  END;

  PROCEDURE p(m VARCHAR2) IS
  BEGIN
    DBMS_OUTPUT.PUT_LINE(m);
    IF g_pipe_ok THEN
      BEGIN
        EXECUTE IMMEDIATE 'DECLARE r INTEGER; BEGIN DBMS_PIPE.RESET_BUFFER; ' ||
                          'DBMS_PIPE.PACK_MESSAGE(:1); r := DBMS_PIPE.SEND_MESSAGE(:2, 0, 4194304); END;'
          USING NVL(SUBSTR(m, 1, 4000), ' '), g_pipe;
      EXCEPTION WHEN OTHERS THEN
        g_pipe_ok := FALSE;
      END;
    END IF;
  END;

  PROCEDURE passo(a VARCHAR2, pct NUMBER DEFAULT NULL) IS
  BEGIN
    DBMS_APPLICATION_INFO.SET_MODULE('SHRINK_TS', SUBSTR(a, 1, 64));
    IF pct IS NOT NULL THEN
      DBMS_APPLICATION_INFO.SET_SESSION_LONGOPS(
        rindex => g_rindex, slno => g_slno, op_name => 'SHRINK_TS',
        sofar => LEAST(ROUND(g_base + g_fatia * pct / 100), 1000), totalwork => 1000,
        target_desc => SUBSTR(a, 1, 32), units => 'permil');
    END IF;
  END;

  FUNCTION q(n VARCHAR2) RETURN VARCHAR2 IS BEGIN RETURN '"' || REPLACE(n, '"', '') || '"'; END;

  -- Mostra o comando e, em EXECUTAR, executa-o
  PROCEDURE run(s VARCHAR2) IS
  BEGIN
    p(s || ';');
    IF c_exec THEN
      BEGIN
        EXECUTE IMMEDIATE s;
        v_ok := v_ok + 1;
        p('   -- OK');
      EXCEPTION WHEN OTHERS THEN
        v_err := v_err + 1;
        p('   -- ERRO: ' || SQLERRM);
      END;
    END IF;
  END;

  FUNCTION mb(o VARCHAR2) RETURN NUMBER IS
    v NUMBER;
  BEGIN
    SELECT NVL(SUM(r.space * NVL(t.block_size, v_bs)), 0) / 1048576 INTO v
      FROM dba_recyclebin r LEFT JOIN dba_tablespaces t ON t.tablespace_name = r.ts_name
     WHERE r.owner = o;
    RETURN v;
  END;

  FUNCTION n_obj(o VARCHAR2) RETURN NUMBER IS
    v NUMBER;
  BEGIN
    SELECT COUNT(*) INTO v FROM dba_recyclebin WHERE owner = o;
    RETURN v;
  END;

  PROCEDURE processa(o VARCHAR2) IS
    v_antes  NUMBER := mb(o);
    v_n      NUMBER := n_obj(o);
    v_depois NUMBER;
    v_resto  NUMBER;
  BEGIN
    v_ok := 0; v_err := 0;
    passo(o || ' 1 recyclebin', 5);
    p('');
    p('#############################################################################');
    p('#### SCHEMA ' || o);
    p('#############################################################################');
    IF v_n = 0 THEN
      p('-- recyclebin vazia');
      add_res(o, 0, 0, 0, 0, 'VAZIA');
      RETURN;
    END IF;
    p(RPAD('Objetos na recyclebin', 42) || ': ' || v_n);
    p(RPAD('Espaco ocupado', 42) || ': ' || ROUND(v_antes, 1) || ' MB');
    p('===== Objetos (maiores primeiro, ate 30) =====');
    FOR r IN (SELECT * FROM (
                SELECT r.original_name, r.type, r.ts_name, r.droptime,
                       ROUND(r.space * NVL(t.block_size, v_bs) / 1048576, 2) AS mb
                  FROM dba_recyclebin r LEFT JOIN dba_tablespaces t ON t.tablespace_name = r.ts_name
                 WHERE r.owner = o ORDER BY r.space DESC) WHERE ROWNUM <= 30) LOOP
      p('  ' || RPAD(NVL(r.original_name, '-'), 32) || RPAD(r.type, 14) || RPAD(NVL(r.ts_name, '-'), 24) ||
        RPAD(NVL(r.droptime, '-'), 21) || LPAD(TO_CHAR(r.mb, 'FM999999990.00'), 12) || ' MB');
    END LOOP;

    p('===== Purge por tablespace =====');
    passo(o || ' 2 purge', 30);
    FOR t IN (SELECT DISTINCT ts_name FROM dba_recyclebin WHERE owner = o AND ts_name IS NOT NULL ORDER BY ts_name) LOOP
      run('PURGE TABLESPACE ' || q(t.ts_name) || ' USER ' || q(o));
    END LOOP;

    IF c_exec THEN
      -- o que ficou (sem tablespace, sem privilegio para o purge por tablespace, ...): objeto a objeto
      SELECT COUNT(*) INTO v_resto FROM dba_recyclebin WHERE owner = o;
      IF v_resto > 0 THEN
        p('===== Objetos que ficaram: purge um a um =====');
        passo(o || ' 3 objetos', 70);
        -- tabelas primeiro (levam os seus indices e LOBs); depois os indices que ainda ficarem
        FOR r IN (SELECT object_name FROM dba_recyclebin
                   WHERE owner = o AND can_purge = 'YES' AND type = 'TABLE') LOOP
          run('PURGE TABLE ' || q(o) || '.' || q(r.object_name));
        END LOOP;
        FOR r IN (SELECT object_name FROM dba_recyclebin
                   WHERE owner = o AND can_purge = 'YES' AND type = 'INDEX') LOOP
          run('PURGE INDEX ' || q(o) || '.' || q(r.object_name));
        END LOOP;
      END IF;
      v_depois := mb(o);
    ELSE
      v_depois := 0;
    END IF;

    passo(o || ' fim', 100);
    p('===== Resumo ' || o || ' =====');
    IF c_exec THEN
      p(RPAD('Espaco antes / depois', 42) || ': ' || ROUND(v_antes, 1) || ' MB / ' || ROUND(v_depois, 1) || ' MB');
      p(RPAD('Espaco libertado', 42) || ': ' || ROUND(v_antes - v_depois, 1) || ' MB');
      p(RPAD('Objetos que ficaram', 42) || ': ' || n_obj(o));
      p(RPAD('Comandos OK / ERRO', 42) || ': ' || v_ok || ' / ' || v_err);
    ELSE
      p(RPAD('Ganho estimado', 42) || ': ' || ROUND(v_antes, 1) || ' MB');
    END IF;
    g_tot_ok := g_tot_ok + v_ok; g_tot_err := g_tot_err + v_err;
    add_res(o, ROUND(v_antes), ROUND(v_depois), ROUND(v_antes - v_depois), v_err,
            CASE WHEN v_err > 0 THEN 'COM ERROS (ver log)'
                 WHEN c_exec AND v_depois > 0 THEN 'OK, ficaram ' || n_obj(o) || ' objeto(s)'
                 WHEN c_exec THEN 'OK' ELSE 'ANALISE' END);
  END processa;

BEGIN
  DBMS_OUTPUT.ENABLE(NULL);
  SELECT TO_NUMBER(value) INTO v_bs FROM v$parameter WHERE name = 'db_block_size';
  passo('0 lista de schemas', 0);
  p('Purge da recyclebin - modo ' || CASE WHEN c_exec THEN 'EXECUTAR' ELSE 'ANALISE (nada sera alterado)' END);

  IF c_todos = 1 THEN
    SELECT DISTINCT owner BULK COLLECT INTO g_donos FROM dba_recyclebin ORDER BY owner;
  ELSE
    SELECT column_value BULK COLLECT INTO g_donos FROM TABLE(c_lista) ORDER BY 1;
  END IF;
  IF g_donos.COUNT = 0 THEN
    p('-- a recyclebin esta vazia: nada a fazer');
  END IF;

  g_fatia := 1000 / GREATEST(g_donos.COUNT, 1);
  FOR i IN 1 .. g_donos.COUNT LOOP
    g_base := (i - 1) * g_fatia;
    BEGIN
      processa(g_donos(i));
    EXCEPTION WHEN OTHERS THEN
      p('-- ERRO no schema ' || g_donos(i) || ': ' || SQLERRM);
      add_res(g_donos(i), NULL, NULL, NULL, 1, 'ERRO: ' || SQLERRM);
      g_tot_err := g_tot_err + 1;
    END;
  END LOOP;

  g_base := 0; g_fatia := 1000;
  passo('fim', 100);
  p('');
  p('=============================================================================');
  p('===== RESUMO GLOBAL (' || CASE WHEN c_exec THEN 'EXECUTAR' ELSE 'ANALISE - ganho estimado' END ||
    ') - recyclebin - contentor ' || SYS_CONTEXT('USERENV', 'CON_NAME') || ' =====');
  p(RPAD('SCHEMA', 30) || LPAD('ANTES_MB', 10) || LPAD('DEPOIS_MB', 11) ||
    LPAD('GANHO_MB', 10) || LPAD('REORG', 7) || LPAD('ERROS', 7) || '  ESTADO');
  p(RPAD('-', 120, '-'));
  DECLARE v_g NUMBER := 0;
  BEGIN
    FOR i IN 1 .. g_res.COUNT LOOP
      p(RPAD(g_res(i).ts, 30) || LPAD(NVL(TO_CHAR(g_res(i).antes), '-'), 10) ||
        LPAD(NVL(TO_CHAR(g_res(i).depois), '-'), 11) ||
        LPAD(NVL(TO_CHAR(g_res(i).ganho), '-'), 10) || LPAD(g_res(i).reorg, 7) ||
        LPAD(g_res(i).erros, 7) || '  ' || g_res(i).estado);
      v_g := v_g + NVL(g_res(i).ganho, 0);
    END LOOP;
    p(RPAD('-', 120, '-'));
    p(RPAD('TOTAL', 30) || LPAD(' ', 21) || LPAD(v_g, 10));
    IF c_exec THEN
      p('Comandos OK / ERRO: ' || g_tot_ok || ' / ' || g_tot_err);
    END IF;
  END;
END;
