"""Informacao das bases de dados Oracle (Oracle > Bases de dados): estado, versao, Data Guard, ASM,
jobs, recyclebin, schemas, backups RMAN e Data Pump. So leitura (vistas V$ e DBA_).

Privilegios: SELECT ANY DICTIONARY chega para tudo (ver dboperator abaixo). Cada consulta tem um
comentario /* portal:<parte> */ para se identificar no V$SQL.

Tambem gera o script do utilizador dedicado do portal (DBOPERATOR) e cria-o pela ligacao guardada.
"""
import re
import time

from . import bd

ESTADOS = {  # chave -> (cor, texto)
    "open": ("ok", "Aberta"), "mount": ("warn", "Montada (MOUNT)"), "nomount": ("warn", "Iniciada (NOMOUNT)"),
    "transicao": ("warn", "A abrir ou a fechar"), "fechada": ("bad", "Fechada / indisponível"),
    "erro": ("bad", "Sem ligação"),
}


def _linhas(cur) -> list[dict]:
    cols = [d[0].lower() for d in cur.description]
    out = []
    for r in cur:
        d = {}
        for c, v in zip(cols, r):
            d[c] = v.isoformat(" ", "seconds") if hasattr(v, "isoformat") else (v.read() if hasattr(v, "read") else v)
        out.append(d)
    return out


def _q(conn, sql: str, params=None, tolerar: bool = False) -> list[dict]:
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            return _linhas(cur)
    except Exception:  # noqa: BLE001 - vista inexistente na versao, sem privilegio, ...
        if tolerar:
            return []
        raise


def _ligar(db: dict, password: str):
    return bd.connect(db, password)


def estado_de_erro(msg: str) -> str:
    m = msg.upper()
    if "ORA-01033" in m or "ORA-01089" in m or "ORA-01090" in m:
        return "transicao"
    if "ORA-01219" in m or "ORA-01507" in m:
        return "mount"
    if any(c in m for c in ("ORA-01034", "ORA-27101", "ORA-12514", "ORA-12528", "ORA-12526")):
        return "fechada"
    return "erro"


# ------------------------------------------------------------------ resumo (tabela)
SQL_RESUMO = """/* portal:resumo */
SELECT i.instance_name, i.host_name, i.version, i.status AS instance_status, i.startup_time,
       d.name AS db_name, d.db_unique_name, d.open_mode, d.database_role, d.protection_mode,
       d.dataguard_broker, d.log_mode, d.flashback_on, d.force_logging, d.platform_name,
       (SELECT banner FROM v$version WHERE ROWNUM = 1) AS banner,
       SYS_CONTEXT('USERENV', 'CON_NAME') AS con_name, SYS_CONTEXT('USERENV', 'SERVICE_NAME') AS service_name,
       (SELECT COUNT(*) FROM v$archive_dest WHERE target = 'STANDBY' AND status = 'VALID') AS dest_standby
FROM v$instance i, v$database d"""


