"""Ligacao Oracle com python-oracledb (modo thin: nao precisa de Oracle Client)."""
import os
import re

import oracledb

SQL_DIR = os.path.join(os.path.dirname(__file__), "sql")
IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")
TODOS = ("TODOS", "ALL", "*")


class ValidationError(ValueError):
    pass


def valid_ident(value: str, what: str) -> str:
    value = (value or "").strip().upper()
    if not IDENT_RE.match(value):
        raise ValidationError(f"{what} invalido: {value!r}")
    return value


# ------------------------------------------------------------------ ligacao
def make_dsn(cfg: dict) -> str:
    if cfg.get("service_name"):
        return oracledb.makedsn(cfg["host"], int(cfg["port"]), service_name=cfg["service_name"])
    if cfg.get("sid"):
        return oracledb.makedsn(cfg["host"], int(cfg["port"]), sid=cfg["sid"])
    raise ValidationError("Indique o SID ou o Service Name.")


def connect(cfg: dict, password: str) -> oracledb.Connection:
    mode = oracledb.AUTH_MODE_SYSDBA if cfg.get("role") == "SYSDBA" else oracledb.AUTH_MODE_DEFAULT
    conn = oracledb.connect(user=cfg["username"], password=password, dsn=make_dsn(cfg), mode=mode)
    if cfg.get("pdb"):
        pdb = valid_ident(cfg["pdb"], "PDB")
        with conn.cursor() as cur:
            cur.execute(f"ALTER SESSION SET CONTAINER = {pdb}")
    return conn


