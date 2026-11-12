"""
Núcleo comum do tesla-key gateway: base de dados, definições, clientes, tokens Tesla e logs.

Base de dados SQLite em /opt/tesla/gateway.db (volume ./tesla).
"""
import base64
import fcntl
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import requests

BASE = os.environ.get("TESLA_BASE", "/opt/tesla")
CONF_FILE = os.path.join(BASE, "tesla.env")
TOKENS_FILE = os.path.join(BASE, "tokens.json")
LOCK_FILE = os.path.join(BASE, ".tokens.lock")
DB_FILE = os.path.join(BASE, "gateway.db")
TIMEOUT = 30

AUDIENCES = {
    "eu": "https://fleet-api.prd.eu.vn.cloud.tesla.com",
    "na": "https://fleet-api.prd.na.vn.cloud.tesla.com",
}
DEFAULT_TOKEN_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"

DEFAULTS = {
    "cache_segundos": "60",
    "retencao_logs_dias": "30",
    "status_api_ativa": "0",
    "status_api_token_hash": "",
    "status_api_token_prefixo": "",
    "admin_user": "",
    "admin_pass_hash": "",
    "secret_key": "",
}

_token_lock = threading.Lock()


class ErroGateway(Exception):
    def __init__(self, codigo, mensagem):
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem


def agora_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Base de dados
# ---------------------------------------------------------------------------