def resumo(db: dict, password: str) -> dict:
    """Uma linha da tabela: estado (com cor), versao, SID, service, Data Guard, ..."""
    t0 = time.time()
    try:
        conn = _ligar(db, password)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).splitlines()[0][:300]
        est = estado_de_erro(msg)
        return {"ok": False, "estado": est, "cor": ESTADOS[est][0], "estado_txt": f"{ESTADOS[est][1]}: {msg}", "erro": msg}
    try:
        r = _q(conn, SQL_RESUMO)[0]
        servicos = [x["name"] for x in _q(conn, "/* portal:servicos */ SELECT name FROM v$services "
                                                "WHERE name NOT LIKE 'SYS$%' ORDER BY name", tolerar=True)]
        cdb = _q(conn, "/* portal:cdb */ SELECT cdb FROM v$database", tolerar=True)
    finally:
        conn.close()
    st, om = (r.get("instance_status") or "").upper(), (r.get("open_mode") or "").upper()
    est = "open" if st == "OPEN" else "mount" if st == "MOUNTED" else "nomount" if st == "STARTED" else "transicao"
    papel = r.get("database_role") or ""
    dg = papel != "PRIMARY" or int(r.get("dest_standby") or 0) > 0 or (r.get("dataguard_broker") or "") == "ENABLED"
    texto = ESTADOS[est][1] + (f" ({om})" if om else "")
    return {"ok": True, "estado": est, "cor": ESTADOS[est][0], "estado_txt": texto, "open_mode": om,
            "instancia": r.get("instance_name"), "host": r.get("host_name"), "versao": r.get("version"),
            "banner": r.get("banner"), "db_name": r.get("db_name"), "db_unique_name": r.get("db_unique_name"),
            "service_name": r.get("service_name"), "servicos": servicos, "contentor": r.get("con_name"),
            "cdb": (cdb[0]["cdb"] if cdb else "NO") == "YES", "papel": papel, "dataguard": dg,
            "protecao": r.get("protection_mode"), "broker": r.get("dataguard_broker"), "log_mode": r.get("log_mode"),
            "flashback": r.get("flashback_on"), "force_logging": r.get("force_logging"),
            "plataforma": r.get("platform_name"), "arranque": r.get("startup_time"),
            "ms": int((time.time() - t0) * 1000)}


# ------------------------------------------------------------------ detalhe (janela)
PARTES = ("geral", "asm", "dataguard", "jobs", "recyclebin", "schemas", "rman", "datapump")


def detalhe(db: dict, password: str, parte: str, extra: dict | None = None) -> dict:
    if parte not in PARTES and parte != "recyclebin_owner":
        raise ValueError("Parte desconhecida.")
    conn = _ligar(db, password)
    try:
        return globals()[f"_d_{parte}"](conn, extra or {})
    finally:
        conn.close()


def _d_geral(conn, _x):
    tam = _q(conn, """/* portal:tamanho */
        SELECT (SELECT ROUND(SUM(bytes) / 1048576) FROM dba_data_files) AS dados_mb,
               (SELECT ROUND(NVL(SUM(bytes), 0) / 1048576) FROM dba_temp_files) AS temp_mb,
               (SELECT ROUND(SUM(bytes) / 1048576) FROM dba_segments) AS segmentos_mb,
               (SELECT COUNT(*) FROM dba_tablespaces) AS tablespaces,
               (SELECT COUNT(*) FROM v$session WHERE type = 'USER') AS sessoes,
               (SELECT ROUND(SUM(value) / 1048576) FROM v$sga) AS sga_mb,
               (SELECT ROUND(value / 1048576) FROM v$pgastat WHERE name = 'total PGA allocated') AS pga_mb,
               (SELECT value FROM nls_database_parameters WHERE parameter = 'NLS_CHARACTERSET') AS charset,
               (SELECT created FROM v$database) AS criada
        FROM dual""", tolerar=True)
    fra = _q(conn, """/* portal:fra */ SELECT name, ROUND(space_limit / 1048576) AS limite_mb,
        ROUND(space_used / 1048576) AS usado_mb, ROUND(space_reclaimable / 1048576) AS recuperavel_mb
        FROM v$recovery_file_dest""", tolerar=True)
    redo = _q(conn, """/* portal:redo */ SELECT COUNT(*) AS grupos, MAX(members) AS membros, ROUND(MAX(bytes) / 1048576) AS tamanho_mb
        FROM v$log""", tolerar=True)
    ts = _q(conn, """/* portal:ts */ SELECT m.tablespace_name, ROUND(m.used_percent, 1) AS pct
        FROM dba_tablespace_usage_metrics m ORDER BY m.used_percent DESC FETCH FIRST 5 ROWS ONLY""", tolerar=True)
    return {"tamanho": tam[0] if tam else {}, "fra": fra[0] if fra else None, "redo": redo[0] if redo else None, "ts": ts}