def test_connection(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT SYS_CONTEXT('USERENV', 'SESSION_USER'),
                   SYS_CONTEXT('USERENV', 'CON_NAME'),
                   SYS_CONTEXT('USERENV', 'DB_NAME')
            FROM dual""")
        user, con, db = cur.fetchone()
    return {"user": user, "container": con, "db": db, "version": conn.version}


def session_id(conn):
    """SID e SERIAL# da sessao (para acompanhar o progresso e permitir cancelar)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT sid, serial# FROM v$session
            WHERE  sid = SYS_CONTEXT('USERENV', 'SID')""")
        row = cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


def current_action(conn, sid, serial):
    with conn.cursor() as cur:
        cur.execute("SELECT action, event FROM v$session WHERE sid = :1 AND serial# = :2", [sid, serial])
        row = cur.fetchone()
    return f"{row[0] or '-'} ({row[1]})" if row else None


def kill_session(conn, sid, serial) -> None:
    with conn.cursor() as cur:
        cur.execute(f"ALTER SYSTEM KILL SESSION '{int(sid)},{int(serial)}' IMMEDIATE")


# ------------------------------------------------------------------ consultas
def list_tablespaces(conn):
    """Tablespaces elegiveis (exclui SYSTEM, SYSAUX, UNDO e TEMP). So dicionario: rapido."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT tablespace_name, status
            FROM   dba_tablespaces
            WHERE  contents = 'PERMANENT'
            AND    tablespace_name NOT IN ('SYSTEM', 'SYSAUX')
            ORDER  BY tablespace_name""")
        return [{"name": r[0], "status": r[1]} for r in cur]


REPORT_FAST = """
SELECT m.tablespace_name,
       ROUND(d.bytes / 1048576)                                  tamanho_mb,
       ROUND(m.used_space * t.block_size / 1048576)              usado_mb,
       ROUND((d.bytes - m.used_space * t.block_size) / 1048576)  livre_mb,
       ROUND(d.max_bytes / 1048576)                              max_mb
FROM   dba_tablespace_usage_metrics m
JOIN   dba_tablespaces t ON t.tablespace_name = m.tablespace_name
JOIN  (SELECT tablespace_name, SUM(bytes) bytes,
              SUM(CASE WHEN autoextensible = 'YES' THEN GREATEST(maxbytes, bytes) ELSE bytes END) max_bytes
       FROM   dba_data_files GROUP BY tablespace_name) d
       ON d.tablespace_name = m.tablespace_name
WHERE  t.contents = 'PERMANENT'
AND    t.tablespace_name NOT IN ('SYSTEM', 'SYSAUX')
ORDER  BY d.bytes DESC"""

REPORT_EXACT = """
WITH df AS (
  SELECT tablespace_name, SUM(bytes) bytes,
         SUM(CASE WHEN autoextensible = 'YES' THEN GREATEST(maxbytes, bytes) ELSE bytes END) max_bytes
  FROM   dba_data_files GROUP BY tablespace_name),
fs AS (
  SELECT tablespace_name, SUM(bytes) free_bytes
  FROM   dba_free_space GROUP BY tablespace_name)
SELECT df.tablespace_name,
       ROUND(df.bytes / 1048576)                            tamanho_mb,
       ROUND((df.bytes - NVL(fs.free_bytes, 0)) / 1048576)  usado_mb,
       ROUND(NVL(fs.free_bytes, 0) / 1048576)               livre_mb,
       ROUND(df.max_bytes / 1048576)                        max_mb
FROM   df
JOIN   dba_tablespaces t ON t.tablespace_name = df.tablespace_name
LEFT   JOIN fs ON fs.tablespace_name = df.tablespace_name
WHERE  t.contents = 'PERMANENT'
AND    t.tablespace_name NOT IN ('SYSTEM', 'SYSAUX')
ORDER  BY df.bytes DESC"""


def tablespace_report(conn, exact: bool = False):
    with conn.cursor() as cur:
        cur.execute(REPORT_EXACT if exact else REPORT_FAST)
        cols = [d[0].lower() for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur]
    for r in rows:
        r["pct_livre"] = round(r["livre_mb"] / r["tamanho_mb"] * 100, 1) if r["tamanho_mb"] else 0
    return rows


# ------------------------------------------------------------------ shrink
def validate_shrink_params(form) -> dict:
    ts = (form.get("tablespace") or "TODOS").strip().upper()
    if ts not in TODOS:
        ts = valid_ident(ts, "Tablespace")
    modo = (form.get("modo") or "ANALISE").upper()
    if modo not in ("ANALISE", "EXECUTAR"):
        raise ValidationError("Modo invalido.")
    # checkbox desmarcada nao e enviada pelo browser: ausente = N
    online = "S" if form.get("online") in ("S", "on", "1", True) else "N"
    purge = "S" if form.get("purge") in ("S", "on", "1", True) else "N"
    try:
        min_ganho = int(form.get("min_ganho") or 100)
        margem = int(form.get("margem") or 10)
    except ValueError as exc:
        raise ValidationError("Ganho minimo e margem tem de ser numeros inteiros.") from exc
    if not 0 <= min_ganho <= 10_000_000 or not 0 <= margem <= 100:
        raise ValidationError("Ganho minimo (0-10000000) ou margem (0-100) fora dos limites.")
    return {"ts": ts, "modo": modo, "online": online, "purge": purge,
            "min_ganho": min_ganho, "margem": margem}


def build_block(p: dict) -> str:
    """Substitui os marcadores &p_* do bloco PL/SQL (valores ja validados: sem aspas nem codigo)."""
    with open(os.path.join(SQL_DIR, "shrink_block.sql")) as fh:
        block = fh.read()
    block = block[block.index("DECLARE"):]          # sem o comentario de cabecalho
    block = block.rstrip().removesuffix("/").rstrip()  # sem o "/" do SQL*Plus, se existir
    for key, val in (("&p_min_ganho", p["min_ganho"]), ("&p_margem", p["margem"]),
                     ("&p_modo", p["modo"]), ("&p_online", p["online"]),
                     ("&p_purge", p["purge"]), ("&p_ts", p["ts"])):
        block = block.replace(key, str(val))
    if "&p_" in block:
        raise RuntimeError("Marcador por substituir no bloco PL/SQL.")
    return block


def read_dbms_output(conn) -> list[str]:
    lines = []
    chunk = 1000
    with conn.cursor() as cur:
        lines_var = cur.arrayvar(str, chunk, 32767)
        num_var = cur.var(int)
        while True:
            num_var.setvalue(0, chunk)
            cur.callproc("dbms_output.get_lines", (lines_var, num_var))
            n = num_var.getvalue()
            lines.extend((l or "") for l in lines_var.getvalue()[:n])
            if n < chunk:
                break
    return lines


def run_shrink(conn, params: dict) -> list[str]:
    block = build_block(params)
    with conn.cursor() as cur:
        cur.callproc("dbms_output.enable", [None])
        cur.execute(block)
    return read_dbms_output(conn)
