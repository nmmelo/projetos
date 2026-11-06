"""Base de dados do portal: SQLite (local), MariaDB/MySQL ou PostgreSQL (local ou remota).

Camada fina sobre a DB-API do Python. Todo o SQL usa "?" como marcador; para
MariaDB e PostgreSQL e convertido para "%s". Nao usar "%" nem "?" literais no SQL.
"""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta

from . import crypto, settings

TABLES = ("settings", "oracle_dbs", "profiles", "users", "jobs", "backups")

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS settings (
        k VARCHAR(100) NOT NULL PRIMARY KEY,
        v TEXT
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS oracle_dbs (
        id {ID},
        name VARCHAR(100) NOT NULL UNIQUE,
        host VARCHAR(255) NOT NULL,
        port INTEGER NOT NULL,
        sid VARCHAR(128),
        service_name VARCHAR(255),
        pdb VARCHAR(128),
        username VARCHAR(128) NOT NULL,
        password_enc TEXT NOT NULL,
        role VARCHAR(10) NOT NULL,
        created_at VARCHAR(19) NOT NULL,
        updated_at VARCHAR(19) NOT NULL
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS profiles (
        id {ID},
        name VARCHAR(100) NOT NULL UNIQUE,
        description VARCHAR(255),
        all_dbs INTEGER NOT NULL DEFAULT 0,
        db_ids TEXT,
        permissions TEXT,
        created_at VARCHAR(19) NOT NULL,
        updated_at VARCHAR(19) NOT NULL
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS users (
        id {ID},
        username VARCHAR(64) NOT NULL UNIQUE,
        name VARCHAR(150),
        email VARCHAR(255),
        password_hash VARCHAR(255) NOT NULL,
        profile_id INTEGER,
        active INTEGER NOT NULL DEFAULT 1,
        created_at VARCHAR(19) NOT NULL,
        updated_at VARCHAR(19) NOT NULL,
        last_login VARCHAR(19)
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS jobs (
        id {ID},
        db_id INTEGER,
        db_name VARCHAR(100),
        params TEXT NOT NULL,
        status VARCHAR(20) NOT NULL,
        started_at VARCHAR(19) NOT NULL,
        finished_at VARCHAR(19),
        current_action VARCHAR(255),
        ora_sid INTEGER,
        ora_serial INTEGER,
        output {LONGTEXT},
        error TEXT,
        created_by VARCHAR(64)
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS backups (
        id {ID},
        filename VARCHAR(255) NOT NULL,
        created_at VARCHAR(19) NOT NULL,
        created_by VARCHAR(64),
        size_bytes INTEGER,
        origin VARCHAR(20),
        note VARCHAR(255)
    ){SUFFIX}""",
]

_DIALECT = {
    "sqlite":     {"ID": "INTEGER PRIMARY KEY AUTOINCREMENT", "LONGTEXT": "TEXT", "SUFFIX": ""},
    "mariadb":    {"ID": "INTEGER AUTO_INCREMENT PRIMARY KEY", "LONGTEXT": "LONGTEXT",
                   "SUFFIX": " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"},
    "postgresql": {"ID": "SERIAL PRIMARY KEY", "LONGTEXT": "TEXT", "SUFFIX": ""},
}


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ago(**kw) -> str:
    return (datetime.now() - timedelta(**kw)).strftime("%Y-%m-%d %H:%M:%S")


class Tx:
    """Uma ligacao/transacao. Linhas devolvidas como dict."""

    def __init__(self, kind: str, conn):
        self.kind, self.conn = kind, conn

    def _sql(self, sql: str) -> str:
        return sql if self.kind == "sqlite" else sql.replace("?", "%s")

    def _exec(self, sql, params=()):
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), tuple(params))
        return cur

    def all(self, sql, params=()) -> list[dict]:
        cur = self._exec(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) if not isinstance(r, dict) else r for r in cur.fetchall()]

    def one(self, sql, params=()) -> dict | None:
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def val(self, sql, params=()):
        row = self.one(sql, params)
        return next(iter(row.values())) if row else None

    def run(self, sql, params=()) -> int:
        return self._exec(sql, params).rowcount

    def insert(self, table: str, data: dict) -> int:
        cols = ", ".join(data)
        marks = ", ".join("?" * len(data))
        sql = f"INSERT INTO {table} ({cols}) VALUES ({marks})"
        if self.kind == "postgresql":
            return self.val(sql + " RETURNING id", data.values())
        return self._exec(sql, data.values()).lastrowid

    def update(self, table: str, row_id: int, data: dict) -> None:
        sets = ", ".join(f"{k} = ?" for k in data)
        self.run(f"UPDATE {table} SET {sets} WHERE id = ?", (*data.values(), row_id))


class Store:
    def __init__(self, cfg: dict):
        self.cfg = dict(cfg)
        self.kind = cfg.get("type", "sqlite")
        if self.kind not in _DIALECT:
            raise ValueError(f"Tipo de base de dados nao suportado: {self.kind}")

    def describe(self) -> str:
        if self.kind == "sqlite":
            return f"SQLite - {self._sqlite_path()}"
        return (f"{settings.STORAGE_TYPES[self.kind]} - {self.cfg.get('user')}@"
                f"{self.cfg.get('host')}:{self.cfg.get('port')}/{self.cfg.get('database')}")

    def _sqlite_path(self) -> str:
        p = self.cfg.get("path") or "portal.sqlite3"
        return p if os.path.isabs(p) else settings.path(p)

    def connect(self):
        if self.kind == "sqlite":
            c = sqlite3.connect(self._sqlite_path(), timeout=30)
            c.execute("PRAGMA journal_mode=WAL")
            return c
        password = crypto.decrypt(self.cfg["password_enc"]) if self.cfg.get("password_enc") else ""
        port = int(self.cfg.get("port") or settings.DEFAULT_PORTS[self.kind])
        if self.kind == "mariadb":
            import pymysql
            from pymysql.constants import CLIENT
            # FOUND_ROWS: o UPDATE devolve as linhas encontradas (e nao so as alteradas),
            # como no SQLite/PostgreSQL - necessario para o "UPDATE ou INSERT" das settings.
            return pymysql.connect(host=self.cfg["host"], port=port, user=self.cfg["user"],
                                   password=password, database=self.cfg["database"],
                                   charset="utf8mb4", connect_timeout=10, autocommit=False,
                                   client_flag=CLIENT.FOUND_ROWS)
        import psycopg
        return psycopg.connect(host=self.cfg["host"], port=port, user=self.cfg["user"],
                               password=password, dbname=self.cfg["database"], connect_timeout=10)

    @contextmanager
    def tx(self):
        conn = self.connect()
        try:
            yield Tx(self.kind, conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        d = _DIALECT[self.kind]
        with self.tx() as t:
            for ddl in _SCHEMA:
                t.run(ddl.replace("{ID}", d["ID"]).replace("{LONGTEXT}", d["LONGTEXT"])
                      .replace("{SUFFIX}", d["SUFFIX"]))
        if self.kind == "sqlite":
            self._migrate_v1()

    def _migrate_v1(self) -> None:
        """Versao anterior do portal: tabela 'databases' -> 'oracle_dbs'."""
        with self.tx() as t:
            old = t.one("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'databases'")
            if old and not t.val("SELECT COUNT(*) FROM oracle_dbs"):
                t.run("""INSERT INTO oracle_dbs (id, name, host, port, sid, service_name, pdb, username,
                                                 password_enc, role, created_at, updated_at)
                         SELECT id, name, host, port, sid, service_name, pdb, username,
                                password_enc, role, created_at, updated_at FROM databases""")

    # --------------------------------------------------------------- copia entre BDs
    def dump(self) -> dict:
        with self.tx() as t:
            return {tb: t.all(f"SELECT * FROM {tb}") for tb in TABLES}

    def load(self, data: dict) -> None:
        """Substitui o conteudo de todas as tabelas (mantendo os ids)."""
        with self.tx() as t:
            for tb in reversed(TABLES):
                t.run(f"DELETE FROM {tb}")
            for tb in TABLES:
                for row in data.get(tb, []):
                    cols = ", ".join(row)
                    t.run(f"INSERT INTO {tb} ({cols}) VALUES ({', '.join('?' * len(row))})", row.values())
            if self.kind == "postgresql":  # acertar as sequencias depois de inserir ids explicitos
                for tb in TABLES:
                    if tb != "settings":
                        t.run(f"SELECT setval(pg_get_serial_sequence('{tb}', 'id'), "
                              f"COALESCE((SELECT MAX(id) FROM {tb}), 0) + 1, false)")


# ------------------------------------------------------------------ instancia ativa
_lock = threading.Lock()
_store: Store | None = None
_mtime = -1.0


def get() -> Store:
    """Store ativo; recarrega se o portal.json mudou (troca de BD feita noutro worker)."""
    global _store, _mtime
    m = settings.storage_mtime()
    if _store is None or m != _mtime:
        with _lock:
            if _store is None or m != _mtime:
                s = Store(settings.storage_config())
                s.init_schema()
                _store, _mtime = s, m
    return _store


def tx():
    return get().tx()


# ------------------------------------------------------------------ settings (chave/valor)
DEFAULT_SETTINGS = {"portal_name": "Oracle · Gestão de espaço", "session_minutes": "480",
                    "backup_keep": "30"}


def get_settings() -> dict:
    with tx() as t:
        rows = t.all("SELECT k, v FROM settings")
    return {**DEFAULT_SETTINGS, **{r["k"]: r["v"] for r in rows}}


def save_settings(values: dict) -> None:
    with tx() as t:
        for k, v in values.items():
            if t.run("UPDATE settings SET v = ? WHERE k = ?", (str(v), k)) == 0:
                t.run("INSERT INTO settings (k, v) VALUES (?, ?)", (k, str(v)))


# ------------------------------------------------------------------ bases de dados Oracle
DB_FIELDS = ("name", "host", "port", "sid", "service_name", "pdb", "username", "role")


def list_dbs() -> list[dict]:
    with tx() as t:
        return t.all("SELECT * FROM oracle_dbs ORDER BY name")


def get_db(db_id) -> dict | None:
    with tx() as t:
        return t.one("SELECT * FROM oracle_dbs WHERE id = ?", (db_id,))


def save_db(data: dict, password_enc: str | None, db_id=None) -> int:
    row = {f: data[f] for f in DB_FIELDS}
    with tx() as t:
        if db_id is None:
            return t.insert("oracle_dbs", {**row, "password_enc": password_enc,
                                           "created_at": now(), "updated_at": now()})
        if password_enc:
            row["password_enc"] = password_enc
        t.update("oracle_dbs", db_id, {**row, "updated_at": now()})
        return db_id


def delete_db(db_id) -> None:
    with tx() as t:
        t.run("DELETE FROM oracle_dbs WHERE id = ?", (db_id,))


# ------------------------------------------------------------------ perfis
def _profile(row):
    if row:
        row["permissions"] = json.loads(row.get("permissions") or "[]")
        row["db_ids"] = json.loads(row.get("db_ids") or "[]")
        row["all_dbs"] = bool(row["all_dbs"])
    return row


def list_profiles() -> list[dict]:
    with tx() as t:
        rows = t.all("""SELECT p.*, (SELECT COUNT(*) FROM users u WHERE u.profile_id = p.id) AS n_users
                        FROM profiles p ORDER BY p.name""")
    return [_profile(r) for r in rows]


def get_profile(pid) -> dict | None:
    with tx() as t:
        return _profile(t.one("SELECT * FROM profiles WHERE id = ?", (pid,)))


def save_profile(data: dict, pid=None) -> int:
    row = {"name": data["name"], "description": data.get("description") or "",
           "all_dbs": 1 if data.get("all_dbs") else 0,
           "db_ids": json.dumps(sorted(int(i) for i in data.get("db_ids", []))),
           "permissions": json.dumps(sorted(data.get("permissions", []))), "updated_at": now()}
    with tx() as t:
        if pid is None:
            return t.insert("profiles", {**row, "created_at": now()})
        t.update("profiles", pid, row)
        return pid


def delete_profile(pid) -> None:
    with tx() as t:
        if t.val("SELECT COUNT(*) FROM users WHERE profile_id = ?", (pid,)):
            raise ValueError("O perfil está atribuído a utilizadores.")
        t.run("DELETE FROM profiles WHERE id = ?", (pid,))


# ------------------------------------------------------------------ utilizadores
def list_users() -> list[dict]:
    with tx() as t:
        return t.all("""SELECT u.id, u.username, u.name, u.email, u.profile_id, u.active,
                               u.created_at, u.last_login, p.name AS profile_name
                        FROM users u LEFT JOIN profiles p ON p.id = u.profile_id
                        ORDER BY u.username""")


def get_user(uid=None, username=None) -> dict | None:
    with tx() as t:
        if uid is not None:
            return t.one("SELECT * FROM users WHERE id = ?", (uid,))
        return t.one("SELECT * FROM users WHERE username = ?", (username,))


def save_user(data: dict, password_hash: str | None, uid=None) -> int:
    row = {"username": data["username"], "name": data.get("name") or "", "email": data.get("email") or "",
           "profile_id": data.get("profile_id"), "active": 1 if data.get("active") else 0,
           "updated_at": now()}
    with tx() as t:
        if uid is None:
            return t.insert("users", {**row, "password_hash": password_hash, "created_at": now()})
        if password_hash:
            row["password_hash"] = password_hash
        t.update("users", uid, row)
        return uid


def update_user_fields(uid, **fields) -> None:
    with tx() as t:
        t.update("users", uid, {**fields, "updated_at": now()})


def delete_user(uid) -> None:
    with tx() as t:
        t.run("DELETE FROM users WHERE id = ?", (uid,))


# ------------------------------------------------------------------ execucoes
def create_job(db: dict, params: dict, user: str) -> int:
    with tx() as t:
        return t.insert("jobs", {"db_id": db["id"], "db_name": db["name"], "params": json.dumps(params),
                                 "status": "EM CURSO", "started_at": now(), "created_by": user})


def update_job(job_id: int, **fields) -> None:
    if fields:
        with tx() as t:
            t.update("jobs", job_id, fields)


def _job(row):
    if row:
        row["params"] = json.loads(row["params"])
    return row


def get_job(job_id) -> dict | None:
    with tx() as t:
        return _job(t.one("SELECT * FROM jobs WHERE id = ?", (job_id,)))


def list_jobs(limit: int = 200) -> list[dict]:
    with tx() as t:
        rows = t.all("""SELECT id, db_id, db_name, params, status, started_at, finished_at,
                               current_action, created_by FROM jobs ORDER BY id DESC LIMIT ?""", (int(limit),))
    return [_job(r) for r in rows]


def interrupt_running_jobs() -> None:
    with tx() as t:
        t.run("UPDATE jobs SET status = 'INTERROMPIDO', finished_at = ? WHERE status = 'EM CURSO'", (now(),))


# ------------------------------------------------------------------ registo de backups
def list_backups() -> list[dict]:
    with tx() as t:
        return t.all("SELECT * FROM backups ORDER BY id DESC")


def get_backup(bid) -> dict | None:
    with tx() as t:
        return t.one("SELECT * FROM backups WHERE id = ?", (bid,))


def add_backup(row: dict) -> int:
    with tx() as t:
        return t.insert("backups", row)


def delete_backup_row(bid) -> None:
    with tx() as t:
        t.run("DELETE FROM backups WHERE id = ?", (bid,))