def _d_asm(conn, _x):
    dgs = _q(conn, """/* portal:asm */ SELECT name, state, type, total_mb, free_mb, usable_file_mb, offline_disks,
        ROUND((total_mb - free_mb) * 100 / NULLIF(total_mb, 0), 1) AS pct_usado
        FROM v$asm_diskgroup ORDER BY name""", tolerar=True)
    return {"diskgroups": dgs}


def _d_dataguard(conn, _x):
    dest = _q(conn, """/* portal:dg_dest */ SELECT dest_id, dest_name, status, type, database_mode, recovery_mode,
        protection_mode, destination, gap_status, error
        FROM v$archive_dest_status WHERE status <> 'INACTIVE' AND destination IS NOT NULL ORDER BY dest_id""", tolerar=True)
    stats = _q(conn, """/* portal:dg_stats */ SELECT name, value, unit, time_computed FROM v$dataguard_stats""", tolerar=True)
    db = _q(conn, """/* portal:dg_db */ SELECT database_role, protection_mode, protection_level, switchover_status,
        dataguard_broker, fs_failover_status FROM v$database""", tolerar=True)
    return {"destinos": dest, "estatisticas": stats, "bd": db[0] if db else {}}


def _d_jobs(conn, _x):
    jobs = _q(conn, """/* portal:jobs */ SELECT owner, job_name, enabled, state, last_start_date, next_run_date,
        run_count, failure_count, repeat_interval
        FROM dba_scheduler_jobs
        WHERE owner NOT IN ('SYS', 'ORACLE_OCM', 'EXFSYS', 'XDB', 'AUDSYS', 'GSMADMIN_INTERNAL', 'DBSNMP', 'APEX_PUBLIC_USER')
        ORDER BY owner, job_name""", tolerar=True)
    falhas = _q(conn, """/* portal:jobs_falhas */ SELECT owner, job_name, status, actual_start_date, error#, additional_info
        FROM dba_scheduler_job_run_details
        WHERE status <> 'SUCCEEDED' AND log_date > SYSDATE - 7 ORDER BY log_date DESC FETCH FIRST 30 ROWS ONLY""", tolerar=True)
    antigos = _q(conn, """/* portal:dba_jobs */ SELECT job, log_user, what, broken, failures, last_date, next_date
        FROM dba_jobs ORDER BY job""", tolerar=True)
    return {"jobs": jobs, "falhas": falhas, "dba_jobs": antigos}


def _d_recyclebin(conn, _x):
    por_dono = _q(conn, """/* portal:recyclebin */ SELECT r.owner, COUNT(*) AS objetos,
        ROUND(SUM(r.space * NVL(t.block_size, 8192)) / 1048576, 1) AS espaco_mb, MIN(r.droptime) AS mais_antigo,
        MAX(r.droptime) AS mais_recente, SUM(CASE WHEN r.type = 'TABLE' THEN 1 ELSE 0 END) AS tabelas,
        COUNT(DISTINCT r.ts_name) AS tablespaces
        FROM dba_recyclebin r LEFT JOIN dba_tablespaces t ON t.tablespace_name = r.ts_name
        GROUP BY r.owner ORDER BY espaco_mb DESC""", tolerar=True)
    return {"donos": por_dono, "total_mb": round(sum(float(x["espaco_mb"] or 0) for x in por_dono), 1),
            "total_objetos": sum(int(x["objetos"] or 0) for x in por_dono)}


def _d_recyclebin_owner(conn, x):
    dono = bd.valid_ident(x.get("owner", ""), "Owner")
    objs = _q(conn, """/* portal:recyclebin_owner */ SELECT r.object_name, r.original_name, r.type, r.ts_name, r.droptime,
        ROUND(r.space * NVL(t.block_size, 8192) / 1048576, 2) AS espaco_mb, r.can_purge
        FROM dba_recyclebin r LEFT JOIN dba_tablespaces t ON t.tablespace_name = r.ts_name
        WHERE r.owner = :o ORDER BY r.space DESC FETCH FIRST 300 ROWS ONLY""", {"o": dono})
    return {"owner": dono, "objetos": objs}