@contextmanager
def db():
    con = sqlite3.connect(DB_FILE, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    os.makedirs(BASE, exist_ok=True)
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS definicoes (chave TEXT PRIMARY KEY, valor TEXT);
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT UNIQUE NOT NULL,
            descricao TEXT DEFAULT '',
            chave_hash TEXT NOT NULL,
            prefixo TEXT NOT NULL,
            ativo INTEGER DEFAULT 1,
            criado_em TEXT,
            ultimo_uso TEXT,
            pedidos INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            nivel TEXT NOT NULL,
            origem TEXT NOT NULL,
            cliente TEXT,
            metodo TEXT,
            caminho TEXT,
            status INTEGER,
            ms INTEGER,
            mensagem TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
        CREATE INDEX IF NOT EXISTS idx_logs_nivel ON logs(nivel);
        """)
        for k, v in DEFAULTS.items():
            c.execute("INSERT OR IGNORE INTO definicoes(chave, valor) VALUES (?, ?)", (k, v))
        if not c.execute("SELECT valor FROM definicoes WHERE chave='secret_key'").fetchone()[0]:
            c.execute("UPDATE definicoes SET valor=? WHERE chave='secret_key'", (secrets.token_hex(32),))


def get_def(chave):
    with db() as c:
        r = c.execute("SELECT valor FROM definicoes WHERE chave=?", (chave,)).fetchone()
    return r["valor"] if r else DEFAULTS.get(chave, "")


def set_def(chave, valor):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO definicoes(chave, valor) VALUES (?, ?)", (chave, str(valor)))


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

def log(nivel, origem, mensagem="", cliente=None, metodo=None, caminho=None, status=None, ms=None):
    try:
        with db() as c:
            c.execute(
                "INSERT INTO logs(ts, nivel, origem, cliente, metodo, caminho, status, ms, mensagem)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (agora_iso(), nivel, origem, cliente, metodo, caminho, status, ms, mensagem[:1000]),
            )
    except sqlite3.Error:
        pass


def limpar_logs():
    dias = int(get_def("retencao_logs_dias") or 30)
    limite = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat(timespec="seconds")
    with db() as c:
        return c.execute("DELETE FROM logs WHERE ts < ?", (limite,)).rowcount


def ler_logs(nivel=None, cliente=None, limite=200):
    sql, args = "SELECT * FROM logs WHERE 1=1", []
    if nivel:
        sql += " AND nivel=?"
        args.append(nivel)
    if cliente:
        sql += " AND cliente=?"
        args.append(cliente)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limite))
    with db() as c:
        return [dict(r) for r in c.execute(sql, args)]


def contagem_logs(horas=24):
    desde = (datetime.now(timezone.utc) - timedelta(hours=horas)).isoformat(timespec="seconds")
    with db() as c:
        rows = c.execute("SELECT nivel, COUNT(*) n FROM logs WHERE ts >= ? GROUP BY nivel", (desde,)).fetchall()
        pedidos = c.execute("SELECT COUNT(*) FROM logs WHERE ts >= ? AND origem='api'", (desde,)).fetchone()[0]
    r = {"INFO": 0, "WARNING": 0, "ERROR": 0}
    r.update({x["nivel"]: x["n"] for x in rows})
    r["pedidos_api"] = pedidos
    return r


# ---------------------------------------------------------------------------
# Chaves (clientes e status API)
# ---------------------------------------------------------------------------

def hash_chave(chave):
    return hashlib.sha256(chave.encode()).hexdigest()


def nova_chave(prefixo):
    chave = f"{prefixo}_{secrets.token_urlsafe(32)}"
    return chave, chave[: len(prefixo) + 7]


def criar_cliente(nome, descricao=""):
    nome = nome.strip()
    if not nome or not all(ch.isalnum() or ch in "-_." for ch in nome):
        raise ErroGateway(400, "nome inválido (letras, números, - _ .)")
    chave, pref = nova_chave("tk")
    try:
        with db() as c:
            c.execute(
                "INSERT INTO clientes(nome, descricao, chave_hash, prefixo, criado_em) VALUES (?,?,?,?,?)",
                (nome, descricao, hash_chave(chave), pref, agora_iso()),
            )
    except sqlite3.IntegrityError:
        raise ErroGateway(409, f"já existe o cliente '{nome}'")
    log("INFO", "admin", f"cliente criado: {nome}")
    return chave


def regenerar_cliente(nome):
    chave, pref = nova_chave("tk")
    with db() as c:
        n = c.execute("UPDATE clientes SET chave_hash=?, prefixo=? WHERE nome=?",
                      (hash_chave(chave), pref, nome)).rowcount
    if not n:
        raise ErroGateway(404, f"cliente '{nome}' não existe")
    log("WARNING", "admin", f"chave regenerada: {nome}")
    return chave


def ativar_cliente(nome, ativo):
    with db() as c:
        n = c.execute("UPDATE clientes SET ativo=? WHERE nome=?", (1 if ativo else 0, nome)).rowcount
    if not n:
        raise ErroGateway(404, f"cliente '{nome}' não existe")
    log("INFO", "admin", f"cliente {'ativado' if ativo else 'desativado'}: {nome}")


def apagar_cliente(nome):
    with db() as c:
        n = c.execute("DELETE FROM clientes WHERE nome=?", (nome,)).rowcount
    if not n:
        raise ErroGateway(404, f"cliente '{nome}' não existe")
    log("WARNING", "admin", f"cliente apagado: {nome}")


def listar_clientes():
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, nome, descricao, prefixo, ativo, criado_em, ultimo_uso, pedidos FROM clientes ORDER BY nome")]


def validar_cliente(chave):
    """Devolve o nome do cliente ativo para a chave, ou None."""
    if not chave:
        return None
    with db() as c:
        r = c.execute("SELECT nome, ativo FROM clientes WHERE chave_hash=?", (hash_chave(chave),)).fetchone()
        if not r or not r["ativo"]:
            return None
        c.execute("UPDATE clientes SET ultimo_uso=?, pedidos=pedidos+1 WHERE nome=?", (agora_iso(), r["nome"]))
        return r["nome"]


def gerar_token_status():
    chave, pref = nova_chave("ts")
    set_def("status_api_token_hash", hash_chave(chave))
    set_def("status_api_token_prefixo", pref)
    log("WARNING", "admin", "token da Status API gerado")
    return chave


def validar_token_status(token):
    h = get_def("status_api_token_hash")
    return bool(token and h and secrets.compare_digest(hash_chave(token), h))


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

def definir_admin(user, password):
    from werkzeug.security import generate_password_hash
    if len(password) < 8:
        raise ErroGateway(400, "a password tem de ter pelo menos 8 caracteres")
    set_def("admin_user", user.strip())
    set_def("admin_pass_hash", generate_password_hash(password))
    log("WARNING", "admin", f"credenciais do portal definidas para '{user.strip()}'")


def validar_admin(user, password):
    from werkzeug.security import check_password_hash
    h = get_def("admin_pass_hash")
    return bool(h) and user == get_def("admin_user") and check_password_hash(h, password)


# ---------------------------------------------------------------------------
# Configuração Tesla e tokens
# ---------------------------------------------------------------------------

def ler_conf():
    if not os.path.exists(CONF_FILE):
        raise ErroGateway(500, f"falta {CONF_FILE}")
    conf = {}
    with open(CONF_FILE, encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            k, v = linha.split("=", 1)
            conf[k.strip()] = v.strip().strip('"').strip("'")
    conf["AUDIENCE"] = AUDIENCES.get(conf.get("TESLA_REGION", "eu"), AUDIENCES["eu"])
    if not conf.get("TESLA_TOKEN_URL"):
        conf["TESLA_TOKEN_URL"] = DEFAULT_TOKEN_URL
    return conf


def jwt_exp(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return int(json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0))
    except Exception:
        return 0


def ler_tokens():
    if not os.path.exists(TOKENS_FILE):
        raise ErroGateway(503, "sem tokens: correr `tesla-setup url` e `tesla-setup token <code>`")
    with open(TOKENS_FILE, encoding="utf-8") as f:
        return json.load(f)


def estado_token():
    try:
        t = ler_tokens()
    except ErroGateway as e:
        return {"ok": False, "erro": e.mensagem}
    exp = jwt_exp(t.get("access_token", ""))
    rest = int(exp - time.time()) if exp else None
    return {
        "ok": bool(t.get("refresh_token")),
        "obtido_em": t.get("obtido_em"),
        "expira_em": datetime.fromtimestamp(exp, timezone.utc).isoformat() if exp else None,
        "segundos_restantes": max(0, rest) if rest is not None else None,
        "tem_refresh_token": bool(t.get("refresh_token")),
    }


def _guardar_tokens(dados):
    dados["obtido_em"] = agora_iso()
    tmp = TOKENS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, TOKENS_FILE)


def access_token(forcar=False):
    with _token_lock, open(LOCK_FILE, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        tokens = ler_tokens()
        at = tokens.get("access_token", "")
        if not forcar and jwt_exp(at) - time.time() > 300:
            return at
        conf = ler_conf()
        rt = tokens.get("refresh_token")
        if not rt:
            raise ErroGateway(503, "tokens.json sem refresh_token")
        try:
            r = requests.post(conf["TESLA_TOKEN_URL"], data={
                "grant_type": "refresh_token",
                "client_id": conf.get("TESLA_CLIENT_ID", ""),
                "refresh_token": rt,
            }, timeout=TIMEOUT)
        except requests.RequestException as e:
            log("ERROR", "token", f"falha de rede na renovação: {e}")
            raise ErroGateway(502, "falha de rede na renovação do token")
        if r.status_code != 200:
            log("ERROR", "token", f"renovação falhou: {r.status_code} {r.text[:300]}")
            raise ErroGateway(502, f"falhou a renovação do token ({r.status_code})")
        tokens.update(r.json())
        _guardar_tokens(tokens)
        log("INFO", "token", "access token renovado")
        return tokens["access_token"]


def tesla(metodo, caminho, **kw):
    conf = ler_conf()
    url = f"{conf['AUDIENCE']}/api/1/{caminho.lstrip('/')}"
    r = None
    for tentativa in (0, 1):
        h = {"Authorization": f"Bearer {access_token(forcar=tentativa == 1)}"}
        try:
            r = requests.request(metodo, url, headers=h, timeout=TIMEOUT, **kw)
        except requests.RequestException as e:
            log("ERROR", "tesla", f"{metodo} {caminho}: {e}")
            raise ErroGateway(502, "falha de ligação à Tesla Fleet API")
        if r.status_code != 401:
            break
    if r.status_code >= 500:
        log("ERROR", "tesla", f"{metodo} {caminho} -> {r.status_code} {r.text[:200]}")
    elif r.status_code == 429:
        log("WARNING", "tesla", f"{metodo} {caminho} -> 429 (limite de pedidos)")
    return r


def resumo():
    """Estado geral do gateway (portal admin e Status API)."""
    try:
        conf = ler_conf()
        conf_ok, dominio, regiao = True, conf.get("TESLA_DOMAIN"), conf.get("TESLA_REGION", "eu")
    except ErroGateway:
        conf_ok, dominio, regiao = False, None, None
    clientes = listar_clientes()
    return {
        "gateway": "ok",
        "hora": agora_iso(),
        "configuracao": {"ok": conf_ok, "dominio": dominio, "regiao": regiao},
        "token": estado_token(),
        "clientes": {"total": len(clientes), "ativos": sum(1 for c in clientes if c["ativo"])},
        "ultimas_24h": contagem_logs(24),
        "cache_segundos": int(get_def("cache_segundos") or 60),
        "status_api_ativa": get_def("status_api_ativa") == "1",
    }
