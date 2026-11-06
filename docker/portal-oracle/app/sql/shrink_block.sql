-- Bloco PL/SQL do shrink_tablespace.sql, executado pelo portal via python-oracledb.
-- Os marcadores &p_* sao substituidos pelo portal com valores ja validados.
DECLARE
  c_sel     CONSTANT VARCHAR2(128) := UPPER(TRIM('&p_ts'));
  c_todos   CONSTANT BOOLEAN       := c_sel IN ('TODOS', 'ALL', '*');
  g_ts      VARCHAR2(128);           -- tablespace em processamento
  c_exec    CONSTANT BOOLEAN       := UPPER(TRIM('&p_modo'))   = 'EXECUTAR';
  c_online  CONSTANT BOOLEAN       := UPPER(TRIM('&p_online')) = 'S';
  c_purge   CONSTANT BOOLEAN       := UPPER(TRIM('&p_purge'))  = 'S';
  c_min     CONSTANT NUMBER        := &p_min_ganho;
  c_margem  CONSTANT NUMBER        := &p_margem / 100;
  c_hdr     CONSTANT NUMBER        := 128;  -- blocos reservados no inicio de cada datafile
  c_passes  CONSTANT PLS_INTEGER   := 2;    -- passagens de reorganizacao (a 2.a corrige segmentos que o MOVE pos no fim)

  v_bs        NUMBER;
  v_contents  dba_tablespaces.contents%TYPE;
  v_status    dba_tablespaces.status%TYPE;
  v_ini       NUMBER;
  v_fim       NUMBER;
  v_direto    NUMBER := 0;   -- bytes recuperaveis ja (resize ate HWM)
  v_potencial NUMBER := 0;   -- bytes recuperaveis apos reorganizacao (estimado)
  v_rb        NUMBER := 0;
  v_ok        PLS_INTEGER := 0;
  v_err       PLS_INTEGER := 0;
  v_manual    PLS_INTEGER := 0;
  v_reorg     BOOLEAN := FALSE;
  v_t0        NUMBER;
  v_tmp_left  PLS_INTEGER := 0;       -- segmentos TEMPORARY que ficaram por limpar
  v_tsn       NUMBER;                 -- ts# do tablespace
  v_con       NUMBER := TO_NUMBER(SYS_CONTEXT('USERENV', 'CON_ID'));
  g_fast      BOOLEAN := FALSE;       -- TRUE = le SYS.X$KTFBUE (rapido); FALSE = DBA_EXTENTS

  TYPE t_num   IS TABLE OF NUMBER        INDEX BY PLS_INTEGER;
  TYPE t_txt   IS TABLE OF VARCHAR2(600) INDEX BY PLS_INTEGER;
  TYPE t_nlist IS TABLE OF NUMBER;
  g_tgt   t_num;   -- bloco alvo por file_id
  g_hwm   t_num;
  g_used  t_num;
  g_bytes t_num;
  g_name  t_txt;

  -- SQL rapido: extents lidos diretamente de X$KTFBUE (evita a juncao lenta de DBA_EXTENTS)
  c_sql_hwm CONSTANT VARCHAR2(4000) :=
    'SELECT d.file_id, MAX(e.ktfbuebno + e.ktfbueblks - 1)
       FROM sys.x$ktfbue e, dba_data_files d
      WHERE e.ktfbuesegtsn = :1
        AND d.tablespace_name = :2 AND d.relative_fno = e.ktfbuefno
      GROUP BY d.file_id';

  c_sql_seg_hwm CONSTANT VARCHAR2(4000) :=
    'SELECT d.file_id, MAX(e.ktfbuebno + e.ktfbueblks - 1)
       FROM dba_segments s, sys.x$ktfbue e, dba_data_files d
      WHERE s.owner = :1 AND s.segment_name = :2
        AND NVL(s.partition_name, ''~'') = NVL(:3, ''~'')
        AND s.tablespace_name = :4
        AND e.ktfbuesegtsn = :5
        AND e.ktfbuesegfno = s.relative_fno AND e.ktfbuesegbno = s.header_block
        AND d.tablespace_name = :6 AND d.relative_fno = e.ktfbuefno
      GROUP BY d.file_id';

  c_sql_cand CONSTANT VARCHAR2(4000) :=
    'SELECT s.owner, s.segment_name, s.partition_name, s.segment_type,
            ROUND(s.bytes / 1048576, 1)
       FROM dba_segments s
      WHERE s.tablespace_name = :1
        AND s.segment_type NOT IN (''LOBINDEX'', ''TEMPORARY'')
        AND s.segment_name NOT LIKE ''BIN$%''
        AND (s.relative_fno, s.header_block) IN (
              SELECT e.ktfbuesegfno, e.ktfbuesegbno
                FROM sys.x$ktfbue e,
                     (SELECT d.relative_fno rfno,
                             :2 + CEIL(GREATEST(d.blocks - :3 - NVL(fs.free_blk, 0), 0) * (1 + :4)) tgt
                        FROM dba_data_files d
                        LEFT JOIN (SELECT file_id, SUM(blocks) free_blk
                                     FROM dba_free_space WHERE tablespace_name = :5
                                    GROUP BY file_id) fs ON fs.file_id = d.file_id
                       WHERE d.tablespace_name = :6) u
               WHERE e.ktfbuesegtsn = :7
                 AND u.rfno = e.ktfbuefno
                 AND e.ktfbuebno + e.ktfbueblks - 1 > u.tgt)
      ORDER BY CASE WHEN s.segment_type LIKE ''INDEX%'' THEN 2 ELSE 1 END, s.bytes DESC';

  TYPE r_seg IS RECORD (owner VARCHAR2(128), seg VARCHAR2(128), part VARCHAR2(128),
                        tipo  VARCHAR2(30),  mb  NUMBER);
  TYPE t_seg IS TABLE OF r_seg;
  g_seg t_seg;

  -- Resumo global (uma linha por tablespace)
  TYPE r_res IS RECORD (ts VARCHAR2(128), antes NUMBER, depois NUMBER, ganho NUMBER,
                        reorg VARCHAR2(3), erros NUMBER, estado VARCHAR2(200));
  TYPE t_res IS TABLE OF r_res INDEX BY PLS_INTEGER;
  g_res   t_res;
  g_tot_ok  PLS_INTEGER := 0;
  g_tot_err PLS_INTEGER := 0;
  v_livre   NUMBER;

  PROCEDURE add_res(ts VARCHAR2, antes NUMBER, depois NUMBER, ganho NUMBER,
                    reorg VARCHAR2, erros NUMBER, estado VARCHAR2) IS
    n PLS_INTEGER := g_res.COUNT + 1;
  BEGIN
    g_res(n).ts := ts; g_res(n).antes := antes; g_res(n).depois := depois;
    g_res(n).ganho := ganho; g_res(n).reorg := reorg; g_res(n).erros := erros;
    g_res(n).estado := SUBSTR(estado, 1, 200);
  END;

  -- ---------------------------------------------------------------------------
  PROCEDURE p(m VARCHAR2) IS BEGIN DBMS_OUTPUT.PUT_LINE(m); END;

  -- Passo atual visivel noutra sessao: V$SESSION.ACTION (onde MODULE = 'SHRINK_TS')
  PROCEDURE passo(a VARCHAR2) IS
  BEGIN
    DBMS_APPLICATION_INFO.SET_MODULE('SHRINK_TS', SUBSTR(a, 1, 64));
  END;

  FUNCTION q(n VARCHAR2) RETURN VARCHAR2 IS BEGIN RETURN '"' || n || '"'; END;

  FUNCTION qc(n VARCHAR2) RETURN VARCHAR2 IS   -- colunas LOB podem ja vir com aspas
  BEGIN RETURN CASE WHEN INSTR(n, '"') > 0 THEN n ELSE q(n) END; END;

  -- Executa (em EXECUTAR) e devolve 0 ou o SQLCODE. p_count = FALSE: erro esperado (nao conta).
  FUNCTION run_rc(s VARCHAR2, p_count BOOLEAN DEFAULT TRUE) RETURN PLS_INTEGER IS
  BEGIN
    p(s || ';');
    IF c_exec THEN
      BEGIN
        EXECUTE IMMEDIATE s;
        v_ok := v_ok + 1;
      EXCEPTION WHEN OTHERS THEN
        IF p_count THEN v_err := v_err + 1; END IF;
        p('   -- ' || CASE WHEN p_count THEN 'ERRO' ELSE 'aviso' END || ': ' || SQLERRM);
        RETURN SQLCODE;
      END;
    END IF;
    RETURN 0;
  END;

  PROCEDURE run(s VARCHAR2) IS
    rc PLS_INTEGER;
  BEGIN
    rc := run_rc(s);
  END;

  -- Pausa (DBMS_SESSION.SLEEP 18c+; DBMS_LOCK.SLEEP em versoes antigas)
  PROCEDURE dormir(seg NUMBER) IS
  BEGIN
    EXECUTE IMMEDIATE 'BEGIN DBMS_SESSION.SLEEP(:1); END;' USING seg;
  EXCEPTION WHEN OTHERS THEN
    BEGIN
      EXECUTE IMMEDIATE 'BEGIN DBMS_LOCK.SLEEP(:1); END;' USING seg;
    EXCEPTION WHEN OTHERS THEN NULL;
    END;
  END;

  PROCEDURE manual(r r_seg, motivo VARCHAR2) IS
  BEGIN
    v_manual := v_manual + 1;
    p('-- MANUAL: ' || r.tipo || ' ' || r.owner || '.' || r.seg ||
      CASE WHEN r.part IS NOT NULL THEN ' (' || r.part || ')' END || ' -> ' || motivo);
  END;

  -- Calcula o HWM (ultimo bloco usado) de cada datafile numa unica leitura
  PROCEDURE load_hwm IS
    l_f t_nlist;
    l_h t_nlist;
    i   PLS_INTEGER;
  BEGIN
    IF g_fast THEN
      EXECUTE IMMEDIATE c_sql_hwm BULK COLLECT INTO l_f, l_h USING v_tsn, g_ts;
    ELSE
      SELECT file_id, MAX(block_id + blocks - 1) BULK COLLECT INTO l_f, l_h
        FROM dba_extents WHERE tablespace_name = g_ts GROUP BY file_id;
    END IF;
    i := g_bytes.FIRST;
    WHILE i IS NOT NULL LOOP g_hwm(i) := c_hdr; i := g_bytes.NEXT(i); END LOOP;
    FOR k IN 1 .. l_f.COUNT LOOP g_hwm(l_f(k)) := l_h(k); END LOOP;

    -- Validacao: o HWM nunca pode ser inferior ao espaco ocupado.
    -- Se for (leitura X$ incompleta), descarta e recalcula com DBA_EXTENTS.
    IF g_fast THEN
      i := g_bytes.FIRST;
      WHILE i IS NOT NULL LOOP
        IF g_hwm(i) < c_hdr + g_used(i) * 0.95 THEN
          p('-- AVISO: HWM via X$KTFBUE inconsistente no file_id ' || i ||
            ' (HWM ' || ROUND(g_hwm(i) * v_bs / 1048576) || ' MB < ocupado ' ||
            ROUND((g_used(i) + c_hdr) * v_bs / 1048576) || ' MB). A recalcular com DBA_EXTENTS...');
          g_fast := FALSE;
          load_hwm;
          RETURN;
        END IF;
        i := g_bytes.NEXT(i);
      END LOOP;
    END IF;
  END;

  -- Resize de um datafile com tentativas: se der ORA-03297 (ha blocos usados acima do valor),
  -- recalcula o HWM e sobe o tamanho pedido ate o Oracle aceitar (ou ate ao tamanho atual).
  PROCEDURE resize_file(i PLS_INTEGER, p_mb NUMBER) IS
    v_mb  NUMBER := p_mb;
    v_cur NUMBER := FLOOR(g_bytes(i) / 1048576);
    rc    PLS_INTEGER;
    n     PLS_INTEGER := 0;
  BEGIN
    LOOP
      n := n + 1;
      rc := run_rc('ALTER DATABASE DATAFILE ''' || g_name(i) || ''' RESIZE ' || v_mb || 'M', FALSE);
      IF rc = 0 THEN
        IF c_exec THEN
          IF n > 1 THEN p('   -- OK na tentativa ' || n || ': ' || v_mb || ' MB'); END IF;
          g_bytes(i) := v_mb * 1048576;
        END IF;
        RETURN;
      ELSIF rc <> -3297 OR n >= 6 THEN
        v_err := v_err + 1;
        p('   -- ERRO: resize nao foi possivel (' || n || ' tentativa(s)).');
        RETURN;
      END IF;
      -- ORA-03297: na 1.a vez recalcula o HWM (pode haver extents que a 1.a leitura nao viu);
      -- depois sobe 10% + 64 MB de cada vez
      IF n = 1 THEN
        load_hwm;
        v_mb := GREATEST(v_mb + 16, CEIL((g_hwm(i) + 1) * v_bs / 1048576) + 16);
      ELSE
        v_mb := CEIL(v_mb * 1.10) + 64;
      END IF;
      IF v_mb >= v_cur THEN
        v_err := v_err + 1;
        p('   -- ERRO: ha dados ate perto do fim do ficheiro; nao e possivel reduzir mais.');
        RETURN;
      END IF;
    END LOOP;
  END;

  -- Resize de cada datafile ate ao HWM.
  -- p_refresh = FALSE usa o HWM ja calculado; TRUE recalcula (apos a reorganizacao).
  PROCEDURE resize_hwm(p_refresh BOOLEAN) IS
    v_new NUMBER;
    i     PLS_INTEGER;
  BEGIN
    IF p_refresh THEN
      load_hwm;
    END IF;
    i := g_bytes.FIRST;
    WHILE i IS NOT NULL LOOP
      v_new := CEIL((GREATEST(NVL(g_hwm(i), c_hdr), c_hdr) + 1) * v_bs / 1048576);
      -- protecao: nunca pedir um tamanho inferior ao espaco ocupado
      v_new := GREATEST(v_new, CEIL((c_hdr + g_used(i) + 1) * v_bs / 1048576));
      IF g_bytes(i) / 1048576 - v_new >= 1 THEN
        resize_file(i, v_new);
      END IF;
      i := g_bytes.NEXT(i);
    END LOOP;
  END;

  -- O segmento ainda tem extents acima do alvo? (evita trabalho repetido)
  FUNCTION acima_alvo(o VARCHAR2, s VARCHAR2, pt VARCHAR2) RETURN BOOLEAN IS
    l_f t_nlist;
    l_m t_nlist;
  BEGIN
    IF g_fast THEN
      EXECUTE IMMEDIATE c_sql_seg_hwm BULK COLLECT INTO l_f, l_m
        USING o, s, pt, g_ts, v_tsn, g_ts;
    ELSE
      SELECT file_id, MAX(block_id + blocks - 1) BULK COLLECT INTO l_f, l_m
        FROM dba_extents
       WHERE owner = o AND segment_name = s
         AND NVL(partition_name, '~') = NVL(pt, '~')
         AND tablespace_name = g_ts
       GROUP BY file_id;
    END IF;
    FOR k IN 1 .. l_f.COUNT LOOP
      IF g_tgt.EXISTS(l_f(k)) AND l_m(k) > g_tgt(l_f(k)) THEN RETURN TRUE; END IF;
    END LOOP;
    RETURN FALSE;
  END;

  -- Segmentos TEMPORARY no tablespace (restos de MOVE/REBUILD/CREATE INDEX interrompidos ou
  -- ainda por limpar pelo SMON). Bloqueiam o resize e o DBA_EXTENTS pode nao os mostrar.
  FUNCTION n_temp RETURN PLS_INTEGER IS
    n PLS_INTEGER;
  BEGIN
    SELECT COUNT(*) INTO n FROM dba_segments WHERE tablespace_name = g_ts AND segment_type = 'TEMPORARY';
    RETURN n;
  END;

  -- Ha MOVE/REBUILD/CREATE INDEX a correr noutra sessao? (V$SESSION.COMMAND 9, 11, 15)
  FUNCTION ddl_ativo RETURN PLS_INTEGER IS
    n PLS_INTEGER;
  BEGIN
    EXECUTE IMMEDIATE
      'SELECT COUNT(*) FROM gv$session
        WHERE status = ''ACTIVE'' AND command IN (9, 11, 15)
          AND NOT (sid = SYS_CONTEXT(''USERENV'', ''SID'')
                   AND inst_id = SYS_CONTEXT(''USERENV'', ''INSTANCE''))' INTO n;
    RETURN n;
  EXCEPTION WHEN OTHERS THEN
    RETURN -1;   -- sem acesso a GV$SESSION: nao arriscar
  END;

  -- Limpa os segmentos TEMPORARY (evento DROP_SEGMENTS = o que o SMON faria mais tarde)
  PROCEDURE limpar_temp IS
    n PLS_INTEGER := n_temp;
    d PLS_INTEGER;
    v_evt VARCHAR2(200);
  BEGIN
    v_tmp_left := 0;
    IF n = 0 THEN RETURN; END IF;
    p('-- ' || n || ' segmento(s) TEMPORARY no tablespace (restos de MOVE/REBUILD por limpar):');
    FOR r IN (SELECT owner, segment_name, ROUND(bytes / 1048576, 1) mb
                FROM dba_segments WHERE tablespace_name = g_ts AND segment_type = 'TEMPORARY'
               ORDER BY header_block DESC) LOOP
      p('--   ' || r.owner || '.' || r.segment_name || ' (' || r.mb || ' MB)');
    END LOOP;
    v_evt := 'ALTER SESSION SET EVENTS ''immediate trace name DROP_SEGMENTS level ' || (v_tsn + 1) || '''';
    IF NOT c_exec THEN
      p(v_evt || ';   -- em EXECUTAR e feito automaticamente se nao houver DDL a correr');
      v_tmp_left := n;
      RETURN;
    END IF;
    d := ddl_ativo;
    IF d <> 0 THEN
      p('-- Limpeza NAO feita: ' || CASE WHEN d < 0 THEN 'sem acesso a GV$SESSION'
                                    ELSE d || ' sessao(oes) com MOVE/REBUILD/CREATE INDEX a correr' END ||
        '. Mais tarde: ' || v_evt || ';');
      v_tmp_left := n;
      RETURN;
    END IF;
    run(v_evt);
    FOR k IN 1 .. 12 LOOP          -- espera ate 60 s pela limpeza
      EXIT WHEN n_temp = 0;
      dormir(5);
    END LOOP;
    v_tmp_left := n_temp;
    p('-- Segmentos TEMPORARY restantes: ' || v_tmp_left ||
      CASE WHEN v_tmp_left > 0 THEN ' (o SMON pode demorar; correr de novo mais tarde)' END);
  END;

  -- Algum datafile continua com o HWM claramente acima do alvo (>5% e >= ganho minimo)?
  FUNCTION hwm_alto RETURN BOOLEAN IS
    i PLS_INTEGER := g_bytes.FIRST;
  BEGIN
    WHILE i IS NOT NULL LOOP
      IF g_hwm(i) > g_tgt(i) * 1.05
         AND (g_hwm(i) - g_tgt(i)) * v_bs / 1048576 >= GREATEST(c_min, 1) THEN
        RETURN TRUE;
      END IF;
      i := g_bytes.NEXT(i);
    END LOOP;
    RETURN FALSE;
  END;

  -- Lista os segmentos com extents acima do alvo (g_seg)
  PROCEDURE recolher_candidatos IS
  BEGIN
    IF g_fast THEN
      EXECUTE IMMEDIATE c_sql_cand BULK COLLECT INTO g_seg
        USING g_ts, c_hdr, c_hdr, c_margem, g_ts, g_ts, v_tsn;
    ELSE
      SELECT e.owner, e.segment_name, e.partition_name, e.segment_type,
             ROUND(SUM(e.bytes) / 1048576, 1)
        BULK COLLECT INTO g_seg
        FROM dba_extents e
        JOIN (SELECT d.file_id,
                     c_hdr + CEIL(GREATEST(d.blocks - c_hdr - NVL(fs.free_blk, 0), 0) * (1 + c_margem)) tgt
                FROM dba_data_files d
                LEFT JOIN (SELECT file_id, SUM(blocks) free_blk
                             FROM dba_free_space WHERE tablespace_name = g_ts
                            GROUP BY file_id) fs ON fs.file_id = d.file_id
               WHERE d.tablespace_name = g_ts) u ON u.file_id = e.file_id
       WHERE e.tablespace_name = g_ts
         AND e.segment_type NOT IN ('LOBINDEX', 'TEMPORARY')  -- LOBINDEX move-se com o LOB
         AND e.segment_name NOT LIKE 'BIN$%'
       GROUP BY e.owner, e.segment_name, e.partition_name, e.segment_type
      HAVING MAX(CASE WHEN e.block_id + e.blocks - 1 > u.tgt THEN 1 ELSE 0 END) = 1
       ORDER BY CASE WHEN e.segment_type LIKE 'INDEX%' THEN 2 ELSE 1 END,
                MAX(e.block_id) DESC;
    END IF;
  END;

  -- Gera/executa o comando de reorganizacao adequado ao tipo de segmento
  PROCEDURE reorg(r r_seg) IS
    v_onl VARCHAR2(20)  := CASE WHEN c_online THEN ' ONLINE' END;
    v_upd VARCHAR2(20)  := CASE WHEN c_online THEN ' ONLINE' ELSE ' UPDATE INDEXES' END;
    v_obj VARCHAR2(300) := q(r.owner) || '.' || q(r.seg);
    v_tsq VARCHAR2(140) := q(g_ts);
    v_a   VARCHAR2(128);
    v_b   VARCHAR2(128);
    v_c   VARCHAR2(4000);
  BEGIN
    IF r.owner IN ('SYS', 'SYSTEM') THEN
      manual(r, 'objeto de SYS/SYSTEM'); RETURN;
    END IF;
    IF c_exec AND NOT acima_alvo(r.owner, r.seg, r.part) THEN
      p('-- ignorado (ja abaixo do alvo): ' || v_obj || ' ' || r.part); RETURN;
    END IF;

    CASE r.tipo
      WHEN 'TABLE' THEN
        SELECT iot_type, iot_name INTO v_a, v_b
          FROM dba_tables WHERE owner = r.owner AND table_name = r.seg;
        IF v_a = 'IOT_OVERFLOW' THEN
          run('ALTER TABLE ' || q(r.owner) || '.' || q(v_b) || ' MOVE' || v_onl ||
              ' OVERFLOW TABLESPACE ' || v_tsq);
        ELSE
          run('ALTER TABLE ' || v_obj || ' MOVE' || v_onl || ' TABLESPACE ' || v_tsq ||
              CASE WHEN NOT c_online THEN ' UPDATE INDEXES' END);
        END IF;

      WHEN 'TABLE PARTITION' THEN
        run('ALTER TABLE ' || v_obj || ' MOVE PARTITION ' || q(r.part) ||
            ' TABLESPACE ' || v_tsq || v_upd);

      WHEN 'TABLE SUBPARTITION' THEN
        run('ALTER TABLE ' || v_obj || ' MOVE SUBPARTITION ' || q(r.part) ||
            ' TABLESPACE ' || v_tsq || v_upd);

      WHEN 'INDEX' THEN
        SELECT index_type, table_name INTO v_a, v_b
          FROM dba_indexes WHERE owner = r.owner AND index_name = r.seg;
        IF v_a = 'IOT - TOP' THEN
          run('ALTER TABLE ' || q(r.owner) || '.' || q(v_b) || ' MOVE' || v_onl ||
              ' TABLESPACE ' || v_tsq);
        ELSE
          run('ALTER INDEX ' || v_obj || ' REBUILD TABLESPACE ' || v_tsq || v_onl);
        END IF;

      WHEN 'INDEX PARTITION' THEN
        SELECT index_type, table_name INTO v_a, v_b
          FROM dba_indexes WHERE owner = r.owner AND index_name = r.seg;
        IF v_a = 'IOT - TOP' THEN
          run('ALTER TABLE ' || q(r.owner) || '.' || q(v_b) || ' MOVE PARTITION ' ||
              q(r.part) || ' TABLESPACE ' || v_tsq || v_upd);
        ELSE
          run('ALTER INDEX ' || v_obj || ' REBUILD PARTITION ' || q(r.part) ||
              ' TABLESPACE ' || v_tsq || v_onl);
        END IF;

      WHEN 'INDEX SUBPARTITION' THEN
        run('ALTER INDEX ' || v_obj || ' REBUILD SUBPARTITION ' || q(r.part) ||
            ' TABLESPACE ' || v_tsq || v_onl);

      WHEN 'LOBSEGMENT' THEN
        SELECT table_name, column_name INTO v_b, v_c
          FROM dba_lobs WHERE owner = r.owner AND segment_name = r.seg;
        run('ALTER TABLE ' || q(r.owner) || '.' || q(v_b) || ' MOVE' || v_onl ||
            ' LOB(' || qc(v_c) || ') STORE AS (TABLESPACE ' || v_tsq || ')' ||
            CASE WHEN NOT c_online THEN ' UPDATE INDEXES' END);

      WHEN 'LOB PARTITION' THEN
        SELECT table_name, column_name, partition_name INTO v_b, v_c, v_a
          FROM dba_lob_partitions
         WHERE table_owner = r.owner AND lob_name = r.seg AND lob_partition_name = r.part;
        run('ALTER TABLE ' || q(r.owner) || '.' || q(v_b) || ' MOVE PARTITION ' || q(v_a) ||
            ' LOB(' || qc(v_c) || ') STORE AS (TABLESPACE ' || v_tsq || ')' || v_upd);

      ELSE
        manual(r, 'tipo de segmento nao tratado automaticamente');
    END CASE;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN manual(r, 'metadados nao encontrados');
  END;

-- =============================================================================
  PROCEDURE listar_e_mover(p_pass PLS_INTEGER) IS
  BEGIN
    p(RPAD('TIPO', 20) || RPAD('OWNER.SEGMENTO', 62) || RPAD('PARTICAO', 32) || LPAD('MB', 10));
    p(RPAD('-', 124, '-'));
    FOR i IN 1 .. g_seg.COUNT LOOP
      p(RPAD(g_seg(i).tipo, 20) || RPAD(g_seg(i).owner || '.' || g_seg(i).seg, 62) ||
        RPAD(NVL(g_seg(i).part, '-'), 32) || LPAD(g_seg(i).mb, 10));
    END LOOP;
    p('-- ' || g_seg.COUNT || ' segmento(s) a reorganizar');
    p('');
    p('--- Comandos de reorganizacao (passagem ' || p_pass || ')');
    FOR i IN 1 .. g_seg.COUNT LOOP
      passo(g_ts || ' 5 reorg p' || p_pass || ' ' || i || '/' || g_seg.COUNT || ' ' || g_seg(i).seg);
      reorg(g_seg(i));
    END LOOP;
    p('');
  END;

-- Processa um tablespace (g_ts)
PROCEDURE processa IS
BEGIN
  -- ---------- Reiniciar estado ----------
  v_direto := 0; v_potencial := 0; v_rb := 0; v_ok := 0; v_err := 0; v_manual := 0;
  v_reorg := FALSE; g_fast := FALSE; v_ini := NULL; v_fim := NULL; v_tmp_left := 0;
  g_tgt.DELETE; g_hwm.DELETE; g_used.DELETE; g_bytes.DELETE; g_name.DELETE;
  g_seg := t_seg();

  p('');
  p('#############################################################################');
  p('#### TABLESPACE ' || g_ts);
  p('#############################################################################');

  -- ---------- Validacoes ----------
  BEGIN
    SELECT block_size, contents, status INTO v_bs, v_contents, v_status
      FROM dba_tablespaces WHERE tablespace_name = g_ts;
  EXCEPTION WHEN NO_DATA_FOUND THEN
    RAISE_APPLICATION_ERROR(-20001, 'Tablespace ' || g_ts || ' nao existe no contentor ' ||
      SYS_CONTEXT('USERENV', 'CON_NAME') || '. Indique a PDB correta no primeiro prompt.');
  END;
  IF g_ts IN ('SYSTEM', 'SYSAUX') OR v_contents <> 'PERMANENT' THEN
    RAISE_APPLICATION_ERROR(-20002, 'Tablespace nao suportado (SYSTEM/SYSAUX/UNDO/TEMP).');
  END IF;
  IF v_status <> 'ONLINE' THEN
    RAISE_APPLICATION_ERROR(-20003, 'Tablespace tem de estar ONLINE (estado: ' || v_status || ').');
  END IF;

  SELECT SUM(bytes) INTO v_ini FROM dba_data_files WHERE tablespace_name = g_ts;
  SELECT ts# INTO v_tsn FROM v$tablespace WHERE name = g_ts;

  passo(g_ts || ' 1 deteccao X$KTFBUE');
  -- ---------- Metodo de leitura dos extents ----------
  -- X$KTFBUE (so SYS) e muito mais rapido que DBA_EXTENTS; se nao houver acesso, usa DBA_EXTENTS.
  DECLARE n NUMBER;
  BEGIN
    EXECUTE IMMEDIATE 'SELECT COUNT(*) FROM sys.x$ktfbue WHERE ktfbuesegtsn = :1 AND ROWNUM = 1'
      INTO n USING v_tsn;
    IF n = 0 THEN RAISE NO_DATA_FOUND; END IF;  -- sem extents visiveis: usar DBA_EXTENTS
    g_fast := TRUE;
  EXCEPTION WHEN OTHERS THEN
    g_fast := FALSE;
  END;

  -- ---------- Datafiles: tamanho e ocupado (DBA_FREE_SPACE, rapido) ----------
  FOR f IN (SELECT d.file_id, d.file_name, d.bytes,
                   GREATEST(d.blocks - c_hdr - NVL(fs.free_blk, 0), 0) used
              FROM dba_data_files d
              LEFT JOIN (SELECT file_id, SUM(blocks) free_blk
                           FROM dba_free_space WHERE tablespace_name = g_ts
                          GROUP BY file_id) fs ON fs.file_id = d.file_id
             WHERE d.tablespace_name = g_ts) LOOP
    g_tgt(f.file_id)   := c_hdr + CEIL(f.used * (1 + c_margem));
    g_used(f.file_id)  := f.used;
    g_bytes(f.file_id) := f.bytes;
    g_name(f.file_id)  := f.file_name;
  END LOOP;

  -- ---------- HWM e ganhos ----------
  v_t0 := DBMS_UTILITY.GET_TIME;
  passo(g_ts || ' 2 calculo HWM');
  load_hwm;

  p('===== 2b. HWM por datafile (metodo: ' ||
    CASE WHEN g_fast THEN 'X$KTFBUE' ELSE 'DBA_EXTENTS' END || ') =====');
  p(LPAD('FILE_ID', 8) || '  ' || RPAD('FICHEIRO', 60) || LPAD('TAMANHO_MB', 12) ||
    LPAD('OCUPADO_MB', 12) || LPAD('HWM_MB', 10) || LPAD('RECUP_JA_MB', 13) || LPAD('ALVO_MB', 10));
  p(RPAD('-', 127, '-'));
  DECLARE i PLS_INTEGER := g_bytes.FIRST;
  BEGIN
    WHILE i IS NOT NULL LOOP
      v_direto    := v_direto    + GREATEST(g_bytes(i) - (g_hwm(i) + 1) * v_bs, 0);
      v_potencial := v_potencial + GREATEST(g_bytes(i) - (LEAST(g_tgt(i), g_hwm(i)) + 1) * v_bs, 0);
      p(LPAD(i, 8) || '  ' || RPAD(SUBSTR(g_name(i), -60), 60) ||
        LPAD(ROUND(g_bytes(i) / 1048576), 12) ||
        LPAD(ROUND((g_used(i) + c_hdr) * v_bs / 1048576), 12) ||
        LPAD(ROUND(g_hwm(i) * v_bs / 1048576), 10) ||
        LPAD(ROUND(GREATEST(g_bytes(i) - (g_hwm(i) + 1) * v_bs, 0) / 1048576), 13) ||
        LPAD(ROUND((LEAST(g_tgt(i), g_hwm(i)) + 1) * v_bs / 1048576), 10));
      i := g_bytes.NEXT(i);
    END LOOP;
  END;
  p('-- calculado em ' || ROUND((DBMS_UTILITY.GET_TIME - v_t0) / 100) || ' s');
  p('');

  passo(g_ts || ' 3 recyclebin');
  SELECT COUNT(*) INTO v_rb FROM dba_recyclebin WHERE ts_name = g_ts;

  p('===== 3. Diagnostico =====');
  p(RPAD('Modo', 42) || ': ' || CASE WHEN c_exec THEN 'EXECUTAR' ELSE 'ANALISE (nada sera alterado)' END);
  p(RPAD('Contentor', 42) || ': ' || SYS_CONTEXT('USERENV', 'CON_NAME'));
  p(RPAD('Tamanho atual', 42) || ': ' || ROUND(v_ini / 1048576) || ' MB');
  p(RPAD('Recuperavel ja (resize ate HWM)', 42) || ': ' || ROUND(v_direto / 1048576) || ' MB');
  p(RPAD('Recuperavel apos reorganizacao (estim.)', 42) || ': ' || ROUND(v_potencial / 1048576) ||
    ' MB (margem ' || &p_margem || '%)');
  p(RPAD('Objetos na recyclebin', 42) || ': ' || v_rb);
  p('');

  -- ---------- Recyclebin ----------
  IF v_rb > 0 THEN
    IF c_purge THEN
      p('===== Purge da recyclebin =====');
      run('PURGE TABLESPACE ' || q(g_ts));
    ELSE
      p('-- AVISO: objetos BIN$ podem bloquear o shrink. Considere: PURGE TABLESPACE ' || q(g_ts) || ';');
    END IF;
    p('');
  END IF;

  -- ---------- Shrink direto ----------
  p('===== 4. Shrink direto (resize ate ao HWM) =====');
  -- restos de MOVE/REBUILD anteriores (TEMPORARY) podem estar acima do HWM e bloquear o resize
  IF n_temp > 0 THEN
    limpar_temp;
    IF c_exec THEN load_hwm; END IF;
  END IF;
  IF v_direto >= 1048576 THEN
    resize_hwm(FALSE);
  ELSE
    p('-- Nada a recuperar acima do HWM: shrink direto NAO e possivel.');
  END IF;
  p('');

  -- ---------- Reorganizacao ----------
  IF (v_potencial - v_direto) / 1048576 < c_min THEN
    p('===== 5. Reorganizacao NAO necessaria =====');
    p('-- Ganho adicional estimado (' || ROUND((v_potencial - v_direto) / 1048576) ||
      ' MB) abaixo do minimo definido (' || c_min || ' MB).');
  ELSE
    v_reorg := TRUE;
    p('===== 5. Reorganizacao: segmentos acima do alvo nos datafiles =====');
    passo(g_ts || ' 5 lista de segmentos');
    recolher_candidatos;
    listar_e_mover(1);

    -- 2.a passagem: durante um MOVE ONLINE o segmento antigo e o novo coexistem, e o novo
    -- pode ficar no FIM do ficheiro. Movido outra vez, vai para o espaco libertado no inicio.
    IF c_exec THEN
      limpar_temp;
      load_hwm;
      FOR v_pass IN 2 .. c_passes LOOP
        EXIT WHEN NOT hwm_alto;
        p('===== 5b. HWM continua acima do alvo: passagem ' || v_pass || ' =====');
        passo(g_ts || ' 5 lista de segmentos p' || v_pass);
        recolher_candidatos;
        EXIT WHEN g_seg.COUNT = 0;
        listar_e_mover(v_pass);
        limpar_temp;
        load_hwm;
      END LOOP;
    ELSE
      p('-- Em EXECUTAR: depois do MOVE limpam-se os TEMPORARY e, se o HWM continuar acima do alvo,');
      p('-- e feita uma 2.a passagem (o MOVE ONLINE pode deixar o segmento novo no fim do ficheiro).');
      p('');
    END IF;

    -- ---------- Indices UNUSABLE ----------
    passo(g_ts || ' 6 indices UNUSABLE');
    p('===== 6. Indices UNUSABLE =====');
    IF NOT c_exec THEN
      p('-- (em EXECUTAR esta verificacao e feita apos a reorganizacao; abaixo, os atuais)');
    END IF;
    FOR i IN (SELECT owner, index_name, NULL part
                FROM dba_indexes
               WHERE status = 'UNUSABLE'
                 AND (table_owner, table_name) IN
                     (SELECT owner, segment_name FROM dba_segments WHERE tablespace_name = g_ts)
              UNION ALL
              SELECT index_owner, index_name, partition_name
                FROM dba_ind_partitions
               WHERE status = 'UNUSABLE' AND tablespace_name = g_ts) LOOP
      run('ALTER INDEX ' || q(i.owner) || '.' || q(i.index_name) || ' REBUILD' ||
          CASE WHEN i.part IS NOT NULL THEN ' PARTITION ' || q(i.part) END ||
          CASE WHEN c_online THEN ' ONLINE' END);
    END LOOP;
    p('');

    -- ---------- Shrink final ----------
    passo(g_ts || ' 7 shrink final');
    p('===== 7. Shrink final (apos reorganizacao) =====');
    IF c_exec THEN
      resize_hwm(TRUE);
      IF v_tmp_left > 0 THEN
        p('-- ' || v_tmp_left || ' segmento(s) TEMPORARY ainda por limpar: correr de novo daqui a uns minutos.');
      END IF;
    ELSE
      DECLARE i PLS_INTEGER := g_tgt.FIRST; v_new NUMBER;
      BEGIN
        WHILE i IS NOT NULL LOOP
          v_new := CEIL((LEAST(g_tgt(i), g_hwm(i)) + 1) * v_bs / 1048576);
          IF g_bytes(i) / 1048576 - v_new >= 1 THEN
            p('-- estimativa: ALTER DATABASE DATAFILE ''' || g_name(i) || ''' RESIZE ' || v_new || 'M;');
          END IF;
          i := g_tgt.NEXT(i);
        END LOOP;
      END;
    END IF;
    p('');
  END IF;

  -- ---------- Resumo ----------
  passo(g_ts || ' fim');
  SELECT SUM(bytes) INTO v_fim FROM dba_data_files WHERE tablespace_name = g_ts;
  p('===== Resumo ' || g_ts || ' =====');
  p(RPAD('Modo', 42) || ': ' || CASE WHEN c_exec THEN 'EXECUTAR' ELSE 'ANALISE' END);
  p(RPAD('Reorganizacao necessaria', 42) || ': ' || CASE WHEN v_reorg THEN 'SIM' ELSE 'NAO' END);
  IF c_exec THEN
    p(RPAD('Tamanho antes / depois', 42) || ': ' || ROUND(v_ini / 1048576) || ' MB / ' ||
      ROUND(v_fim / 1048576) || ' MB');
    p(RPAD('Espaco recuperado', 42) || ': ' || ROUND((v_ini - v_fim) / 1048576) || ' MB');
    p(RPAD('Comandos OK / ERRO', 42) || ': ' || v_ok || ' / ' || v_err);
  ELSE
    p(RPAD('Ganho estimado', 42) || ': ' ||
      ROUND(CASE WHEN v_reorg THEN v_potencial ELSE v_direto END / 1048576) || ' MB');
  END IF;
  p(RPAD('Segmentos para tratar manualmente', 42) || ': ' || v_manual);
  IF v_tmp_left > 0 THEN
    p(RPAD('Segmentos TEMPORARY por limpar', 42) || ': ' || v_tmp_left);
  END IF;
  IF c_exec AND v_reorg AND hwm_alto THEN
    p('-- O HWM continua acima do alvo mesmo apos ' || c_passes || ' passagens: repita o script mais tarde');
    p('-- ou mova os objetos do fim do ficheiro via um tablespace intermedio.');
  END IF;

  g_tot_ok  := g_tot_ok  + v_ok;
  g_tot_err := g_tot_err + v_err;
  add_res(g_ts, ROUND(v_ini / 1048576), ROUND(v_fim / 1048576),
          CASE WHEN c_exec THEN ROUND((v_ini - v_fim) / 1048576)
               ELSE ROUND(CASE WHEN v_reorg THEN v_potencial ELSE v_direto END / 1048576) END,
          CASE WHEN v_reorg THEN 'SIM' ELSE 'NAO' END, v_err,
          CASE WHEN v_err > 0 THEN 'COM ERROS (ver log)'
               WHEN v_manual > 0 THEN 'OK, ' || v_manual || ' segmento(s) manuais'
               ELSE 'OK' END);
END processa;

-- =============================================================================
BEGIN
  DBMS_OUTPUT.ENABLE(NULL);

  IF c_todos THEN
    -- Todos os tablespaces permanentes, exceto SYSTEM/SYSAUX (UNDO e TEMP nao sao PERMANENT).
    -- Os que tem menos espaco livre do que o ganho minimo sao ignorados (o ganho nunca
    -- pode ser superior ao espaco livre), poupando o calculo do HWM.
    passo('0 lista de tablespaces');
    FOR r_ts IN (SELECT t.tablespace_name, t.status
                   FROM dba_tablespaces t
                  WHERE t.contents = 'PERMANENT'
                    AND t.tablespace_name NOT IN ('SYSTEM', 'SYSAUX')
                  ORDER BY t.tablespace_name) LOOP
      -- espaco livre calculado um tablespace de cada vez (progresso visivel em V$SESSION)
      IF r_ts.status = 'ONLINE' THEN
        passo(r_ts.tablespace_name || ' 0 espaco livre');
        SELECT ROUND(NVL(SUM(bytes), 0) / 1048576) INTO v_livre
          FROM dba_free_space WHERE tablespace_name = r_ts.tablespace_name;
      END IF;
      IF r_ts.status <> 'ONLINE' THEN
        add_res(r_ts.tablespace_name, NULL, NULL, NULL, '-', 0, 'IGNORADO: estado ' || r_ts.status);
      ELSIF v_livre < c_min THEN
        add_res(r_ts.tablespace_name, NULL, NULL, NULL, '-', 0,
                'IGNORADO: livre ' || v_livre || ' MB < minimo ' || c_min || ' MB');
      ELSE
        g_ts := r_ts.tablespace_name;
        BEGIN
          processa;
        EXCEPTION WHEN OTHERS THEN
          p('-- ERRO no tablespace ' || g_ts || ': ' || SQLERRM);
          add_res(g_ts, NULL, NULL, NULL, '-', 1, 'ERRO: ' || SQLERRM);
          g_tot_err := g_tot_err + 1;
        END;
      END IF;
    END LOOP;
  ELSE
    g_ts := c_sel;
    BEGIN
      processa;
    EXCEPTION WHEN OTHERS THEN
      p('-- ERRO no tablespace ' || g_ts || ': ' || SQLERRM);
      add_res(g_ts, NULL, NULL, NULL, '-', 1, 'ERRO: ' || SQLERRM);
      g_tot_err := g_tot_err + 1;
    END;
  END IF;

  -- ---------- Resumo global ----------
  passo('fim');
  p('');
  p('=============================================================================');
  p('===== RESUMO GLOBAL (' || CASE WHEN c_exec THEN 'EXECUTAR' ELSE 'ANALISE - ganho estimado' END ||
    ') - contentor ' || SYS_CONTEXT('USERENV', 'CON_NAME') || ' =====');
  p(RPAD('TABLESPACE', 30) || LPAD('ANTES_MB', 10) || LPAD('DEPOIS_MB', 11) ||
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