def _d_schemas(conn, x):
    linhas = _q(conn, """/* portal:schemas */ SELECT u.username, u.account_status, u.created, u.default_tablespace,
        u.profile, u.last_login, ROUND(NVL(s.mb, 0), 1) AS tamanho_mb, NVL(s.segmentos, 0) AS segmentos
        FROM dba_users u
        LEFT JOIN (SELECT owner, SUM(bytes) / 1048576 AS mb, COUNT(*) AS segmentos FROM dba_segments GROUP BY owner) s
               ON s.owner = u.username
        WHERE u.oracle_maintained = 'N' ORDER BY u.username""", tolerar=True)
    if not linhas:   # 11g: sem ORACLE_MAINTAINED
        linhas = _q(conn, """/* portal:schemas11 */ SELECT u.username, u.account_status, u.created, u.default_tablespace,
            u.profile, NULL AS last_login, ROUND(NVL(s.mb, 0), 1) AS tamanho_mb, NVL(s.segmentos, 0) AS segmentos
            FROM dba_users u
            LEFT JOIN (SELECT owner, SUM(bytes) / 1048576 AS mb, COUNT(*) AS segmentos FROM dba_segments GROUP BY owner) s
                   ON s.owner = u.username
            WHERE u.username NOT IN ('SYS','SYSTEM','OUTLN','DBSNMP','XDB','ANONYMOUS','APPQOSSYS','WMSYS','CTXSYS',
                  'MDSYS','ORDSYS','ORDDATA','OLAPSYS','EXFSYS','SYSMAN','MGMT_VIEW','ORACLE_OCM','DIP','XS$NULL')
            ORDER BY u.username""", tolerar=True)
    portal = {u.upper() for u in x.get("portal", [])}
    for linha in linhas:
        linha["portal"] = linha["username"] in portal
    return {"schemas": linhas}


def _d_rman(conn, _x):
    jobs = _q(conn, """/* portal:rman */ SELECT session_key, input_type, status, start_time, end_time,
        ROUND(input_bytes / 1048576) AS entrada_mb, ROUND(output_bytes / 1048576) AS saida_mb,
        output_device_type, time_taken_display
        FROM v$rman_backup_job_details WHERE start_time > SYSDATE - 30 ORDER BY start_time DESC FETCH FIRST 60 ROWS ONLY""",
               tolerar=True)
    ultimo = {}
    for j in jobs:
        tipo = j.get("input_type") or ""
        if j.get("status", "").startswith("COMPLETED") and tipo not in ultimo:
            ultimo[tipo] = j.get("end_time")
    return {"jobs": jobs, "ultimo": ultimo}


def _d_datapump(conn, _x):
    jobs = _q(conn, """/* portal:datapump */ SELECT owner_name, job_name, operation, job_mode, state, degree, attached_sessions
        FROM dba_datapump_jobs ORDER BY owner_name, job_name""", tolerar=True)
    dirs = _q(conn, """/* portal:directories */ SELECT directory_name, directory_path FROM dba_directories
        WHERE directory_name NOT LIKE 'ORACLE%' AND directory_name NOT IN ('OPATCH_INST_DIR','OPATCH_LOG_DIR','OPATCH_SCRIPT_DIR',
              'XMLDIR','XSDDIR','SDO_DIR_ADMIN','SDO_DIR_WORK','JAVA$JOX$CUJS$DIRECTORY$','DBMS_OPTIM_ADMINDIR','DBMS_OPTIM_LOGDIR')
        ORDER BY directory_name""", tolerar=True)
    return {"jobs": jobs, "diretorios": dirs}


# ------------------------------------------------------------------ utilizador dedicado do portal
GRANTS = [
    ("CREATE SESSION", "ligar à base de dados"),
    ("SELECT ANY DICTIONARY", "ler as vistas V$ e DBA_ (tablespaces, estado, ASM, Data Guard, jobs, recyclebin, schemas, RMAN)"),
    ("ALTER DATABASE", "shrink: RESIZE dos datafiles"),
    ("ALTER ANY TABLE", "shrink: MOVE de tabelas, partições e LOBs"),
    ("ALTER ANY INDEX", "shrink: REBUILD de índices"),
    ("ALTER SYSTEM", "cancelar uma execução (ALTER SYSTEM KILL SESSION)"),
    ("DROP ANY TABLE", "recyclebin: PURGE dos objetos apagados de outros schemas"),
]
NOME_RE = re.compile(r"^(C##)?[A-Z][A-Z0-9_$#]{0,29}$")


