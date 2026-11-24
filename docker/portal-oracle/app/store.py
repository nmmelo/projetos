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

from . import aparencia, crypto, modulos, settings

TABLES = ("settings", "oracle_dbs", "profiles", "users", "jobs", "backups", "system_actions",
          "notification_rules", "notifications", "email_queue", "traducoes", "agendamentos",
          "agendamento_execucoes", "oracle_utilizadores", "clientes", "cliente_regras", "cliente_alarmes")

# Definicoes volateis (escritas pelo agente a cada poucos segundos): fora dos backups
VOLATEIS = ("agente_estado", "sistema_metricas", "smtp_estado", "regras_iniciais", "regras_iniciais_2", "i18n_rev")

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
        progress INTEGER,
        ora_sid INTEGER,
        ora_serial INTEGER,
        output {LONGTEXT},
        error TEXT,
        created_by VARCHAR(64),
        runner VARCHAR(20),
        n_erros INTEGER,
        n_avisos INTEGER
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS notification_rules (
        id {ID},
        nome VARCHAR(150) NOT NULL,
        canal VARCHAR(10) NOT NULL,
        evento VARCHAR(60) NOT NULL,
        situacoes TEXT,
        parametro INTEGER,
        destino TEXT,
        ativo INTEGER NOT NULL DEFAULT 1,
        created_at VARCHAR(19),
        updated_at VARCHAR(19)
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS notifications (
        id {ID},
        user_key VARCHAR(64) NOT NULL,
        created_at VARCHAR(19) NOT NULL,
        evento VARCHAR(60),
        nivel VARCHAR(10),
        titulo VARCHAR(255),
        texto TEXT,
        link VARCHAR(255),
        lida INTEGER NOT NULL DEFAULT 0
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS email_queue (
        id {ID},
        created_at VARCHAR(19) NOT NULL,
        tipo VARCHAR(12) NOT NULL,
        evento VARCHAR(60),
        regra VARCHAR(150),
        destinatarios TEXT,
        assunto VARCHAR(255),
        corpo {LONGTEXT},
        status VARCHAR(12) NOT NULL,
        tentativas INTEGER NOT NULL DEFAULT 0,
        proxima VARCHAR(19),
        enviado_em VARCHAR(19),
        erro TEXT,
        log {LONGTEXT},
        pedido_por VARCHAR(64)
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS oracle_utilizadores (
        id {ID},
        db_id INTEGER NOT NULL,
        username VARCHAR(128) NOT NULL,
        finalidade VARCHAR(200),
        created_at VARCHAR(19) NOT NULL,
        created_by VARCHAR(64)
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS clientes (
        id {ID},
        nome VARCHAR(100) NOT NULL UNIQUE,
        codigo VARCHAR(40) NOT NULL UNIQUE,
        endereco VARCHAR(255) NOT NULL,
        descricao VARCHAR(500),
        vm INTEGER NOT NULL DEFAULT 1,
        familia VARCHAR(10),
        ssh_utilizador VARCHAR(64),
        ssh_porta INTEGER,
        token_enc TEXT,
        token_hash VARCHAR(64),
        criado_em VARCHAR(19),
        criado_por VARCHAR(64),
        ultimo_contacto VARCHAR(19),
        intervalo INTEGER,
        versao VARCHAR(20),
        ip_origem VARCHAR(64),
        estado_contacto VARCHAR(10),
        info {LONGTEXT}
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS cliente_regras (
        id {ID},
        codigo VARCHAR(40) NOT NULL UNIQUE,
        nome VARCHAR(100) NOT NULL,
        descricao VARCHAR(500),
        cliente_id INTEGER,
        log_path VARCHAR(500),
        padrao_erro VARCHAR(200),
        padrao_aviso VARCHAR(200),
        ativo INTEGER NOT NULL DEFAULT 1,
        criado_em VARCHAR(19),
        criado_por VARCHAR(64)
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS cliente_alarmes (
        id {ID},
        nome VARCHAR(100) NOT NULL,
        cliente_id INTEGER,
        metrica VARCHAR(20) NOT NULL,
        filtro VARCHAR(500),
        limiar REAL NOT NULL,
        ciclos INTEGER NOT NULL DEFAULT 1,
        severidade VARCHAR(10) NOT NULL DEFAULT 'aviso',
        portal INTEGER NOT NULL DEFAULT 1,
        portal_destino VARCHAR(20),
        email INTEGER NOT NULL DEFAULT 0,
        emails TEXT,
        email_padrao INTEGER NOT NULL DEFAULT 1,
        recuperacao INTEGER NOT NULL DEFAULT 1,
        ativo INTEGER NOT NULL DEFAULT 1,
        criado_em VARCHAR(19),
        criado_por VARCHAR(64)
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS cliente_alarmes_estado (
        id {ID},
        alarme_id INTEGER NOT NULL,
        cliente_id INTEGER NOT NULL,
        item VARCHAR(255) NOT NULL,
        estado VARCHAR(10) NOT NULL,
        contagem INTEGER NOT NULL DEFAULT 0,
        valor REAL,
        desde VARCHAR(19),
        atualizado VARCHAR(19)
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS cliente_eventos (
        id {ID},
        cliente_id INTEGER NOT NULL,
        regra VARCHAR(40),
        quando VARCHAR(19),
        recebido VARCHAR(19) NOT NULL,
        estado VARCHAR(10),
        origem VARCHAR(10),
        mensagem TEXT
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS cliente_comandos (
        id {ID},
        cliente_id INTEGER NOT NULL,
        tipo VARCHAR(20) NOT NULL,
        args VARCHAR(500),
        estado VARCHAR(12) NOT NULL,
        pedido_por VARCHAR(100),
        criado VARCHAR(19) NOT NULL,
        iniciado VARCHAR(19),
        terminado VARCHAR(19),
        output {LONGTEXT}
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS cliente_logs (
        id {ID},
        cliente_id INTEGER NOT NULL,
        pasta VARCHAR(500) NOT NULL,
        ficheiro VARCHAR(255) NOT NULL,
        descricao VARCHAR(255),
        tag VARCHAR(40),
        linhas INTEGER,
        criado VARCHAR(19)
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS cliente_dados (
        id {ID},
        cliente_id INTEGER NOT NULL,
        tipo VARCHAR(20) NOT NULL,
        atualizado VARCHAR(19) NOT NULL,
        dados {LONGTEXT}
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS metricas (
        id {ID},
        ts BIGINT NOT NULL,
        componente VARCHAR(20) NOT NULL,
        cpu REAL,
        mem BIGINT,
        threads REAL,
        procs INTEGER,
        extra VARCHAR(255)
    ){SUFFIX}""",
    "CREATE INDEX IF NOT EXISTS ix_metricas_comp_ts ON metricas (componente, ts)",
    "CREATE INDEX IF NOT EXISTS ix_cliente_dados ON cliente_dados (cliente_id, tipo)",
    "CREATE INDEX IF NOT EXISTS ix_cliente_comandos ON cliente_comandos (cliente_id, estado)",
        """CREATE TABLE IF NOT EXISTS cache_dados (
        chave VARCHAR(200) NOT NULL PRIMARY KEY,
        titulo VARCHAR(200),
        dados {LONGTEXT},
        atualizado VARCHAR(19),
        estado VARCHAR(12),
        inicio VARCHAR(19),
        fim VARCHAR(19),
        duracao REAL,
        erro TEXT,
        utilizador VARCHAR(64),
        link VARCHAR(255)
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS firewall_acoes (
        id {ID},
        quando VARCHAR(19) NOT NULL,
        utilizador VARCHAR(64),
        comando VARCHAR(500),
        ok INTEGER,
        resultado TEXT
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS auth_log (
        id {ID},
        quando VARCHAR(19) NOT NULL,
        utilizador VARCHAR(64),
        metodo VARCHAR(10),
        resultado VARCHAR(10),
        detalhe TEXT,
        ip VARCHAR(64)
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS traducoes (
        id {ID},
        lingua VARCHAR(10) NOT NULL,
        chave VARCHAR(40) NOT NULL,
        origem TEXT NOT NULL,
        texto TEXT,
        updated_at VARCHAR(19),
        updated_by VARCHAR(64),
        UNIQUE (lingua, chave)
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS agendamentos (
        id VARCHAR(80) NOT NULL PRIMARY KEY,
        ativo INTEGER NOT NULL DEFAULT 0,
        freq VARCHAR(10) NOT NULL DEFAULT 'diario',
        hora VARCHAR(5),
        intervalo INTEGER,
        dias VARCHAR(20),
        notificar VARCHAR(10) NOT NULL DEFAULT 'erro',
        parametros TEXT,
        proxima VARCHAR(19),
        ultima_inicio VARCHAR(19),
        ultima_fim VARCHAR(19),
        ultimo_estado VARCHAR(12),
        ultima_msg TEXT,
        updated_by VARCHAR(64)
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS agendamento_execucoes (
        id {ID},
        tarefa VARCHAR(80) NOT NULL,
        inicio VARCHAR(19) NOT NULL,
        fim VARCHAR(19),
        estado VARCHAR(12) NOT NULL,
        msg TEXT,
        origem VARCHAR(64)
    ){SUFFIX}""",
    """CREATE TABLE IF NOT EXISTS system_actions (
        id {ID},
        unit VARCHAR(100) NOT NULL,
        action VARCHAR(20) NOT NULL,
        status VARCHAR(20) NOT NULL,
        requested_by VARCHAR(64),
        requested_at VARCHAR(19) NOT NULL,
        finished_at VARCHAR(19),
        result TEXT
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
        self._migrate_modulos()
        self._add_column("jobs", "progress", "INTEGER")
        self._add_column("jobs", "runner", "VARCHAR(20)")
        self._alargar("cliente_comandos", "args", "VARCHAR(4000)")   # firewall: varias operacoes num pedido
        self._alargar("jobs", "runner", "VARCHAR(20)")         # 'agente_oracle' nao cabia em 10 (PG/MariaDB)
        self._add_column("jobs", "n_erros", "INTEGER")
        self._add_column("jobs", "n_avisos", "INTEGER")
        self._add_column("users", "auth_tipo", "VARCHAR(10)")
        self._add_column("clientes", "zabbix_host", "VARCHAR(128)")
        self._add_column("jobs", "agendado_para", "VARCHAR(19)")
        self._add_column("profiles", "administrador", "INTEGER NOT NULL DEFAULT 0")
        self._add_column("users", "first_name", "VARCHAR(100)")
        self._add_column("users", "last_name", "VARCHAR(100)")
        self._add_column("oracle_dbs", "cliente_id", "INTEGER")          # agente cliente que faz os pedidos
        self._add_column("oracle_dbs", "agente_modo", "VARCHAR(12)")     # nao | auto | obrigatorio
        self._add_column("jobs", "via", "VARCHAR(120)")                  # quem tratou: portal / agente cliente
        self._add_column("jobs", "remoto", "VARCHAR(80)")                # <cliente_id>:<job no agente>

    def _alargar(self, table: str, col: str, ddl: str) -> None:
        """Aumenta o tamanho de uma coluna VARCHAR (no SQLite o tamanho nao conta)."""
        if self.kind == "sqlite":
            return
        sql = (f"ALTER TABLE {table} ALTER COLUMN {col} TYPE {ddl}" if self.kind == "postgresql"
               else f"ALTER TABLE {table} MODIFY {col} {ddl}")
        try:
            with self.tx() as t:
                t.run(sql)
        except Exception:  # noqa: BLE001 - ja alterada por outro processo
            pass

    def _add_column(self, table: str, col: str, ddl: str) -> None:
        """Acrescenta uma coluna a uma tabela ja existente (versoes anteriores do portal)."""
        try:
            with self.tx() as t:
                t.one(f"SELECT {col} FROM {table} WHERE 1 = 0")
            return                                   # ja existe
        except Exception:  # noqa: BLE001
            pass
        try:
            with self.tx() as t:
                t.run(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        except Exception:  # noqa: BLE001 - outro processo (portal/agente) acrescentou-a ao mesmo tempo
            with self.tx() as t:
                t.one(f"SELECT {col} FROM {table} WHERE 1 = 0")

    def _migrate_modulos(self) -> None:
        """Perfis anteriores aos modulos: codigos antigos -> novos e acesso ao modulo Oracle."""
        with self.tx() as t:
            if t.val("SELECT v FROM settings WHERE k = ?", ("perfis_modulos",)):
                return
            for p in t.all("SELECT id, permissions, all_dbs, db_ids FROM profiles"):
                perms = json.loads(p["permissions"] or "[]")
                tinha_bds = bool(p["all_dbs"]) or bool(json.loads(p["db_ids"] or "[]"))
                novas = modulos.normalizar_permissoes(perms, legado=True, tinha_bds=tinha_bds)
                t.run("UPDATE profiles SET permissions = ? WHERE id = ?", (json.dumps(novas), p["id"]))
            t.run("INSERT INTO settings (k, v) VALUES (?, ?)", ("perfis_modulos", "1"))

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
                    if tb not in ("settings", "agendamentos"):
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
DEFAULT_SETTINGS = {**aparencia.DEFAULTS, "session_minutes": "480", "backup_keep": "30"}


def get_settings(blobs: bool = False) -> dict:
    """Definicoes do portal. Sem blobs=True nao le o favicon (lido em cada pagina)."""
    with tx() as t:
        if blobs:
            rows = t.all("SELECT k, v FROM settings")
        else:
            marks = ", ".join("?" * len(aparencia.BLOB_KEYS))
            rows = t.all(f"SELECT k, v FROM settings WHERE k NOT IN ({marks})", aparencia.BLOB_KEYS)
    return {**{k: v for k, v in DEFAULT_SETTINGS.items() if blobs or k not in aparencia.BLOB_KEYS},
            **{r["k"]: r["v"] for r in rows}}


def get_setting(key: str) -> str:
    with tx() as t:
        val = t.val("SELECT v FROM settings WHERE k = ?", (key,))
    return DEFAULT_SETTINGS.get(key, "") if val is None else val


def save_settings(values: dict) -> None:
    with tx() as t:
        for k, v in values.items():
            if t.run("UPDATE settings SET v = ? WHERE k = ?", (str(v), k)) == 0:
                t.run("INSERT INTO settings (k, v) VALUES (?, ?)", (k, str(v)))


# ------------------------------------------------------------------ bases de dados Oracle
DB_FIELDS = ("name", "host", "port", "sid", "service_name", "pdb", "username", "role", "cliente_id", "agente_modo")


def list_dbs() -> list[dict]:
    with tx() as t:
        return t.all("SELECT * FROM oracle_dbs ORDER BY name")


def get_db(db_id) -> dict | None:
    with tx() as t:
        return t.one("SELECT * FROM oracle_dbs WHERE id = ?", (db_id,))


def save_db(data: dict, password_enc: str | None, db_id=None) -> int:
    row = {f: data.get(f) for f in DB_FIELDS}
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
        row["permissions"] = modulos.normalizar_permissoes(json.loads(row.get("permissions") or "[]"))
        row["db_ids"] = json.loads(row.get("db_ids") or "[]")
        row["all_dbs"] = bool(row["all_dbs"])
        row["administrador"] = bool(row.get("administrador"))
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
           "all_dbs": 1 if data.get("all_dbs") else 0, "administrador": 1 if data.get("administrador") else 0,
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
        return t.all("""SELECT u.id, u.username, u.name, u.first_name, u.last_name, u.email, u.profile_id, u.active,
                               u.created_at, u.last_login, u.auth_tipo, p.name AS profile_name
                        FROM users u LEFT JOIN profiles p ON p.id = u.profile_id
                        ORDER BY u.username""")


def get_user(uid=None, username=None) -> dict | None:
    with tx() as t:
        if uid is not None:
            return t.one("SELECT * FROM users WHERE id = ?", (uid,))
        return t.one("SELECT * FROM users WHERE username = ?", (username,))


def save_user(data: dict, password_hash: str | None, uid=None) -> int:
    first, last = (data.get("first_name") or "").strip(), (data.get("last_name") or "").strip()
    nome = " ".join(x for x in (first, last) if x) or (data.get("name") or "")
    row = {"username": data["username"], "name": nome, "email": data.get("email") or "",
           "first_name": first, "last_name": last,
           "profile_id": data.get("profile_id"), "active": 1 if data.get("active") else 0,
           "auth_tipo": data.get("auth_tipo") or "local", "updated_at": now()}
    if row["auth_tipo"] == "ldap" and uid is None and not password_hash:
        password_hash = "!ldap"           # sem password local (autentica no LDAP)
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
JOB_RESUMO = ("id, db_id, db_name, params, status, started_at, finished_at, current_action, progress, "
              "created_by, runner, n_erros, n_avisos, error, agendado_para, via")


def create_job(db: dict, params: dict, user: str, status: str = "EM CURSO", runner: str = "portal",
               agendado_para: str | None = None) -> int:
    with tx() as t:
        return t.insert("jobs", {"db_id": db["id"], "db_name": db["name"], "params": json.dumps(params),
                                 "status": status, "started_at": agendado_para or now(), "created_by": user,
                                 "runner": runner, "agendado_para": agendado_para})


def update_job(job_id: int, **fields) -> None:
    if fields:
        with tx() as t:
            t.update("jobs", job_id, fields)


def _job(row):
    if row:
        row["params"] = json.loads(row["params"])
        row["resultado"] = resultado(row)
    return row


def resultado(j: dict) -> str:
    """sucesso | aviso | erro | falhado | em curso (para os filtros das execucoes)."""
    st = j.get("status")
    if st == "AGENDADO":
        return "agendado"
    if st in ("EM CURSO", "PENDENTE"):
        return "em curso"
    if st in ("INTERROMPIDO", "CANCELADO") or (st == "ERRO" and j.get("error")):
        return "falhado"
    if st == "ERRO" or (j.get("n_erros") or 0) > 0:
        return "erro"
    if (j.get("n_avisos") or 0) > 0:
        return "aviso"
    return "sucesso"


def contar(output: str) -> tuple[int, int]:
    """(linhas com erro, linhas com aviso) do output de uma execucao."""
    import re
    erros = avisos = 0
    for linha in (output or "").splitlines():
        if re.search(r"--\s*ERRO|ORA-\d{5}", linha):
            erros += 1
        elif re.search(r"--\s*(AVISO|aviso|MANUAL)|IGNORADO", linha):
            avisos += 1
    return erros, avisos


def get_job(job_id) -> dict | None:
    with tx() as t:
        return _job(t.one("SELECT * FROM jobs WHERE id = ?", (job_id,)))


def list_jobs(limit: int = 200) -> list[dict]:
    with tx() as t:
        rows = t.all(f"SELECT {JOB_RESUMO} FROM jobs ORDER BY id DESC LIMIT ?", (int(limit),))
    return [_job(r) for r in rows]


def search_jobs(texto: str = "", limit: int = 1000) -> list[dict]:
    """Execucoes (sem o output), opcionalmente so as que tem `texto` no output, parametros,
    BD, utilizador ou erro. Preenche as contagens de erros/avisos que faltem (execucoes antigas)."""
    sql = f"SELECT {JOB_RESUMO} FROM jobs"
    args: tuple = ()
    if texto:
        like = f"%{texto.lower()}%"
        sql += (" WHERE LOWER(COALESCE(output, '')) LIKE ? OR LOWER(params) LIKE ? OR LOWER(db_name) LIKE ?"
                " OR LOWER(COALESCE(created_by, '')) LIKE ? OR LOWER(COALESCE(error, '')) LIKE ?")
        args = (like,) * 5
    with tx() as t:
        rows = t.all(sql + " ORDER BY id DESC LIMIT ?", (*args, int(limit)))
        falta = [r["id"] for r in rows if r["n_erros"] is None and r["status"] not in ("EM CURSO", "PENDENTE", "AGENDADO")]
        for jid in falta[:200]:
            out = t.val("SELECT output FROM jobs WHERE id = ?", (jid,)) or ""
            e, a = contar(out)
            t.run("UPDATE jobs SET n_erros = ?, n_avisos = ? WHERE id = ?", (e, a, jid))
            for r in rows:
                if r["id"] == jid:
                    r["n_erros"], r["n_avisos"] = e, a
    return [_job(r) for r in rows]


def delete_jobs(ids) -> int:
    """Apaga execucoes terminadas (nunca as que estao em curso ou na fila)."""
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    marks = ", ".join("?" * len(ids))
    with tx() as t:
        return t.run(f"DELETE FROM jobs WHERE id IN ({marks}) AND status NOT IN ('EM CURSO', 'PENDENTE', 'AGENDADO')", ids)


def interrupt_running_jobs(runner: str = "portal") -> None:
    """Ao arrancar: as execucoes deste processo que ficaram 'EM CURSO' morreram com ele.
    (as do agente so sao marcadas pelo agente, e vice-versa). As que correm num agente cliente
    (remoto) continuam la: o agente do portal volta a acompanha-las (jobs.retomar_remotos)."""
    with tx() as t:
        if runner == "portal":
            t.run("""UPDATE jobs SET status = 'INTERROMPIDO', finished_at = ?
                     WHERE status = 'EM CURSO' AND (runner = 'portal' OR runner IS NULL)""", (now(),))
        else:
            t.run("UPDATE jobs SET status = 'INTERROMPIDO', finished_at = ? WHERE status = 'EM CURSO' AND runner = ? "
                  "AND (remoto IS NULL OR remoto = '')", (now(), runner))


def remote_jobs() -> list[dict]:
    """Execucoes EM CURSO num agente cliente (para voltar a acompanhar depois de reiniciar)."""
    with tx() as t:
        return [_job(r) for r in t.all("SELECT * FROM jobs WHERE status = 'EM CURSO' AND remoto IS NOT NULL "
                                       "AND remoto <> '' ORDER BY id")]


def pending_jobs() -> list[int]:
    """Na fila (PENDENTE) e agendadas cuja hora chegou (AGENDADO), por ordem."""
    with tx() as t:
        return [r["id"] for r in t.all(
            "SELECT id FROM jobs WHERE status = 'PENDENTE' OR (status = 'AGENDADO' AND agendado_para <= ?) "
            "ORDER BY COALESCE(agendado_para, started_at), id", (now(),))]


def claim_job(job_id: int, runner: str = "agente") -> bool:
    """O agente fica com a execucao (so uma vez, mesmo com varios agentes); runner = quem a corre."""
    with tx() as t:
        return t.run("UPDATE jobs SET status = 'EM CURSO', started_at = ?, runner = ? WHERE id = ? AND (status = 'PENDENTE' "
                     "OR (status = 'AGENDADO' AND agendado_para <= ?))", (now(), runner, job_id, now())) == 1


# ------------------------------------------------------------------ acoes sobre servicos (agente)
def add_action(unit: str, action: str, user: str) -> int:
    with tx() as t:
        return t.insert("system_actions", {"unit": unit, "action": action, "status": "PENDENTE",
                                           "requested_by": user, "requested_at": now()})


def get_action(aid) -> dict | None:
    with tx() as t:
        return t.one("SELECT * FROM system_actions WHERE id = ?", (aid,))


def pending_actions() -> list[dict]:
    with tx() as t:
        return t.all("SELECT * FROM system_actions WHERE status = 'PENDENTE' ORDER BY id")


def list_actions(limit: int = 20) -> list[dict]:
    with tx() as t:
        return t.all("SELECT * FROM system_actions ORDER BY id DESC LIMIT ?", (int(limit),))


def finish_action(aid: int, status: str, result: str) -> None:
    with tx() as t:
        t.run("UPDATE system_actions SET status = ?, result = ?, finished_at = ? WHERE id = ?",
              (status, (result or "")[:4000], now(), aid))


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