def script_dboperator(nome: str = "DBOPERATOR", cdb_root: bool = False, password: str = "<password>",
                      incluir_vista: bool = True) -> list[tuple[str, str]]:
    """[(sql, comentario)] para criar o utilizador do portal com os privilegios minimos."""
    nome = nome.strip().upper()
    if cdb_root and not nome.startswith("C##"):
        nome = "C##" + nome
    if not NOME_RE.match(nome):
        raise ValueError("Nome inválido (letras, números e _; no CDB$ROOT começa por C##).")
    cont = " CONTAINER = ALL" if cdb_root else ""
    pw = password.replace('"', "")
    s = [(f'CREATE USER {nome} IDENTIFIED BY "{pw}"{cont}', "utilizador do portal (sem objetos próprios)"),
         (f"ALTER USER {nome} ACCOUNT UNLOCK", "")]
    s += [(f"GRANT {g} TO {nome}{cont}", porque) for g, porque in GRANTS]
    s.append((f"GRANT EXECUTE ON SYS.DBMS_PIPE TO {nome}{cont}", "shrink: output em direto (DBMS_PIPE)"))
    if incluir_vista:
        s.append(("CREATE OR REPLACE VIEW SYS.X_$KTFBUE AS SELECT * FROM SYS.X$KTFBUE",
                  "shrink: cálculo rápido do HWM (só como SYS / SYSDBA)"))
        s.append((f"GRANT SELECT ON SYS.X_$KTFBUE TO {nome}{cont}", "shrink: cálculo rápido do HWM"))
    return s


def texto_script(linhas: list[tuple[str, str]]) -> str:
    return "\n".join((f"-- {c}\n" if c else "") + f"{sql};" for sql, c in linhas)


def criar_dboperator(db: dict, password_ligacao: str, nome: str, password_novo: str) -> dict:
    """Corre o script pela ligacao guardada (tem de ser SYSDBA ou ter CREATE USER e os GRANT ... WITH ADMIN).
    Devolve {ok, nome, passos: [{sql, ok, erro}]}. CREATE USER falhado (ja existe) nao para os GRANT."""
    if len(password_novo) < 12 or not re.search(r"[A-Za-z]", password_novo) or not re.search(r"\d", password_novo):
        raise ValueError("A password do novo utilizador tem de ter pelo menos 12 caracteres, com letras e números.")
    if '"' in password_novo:
        raise ValueError("A password não pode ter aspas (\").")
    conn = _ligar(db, password_ligacao)
    passos = []
    try:
        cdb = _q(conn, "SELECT SYS_CONTEXT('USERENV', 'CON_NAME') AS c FROM dual")[0]["c"] == "CDB$ROOT"
        linhas = script_dboperator(nome, cdb, password_novo, incluir_vista=db.get("role") == "SYSDBA")
        for sql, _c in linhas:
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                passos.append({"sql": sql.replace(password_novo, "******"), "ok": True, "erro": ""})
            except Exception as exc:  # noqa: BLE001
                passos.append({"sql": sql.replace(password_novo, "******"), "ok": False,
                               "erro": str(exc).splitlines()[0][:300]})
        nome_final = linhas[0][0].split()[2]
    finally:
        conn.close()
    criticos = [p for p in passos if not p["ok"] and not p["sql"].startswith(("CREATE OR REPLACE VIEW", "GRANT SELECT ON SYS.X_$"))
                and "ORA-01920" not in p["erro"]]      # ORA-01920: o utilizador ja existe
    return {"ok": not criticos, "nome": nome_final, "passos": passos}
