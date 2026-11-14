"""
Núcleo comum do tesla-key gateway: base de dados, definições, clientes, tokens Tesla e logs.

Base de dados SQLite em /opt/tesla/gateway.db (volume ./tesla).
"""
import base64
import fcntl
import re
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
    "cache_segundos": "60",              # vehicle_data (/data)
    "cache_localizacao_segundos": "60",  # /location
    "cache_veiculos_segundos": "60",     # /api/vehicles e /api/vehicles/<vin>
    "imagem_cache_dias": "",             # vazio = TESLA_IMAGE_CACHE_DIAS do tesla.env (ou 30)
    "data_endpoints_omissao": "charge_state;climate_state;drive_state;vehicle_state;vehicle_config",
    "funcionalidades": "",               # vazio = todas; senão lista de permissões ativas no gateway
    # preços (USD) para a estimativa de consumo; confirmar em developer.tesla.com
    "preco_dados": "0.002",
    "preco_wake": "0.02",
    "preco_comando": "0.001",
    "credito_mensal": "10",
    # recolha de viagens (leituras periódicas)
    "recolha_ativa": "0",
    "recolha_intervalo_conduzir": "120",   # s entre leituras a conduzir
    "recolha_intervalo_parado": "600",     # s entre leituras parado e acordado
    "recolha_minutos_parado": "30",        # depois disto parado, passa ao intervalo de "a dormir"
    "recolha_intervalo_dormir": "1800",    # s entre tentativas com o carro a dormir
    "recolha_limite_diario": "150",        # máx. pedidos de dados por veículo e por dia (Tesla: 200)
    # backups
    "backup_automatico": "1",              # backup diário do gateway.db
    "backup_retencao": "14",               # nº de backups guardados
    # portal admin
    "tema": "claro",                       # claro | escuro | tesla
    "login_max_tentativas": "5",           # tentativas falhadas por IP...
    "login_janela_minutos": "15",          # ...nesta janela antes de bloquear
    "login_bloqueio_minutos": "15",        # duração do bloqueio
    "retencao_auditoria_dias": "180",
    "retencao_logs_dias": "30",
    "status_api_ativa": "0",
    "status_api_token_hash": "",
    "status_api_token_prefixo": "",
    "admin_user": "",
    "admin_pass_hash": "",
    "secret_key": "",
}

# Permissões por cliente: chave -> (descrição, endpoints)
PERMISSOES = {
    "veiculos":    ("Lista e estado dos veículos (online/asleep)", "/api/vehicles, /api/vehicles/<vin>"),
    "carga":       ("Bateria, autonomia e carregamento (charge_state)", "/data"),
    "clima":       ("Temperaturas e climatização (climate_state)", "/data"),
    "conducao":    ("Mudança, velocidade e potência, sem coordenadas (drive_state)", "/data"),
    "estado":      ("Odómetro, trancas, sentinela, versão de software (vehicle_state)", "/data"),
    "config":      ("Modelo, cor e jantes (vehicle_config)", "/data"),
    "localizacao": ("Coordenadas GPS (location_data)", "/location, /data"),
    "imagem":      ("Imagem do modelo do carro", "/image"),
    "token":       ("Estado do token Tesla", "/api/token/status"),
    "acordar":     ("Acordar o carro (custo extra, gasta bateria)", "/wake"),
    "fleet":       ("Passthrough de leitura para qualquer endpoint da Fleet API (acesso amplo)", "/api/fleet/..."),
    "viagens":     ("Viagens e posições registadas pelo gateway", "/trips, /positions"),
}
PRESETS = {
    "tudo": list(PERMISSOES),
    "leitura": [p for p in PERMISSOES if p not in ("acordar", "fleet")],
    "basico": ["veiculos", "carga", "imagem"],
    "nenhuma": [],
}
PERMISSAO_OMISSAO = "leitura"   # novos clientes
# secção de vehicle_data -> permissão necessária
SECCOES = {
    "charge_state": "carga", "climate_state": "clima", "drive_state": "conducao",
    "vehicle_state": "estado", "vehicle_config": "config", "location_data": "localizacao",
    "gui_settings": "estado", "closures_state": "estado", "charge_schedule_data": "carga",
    "preconditioning_schedule_data": "clima", "vehicle_data_combo": "estado",
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
        CREATE TABLE IF NOT EXISTS consumo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            dia TEXT NOT NULL,
            categoria TEXT NOT NULL,
            caminho TEXT,
            cliente TEXT,
            status INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_consumo_dia ON consumo(dia);
        CREATE TABLE IF NOT EXISTS viagens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vin TEXT NOT NULL,
            inicio TEXT NOT NULL, fim TEXT,
            lat_inicio REAL, lon_inicio REAL, lat_fim REAL, lon_fim REAL,
            odo_inicio_km REAL, odo_fim_km REAL, km REAL,
            bateria_inicio INTEGER, bateria_fim INTEGER,
            velocidade_max_kmh INTEGER, pontos INTEGER DEFAULT 0,
            estado TEXT DEFAULT 'aberta'
        );
        CREATE INDEX IF NOT EXISTS idx_viagens_vin_inicio ON viagens(vin, inicio);
        CREATE TABLE IF NOT EXISTS pontos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vin TEXT NOT NULL, viagem_id INTEGER,
            ts TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
            velocidade_kmh INTEGER, direcao INTEGER, bateria INTEGER, odo_km REAL
        );
        CREATE INDEX IF NOT EXISTS idx_pontos_vin_ts ON pontos(vin, ts);
        CREATE INDEX IF NOT EXISTS idx_pontos_viagem ON pontos(viagem_id);
        CREATE TABLE IF NOT EXISTS utilizadores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT UNIQUE NOT NULL,
            pass_hash TEXT NOT NULL,
            perfil TEXT NOT NULL DEFAULT 'leitura',
            ativo INTEGER DEFAULT 1,
            criado_em TEXT, ultimo_login TEXT
        );
        CREATE TABLE IF NOT EXISTS auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, ip TEXT, utilizador TEXT,
            tipo TEXT NOT NULL,          -- login | login_falhado | bloqueado | logout | acao
            detalhe TEXT, agente TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_auditoria_ts ON auditoria(ts);
        CREATE INDEX IF NOT EXISTS idx_auditoria_ip ON auditoria(ip, tipo, ts);
        CREATE TABLE IF NOT EXISTS estado_veiculo (
            vin TEXT PRIMARY KEY, ts TEXT, estado TEXT, mudanca TEXT,
            lat REAL, lon REAL, odo_km REAL, bateria INTEGER, parado_desde TEXT, proxima_leitura REAL
        );
        """)
        if not c.execute("SELECT COUNT(*) FROM utilizadores").fetchone()[0]:
            u = c.execute("SELECT valor FROM definicoes WHERE chave='admin_user'").fetchone()
            h = c.execute("SELECT valor FROM definicoes WHERE chave='admin_pass_hash'").fetchone()
            if u and h and u[0] and h[0]:
                c.execute("INSERT INTO utilizadores(nome, pass_hash, perfil, criado_em) VALUES (?,?,?,?)",
                          (u[0], h[0], "admin", agora_iso()))
        cols = [r[1] for r in c.execute("PRAGMA table_info(clientes)")]
        if "permissoes" not in cols:
            # clientes que já existiam mantêm o acesso total
            c.execute("ALTER TABLE clientes ADD COLUMN permissoes TEXT")
            c.execute("UPDATE clientes SET permissoes=?", (",".join(PRESETS["tudo"]),))
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
    dias_aud = int(get_def("retencao_auditoria_dias") or 180)
    limite_aud = (datetime.now(timezone.utc) - timedelta(days=dias_aud)).isoformat(timespec="seconds")
    with db() as c:
        c.execute("DELETE FROM auditoria WHERE ts < ?", (limite_aud,))
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


def resolver_permissoes(valor):
    """'leitura' | 'tudo' | 'carga,imagem' | lista -> lista ordenada e validada."""
    if isinstance(valor, (list, tuple, set)):
        valor = ",".join(str(v) for v in valor)
    itens = [x.strip() for x in str(valor or "").split(",") if x.strip()]
    perms = set()
    for x in itens:
        if x in PRESETS:
            perms.update(PRESETS[x])
        elif x in PERMISSOES:
            perms.add(x)
        else:
            raise ErroGateway(400, f"permissão desconhecida: {x} (válidas: {', '.join(PERMISSOES)}; "
                                   f"presets: {', '.join(PRESETS)})")
    return [p for p in PERMISSOES if p in perms]


def definir_permissoes(nome, valor):
    perms = resolver_permissoes(valor)
    with db() as c:
        n = c.execute("UPDATE clientes SET permissoes=? WHERE nome=?", (",".join(perms), nome)).rowcount
    if not n:
        raise ErroGateway(404, f"cliente '{nome}' não existe")
    log("WARNING", "admin", f"permissões de {nome}: {', '.join(perms) or '(nenhuma)'}")
    return perms


def alterar_permissoes(nome, adicionar=(), remover=()):
    atual = set(permissoes_cliente(nome))
    atual |= set(resolver_permissoes(list(adicionar)))
    atual -= set(resolver_permissoes(list(remover)))
    return definir_permissoes(nome, list(atual))


def permissoes_cliente(nome):
    with db() as c:
        r = c.execute("SELECT permissoes FROM clientes WHERE nome=?", (nome,)).fetchone()
    if not r:
        raise ErroGateway(404, f"cliente '{nome}' não existe")
    return [p for p in (r["permissoes"] or "").split(",") if p]


def criar_cliente(nome, descricao="", permissoes=None):
    nome = nome.strip()
    if not nome or not all(ch.isalnum() or ch in "-_." for ch in nome):
        raise ErroGateway(400, "nome inválido (letras, números, - _ .)")
    perms = resolver_permissoes(permissoes if permissoes is not None else PERMISSAO_OMISSAO)
    chave, pref = nova_chave("tk")
    try:
        with db() as c:
            c.execute(
                "INSERT INTO clientes(nome, descricao, chave_hash, prefixo, criado_em, permissoes)"
                " VALUES (?,?,?,?,?,?)",
                (nome, descricao, hash_chave(chave), pref, agora_iso(), ",".join(perms)),
            )
    except sqlite3.IntegrityError:
        raise ErroGateway(409, f"já existe o cliente '{nome}'")
    log("INFO", "admin", f"cliente criado: {nome} ({', '.join(perms) or 'sem permissões'})")
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
            "SELECT id, nome, descricao, prefixo, ativo, criado_em, ultimo_uso, pedidos, permissoes"
            " FROM clientes ORDER BY nome")]


def validar_cliente(chave):
    """Devolve (nome, permissões) do cliente ativo para a chave, ou (None, [])."""
    if not chave:
        return None, []
    with db() as c:
        r = c.execute("SELECT nome, ativo, permissoes FROM clientes WHERE chave_hash=?",
                      (hash_chave(chave),)).fetchone()
        if not r or not r["ativo"]:
            return None, []
        c.execute("UPDATE clientes SET ultimo_uso=?, pedidos=pedidos+1 WHERE nome=?", (agora_iso(), r["nome"]))
        return r["nome"], [p for p in (r["permissoes"] or "").split(",") if p]


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

PERFIS = {"admin": "Administrador (tudo)", "leitura": "Só leitura (consulta, sem alterações)"}


def _validar_nome_utilizador(nome):
    nome = (nome or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{2,32}", nome):
        raise ErroGateway(400, "nome de utilizador inválido (2–32: letras, números, . _ -)")
    return nome


def definir_utilizador(nome, password=None, perfil=None, ativo=None, criar=True):
    """Cria ou altera um utilizador do portal. password=None mantém a atual."""
    from werkzeug.security import generate_password_hash
    nome = _validar_nome_utilizador(nome)
    if perfil is not None and perfil not in PERFIS:
        raise ErroGateway(400, "perfil: admin | leitura")
    if password is not None and len(password) < 8:
        raise ErroGateway(400, "a password tem de ter pelo menos 8 caracteres")
    with db() as c:
        existe = c.execute("SELECT perfil FROM utilizadores WHERE nome=?", (nome,)).fetchone()
        if not existe:
            if not criar:
                raise ErroGateway(404, f"utilizador '{nome}' não existe")
            if password is None:
                raise ErroGateway(400, "password obrigatória para um utilizador novo")
            c.execute("INSERT INTO utilizadores(nome, pass_hash, perfil, ativo, criado_em) VALUES (?,?,?,?,?)",
                      (nome, generate_password_hash(password), perfil or "leitura",
                       0 if ativo is False else 1, agora_iso()))
        else:
            if password is not None:
                c.execute("UPDATE utilizadores SET pass_hash=? WHERE nome=?", (generate_password_hash(password), nome))
            if perfil is not None:
                c.execute("UPDATE utilizadores SET perfil=? WHERE nome=?", (perfil, nome))
            if ativo is not None:
                c.execute("UPDATE utilizadores SET ativo=? WHERE nome=?", (1 if ativo else 0, nome))
        n_admin = c.execute("SELECT COUNT(*) FROM utilizadores WHERE perfil='admin' AND ativo=1").fetchone()[0]
        if n_admin == 0:
            c.rollback()
            raise ErroGateway(400, "tem de existir pelo menos um administrador ativo")
    log("WARNING", "admin", f"utilizador do portal {'criado' if not existe else 'alterado'}: {nome}")


def apagar_utilizador(nome):
    with db() as c:
        r = c.execute("SELECT perfil FROM utilizadores WHERE nome=?", (nome,)).fetchone()
        if not r:
            raise ErroGateway(404, f"utilizador '{nome}' não existe")
        if r["perfil"] == "admin" and c.execute(
                "SELECT COUNT(*) FROM utilizadores WHERE perfil='admin' AND ativo=1").fetchone()[0] <= 1:
            raise ErroGateway(400, "não é possível apagar o último administrador")
        c.execute("DELETE FROM utilizadores WHERE nome=?", (nome,))
    log("WARNING", "admin", f"utilizador do portal apagado: {nome}")


def listar_utilizadores():
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT nome, perfil, ativo, criado_em, ultimo_login FROM utilizadores ORDER BY nome")]


def validar_utilizador(nome, password):
    """Devolve o perfil se as credenciais forem válidas e o utilizador estiver ativo; senão None."""
    from werkzeug.security import check_password_hash
    with db() as c:
        r = c.execute("SELECT pass_hash, perfil, ativo FROM utilizadores WHERE nome=?", (nome,)).fetchone()
        if r and r["ativo"] and check_password_hash(r["pass_hash"], password):
            c.execute("UPDATE utilizadores SET ultimo_login=? WHERE nome=?", (agora_iso(), nome))
            return r["perfil"]
    return None


def perfil_utilizador(nome):
    with db() as c:
        r = c.execute("SELECT perfil, ativo FROM utilizadores WHERE nome=?", (nome,)).fetchone()
    return r["perfil"] if r and r["ativo"] else None


def existem_utilizadores():
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM utilizadores").fetchone()[0] > 0


# compatibilidade com versões anteriores (tesla-setup admin)
def definir_admin(user, password):
    definir_utilizador(user, password, perfil="admin")


def validar_admin(user, password):
    return validar_utilizador(user, password) is not None


# ---------------------------------------------------------------------------
# Auditoria do portal e proteção contra tentativas repetidas
# ---------------------------------------------------------------------------

def auditar(tipo, ip=None, utilizador=None, detalhe="", agente=""):
    try:
        with db() as c:
            c.execute("INSERT INTO auditoria(ts, ip, utilizador, tipo, detalhe, agente) VALUES (?,?,?,?,?,?)",
                      (agora_iso(), ip, utilizador, tipo, (detalhe or "")[:300], (agente or "")[:200]))
    except sqlite3.Error:
        pass


def ip_bloqueado(ip):
    """Segundos de bloqueio restantes para o IP (0 se não estiver bloqueado)."""
    janela = int(get_def("login_janela_minutos") or 15)
    maxt = int(get_def("login_max_tentativas") or 5)
    bloq = int(get_def("login_bloqueio_minutos") or 15)
    desde = (datetime.now(timezone.utc) - timedelta(minutes=max(janela, bloq))).isoformat(timespec="seconds")
    with db() as c:
        falhas = [r[0] for r in c.execute(
            "SELECT ts FROM auditoria WHERE ip=? AND tipo='login_falhado' AND ts >= ? ORDER BY ts DESC", (ip, desde))]
        ultimo_ok = c.execute("SELECT MAX(ts) FROM auditoria WHERE ip=? AND tipo='login'", (ip,)).fetchone()[0]
    if ultimo_ok:
        falhas = [f for f in falhas if f > ultimo_ok]
    if len(falhas) < maxt:
        return 0
    fim = datetime.fromisoformat(falhas[maxt - 1]) + timedelta(minutes=bloq)
    return max(0, int((fim - datetime.now(timezone.utc)).total_seconds()))


def ler_auditoria(tipo=None, utilizador=None, ip=None, limite=300):
    sql, args = "SELECT * FROM auditoria WHERE 1=1", []
    for campo, val in (("tipo", tipo), ("utilizador", utilizador), ("ip", ip)):
        if val:
            sql += f" AND {campo}=?"
            args.append(val)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limite))
    with db() as c:
        return [dict(r) for r in c.execute(sql, args)]


def apagar_auditoria(ids=None, dias=None, tudo=False):
    with db() as c:
        if tudo:
            n = c.execute("DELETE FROM auditoria").rowcount
        elif dias is not None:
            limite = (datetime.now(timezone.utc) - timedelta(days=int(dias))).isoformat(timespec="seconds")
            n = c.execute("DELETE FROM auditoria WHERE ts < ?", (limite,)).rowcount
        else:
            ids = [int(i) for i in (ids or [])]
            n = c.execute(f"DELETE FROM auditoria WHERE id IN ({','.join('?' * len(ids))})", ids).rowcount if ids else 0
    return n


def apagar_logs(ids=None, dias=None, tudo=False, nivel=None):
    with db() as c:
        if tudo:
            n = c.execute("DELETE FROM logs" + (" WHERE nivel=?" if nivel else ""), (nivel,) if nivel else ()).rowcount
        elif dias is not None:
            limite = (datetime.now(timezone.utc) - timedelta(days=int(dias))).isoformat(timespec="seconds")
            n = c.execute("DELETE FROM logs WHERE ts < ?", (limite,)).rowcount
        else:
            ids = [int(i) for i in (ids or [])]
            n = c.execute(f"DELETE FROM logs WHERE id IN ({','.join('?' * len(ids))})", ids).rowcount if ids else 0
    return n


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


def ler_conf_seguro():
    try:
        return ler_conf()
    except ErroGateway:
        return {}


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


def _categoria(metodo, caminho):
    if metodo.upper() == "POST" and caminho.rstrip("/").endswith("wake_up"):
        return "wake"
    if metodo.upper() == "POST":
        return "comando"
    return "dados"


def _cliente_atual():
    try:
        from flask import g, has_request_context
        return getattr(g, "cliente", None) if has_request_context() else None
    except Exception:
        return None


def registar_consumo(categoria, caminho, status, cliente=None):
    agora = datetime.now(timezone.utc)
    try:
        with db() as c:
            c.execute("INSERT INTO consumo(ts, dia, categoria, caminho, cliente, status) VALUES (?,?,?,?,?,?)",
                      (agora.isoformat(timespec="seconds"), agora.strftime("%Y-%m-%d"), categoria,
                       caminho.split("?")[0][:200], cliente, status))
    except sqlite3.Error:
        pass


def tesla(metodo, caminho, cliente_consumo=None, **kw):
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
    registar_consumo(_categoria(metodo, caminho), caminho, r.status_code, cliente_consumo or _cliente_atual())
    if r.status_code >= 500:
        log("ERROR", "tesla", f"{metodo} {caminho} -> {r.status_code} {r.text[:200]}")
    elif r.status_code == 429:
        log("WARNING", "tesla", f"{metodo} {caminho} -> 429 (limite de pedidos)")
    return r


# ---------------------------------------------------------------------------
# Consumo (estimativa) e limites
# ---------------------------------------------------------------------------

LIMITES_DIA = {"dados": 200, "comando": 50, "wake": 15}   # por veículo e por dia (documentação Tesla)


def consumo_mes(mes=None):
    """Estimativa do consumo do mês (UTC). mes='AAAA-MM' (omissão: mês atual)."""
    mes = mes or datetime.now(timezone.utc).strftime("%Y-%m")
    hoje = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    precos = {"dados": float(get_def("preco_dados") or 0), "wake": float(get_def("preco_wake") or 0),
              "comando": float(get_def("preco_comando") or 0)}
    with db() as c:
        cat = {r["categoria"]: r["n"] for r in c.execute(
            "SELECT categoria, COUNT(*) n FROM consumo WHERE dia LIKE ? GROUP BY categoria", (mes + "%",))}
        hoje_cat = {r["categoria"]: r["n"] for r in c.execute(
            "SELECT categoria, COUNT(*) n FROM consumo WHERE dia=? GROUP BY categoria", (hoje,))}
        por_cliente = [dict(r) for r in c.execute(
            "SELECT COALESCE(cliente,'(interno)') cliente, categoria, COUNT(*) n FROM consumo"
            " WHERE dia LIKE ? GROUP BY cliente, categoria ORDER BY n DESC", (mes + "%",))]
        por_caminho = [dict(r) for r in c.execute(
            "SELECT categoria, caminho, COUNT(*) n FROM consumo WHERE dia LIKE ?"
            " GROUP BY categoria, caminho ORDER BY n DESC LIMIT 20", (mes + "%",))]
        por_dia = [dict(r) for r in c.execute(
            "SELECT dia, COUNT(*) n FROM consumo WHERE dia LIKE ? GROUP BY dia ORDER BY dia", (mes + "%",))]
    categorias = {k: {"pedidos": cat.get(k, 0), "preco": precos[k],
                      "custo": round(cat.get(k, 0) * precos[k], 4),
                      "hoje": hoje_cat.get(k, 0), "limite_dia": LIMITES_DIA[k]} for k in precos}
    total = round(sum(v["custo"] for v in categorias.values()), 4)
    credito = float(get_def("credito_mensal") or 0)
    for x in por_cliente:
        x["custo"] = round(x["n"] * precos.get(x["categoria"], 0), 4)
    return {
        "mes": mes,
        "categorias": categorias,
        "total_usd": total,
        "credito_usd": credito,
        "credito_usado_pct": round(100 * total / credito, 1) if credito else None,
        "por_cliente": por_cliente,
        "por_caminho": por_caminho,
        "por_dia": por_dia,
        "nota": "estimativa contada pelo gateway; o valor oficial está em developer.tesla.com/dashboard",
    }


def funcionalidades_ativas():
    v = get_def("funcionalidades")
    return list(PERMISSOES) if not v else [p for p in v.split(",") if p in PERMISSOES]


def definir_funcionalidades(valor):
    perms = resolver_permissoes(valor)
    set_def("funcionalidades", ",".join(perms) if len(perms) < len(PERMISSOES) else "")
    log("WARNING", "admin", f"funcionalidades do gateway: {', '.join(perms) or '(nenhuma)'}")
    return perms


# ---------------------------------------------------------------------------
# tesla.env: leitura (com segredos mascarados) e escrita
# ---------------------------------------------------------------------------

CAMPOS_ENV = [
    # chave, descrição, tipo (texto|segredo|regiao|scopes|numero), editável
    ("TESLA_DOMAIN", "Domínio da chave pública (sem https://)", "texto"),
    ("TESLA_CLIENT_ID", "Client ID da aplicação", "texto"),
    ("TESLA_CLIENT_SECRET", "Client Secret da aplicação", "segredo"),
    ("TESLA_REGION", "Região da Fleet API", "regiao"),
    ("TESLA_REDIRECT_URI", "Redirect URI registado na aplicação", "texto"),
    ("TESLA_SCOPES", "Scopes pedidos no login (OAuth)", "scopes"),
    ("TESLA_IMAGE_MODEL", "Modelo para a imagem (m3, my, ms, mx)", "texto"),
    ("TESLA_IMAGE_OPTIONS", "Códigos da imagem (sem $)", "texto"),
    ("TESLA_IMAGE_CACHE_DIAS", "Dias de cache da imagem (se não definido em Definições)", "numero"),
    ("TESLA_AUTH_URL", "Endpoint de autorização", "texto"),
    ("TESLA_TOKEN_URL", "Endpoint dos tokens", "texto"),
]
SCOPES_TESLA = [
    ("openid", "Identificação (obrigatório)"),
    ("offline_access", "Refresh token (obrigatório)"),
    ("vehicle_device_data", "Vehicle Information"),
    ("vehicle_location", "Vehicle Location"),
    ("vehicle_cmds", "Vehicle Commands"),
    ("vehicle_charging_cmds", "Vehicle Charging Management (inclui histórico de carregamentos)"),
    ("energy_device_data", "Energy Product Information"),
    ("energy_cmds", "Energy Product Commands"),
]


def mascarar(chave, valor):
    if not valor:
        return ""
    if "SECRET" in chave:
        return "•" * 8 + valor[-4:] if len(valor) > 8 else "•" * len(valor)
    return valor


def ler_env_bruto():
    """Lista (chave, valor) pela ordem do ficheiro."""
    if not os.path.exists(CONF_FILE):
        return []
    out = []
    with open(CONF_FILE, encoding="utf-8") as f:
        for linha in f:
            l = linha.strip()
            if l and not l.startswith("#") and "=" in l:
                k, v = l.split("=", 1)
                out.append((k.strip(), v.strip().strip('"').strip("'")))
    return out


def _formatar_valor(v):
    if v == "" or all(ch.isalnum() or ch in "-_.,:/@" for ch in v):
        return v
    return "'" + v.replace("'", "") + "'"


def escrever_env(alteracoes):
    """Atualiza chaves do tesla.env mantendo comentários e ordem; acrescenta as que faltam."""
    validas = {k for k, _, _ in CAMPOS_ENV}
    for k in alteracoes:
        if k not in validas:
            raise ErroGateway(400, f"campo não editável: {k}")
    if "TESLA_REGION" in alteracoes and alteracoes["TESLA_REGION"] not in AUDIENCES:
        raise ErroGateway(400, "TESLA_REGION: eu | na")
    linhas = open(CONF_FILE, encoding="utf-8").read().splitlines() if os.path.exists(CONF_FILE) else []
    feitas = set()
    for i, linha in enumerate(linhas):
        l = linha.strip()
        if l.startswith("#") or "=" not in l:
            continue
        k = l.split("=", 1)[0].strip()
        if k in alteracoes:
            linhas[i] = f"{k}={_formatar_valor(alteracoes[k])}"
            feitas.add(k)
    for k, v in alteracoes.items():
        if k not in feitas:
            linhas.append(f"{k}={_formatar_valor(v)}")
    tmp = CONF_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas) + "\n")
    if os.path.exists(CONF_FILE):
        os.chmod(tmp, os.stat(CONF_FILE).st_mode & 0o777)
    os.replace(tmp, CONF_FILE)
    log("WARNING", "admin", "tesla.env alterado: " + ", ".join(sorted(alteracoes)))


def scopes_token():
    try:
        t = ler_tokens().get("access_token", "")
        payload = t.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        scp = json.loads(base64.urlsafe_b64decode(payload)).get("scp", [])
        return scp if isinstance(scp, list) else str(scp).split()
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Viagens e posições
# ---------------------------------------------------------------------------

def intervalo_periodo(periodo=None, valor=None, de=None, ate=None):
    """Converte filtros em (de, ate) ISO UTC. periodo: ano (2026) | mes (2026-11) | semana (2026-W46) | dias (30)."""
    from datetime import date
    if de or ate:
        return (de or "0000"), (ate or "9999")
    if not periodo or periodo == "tudo":
        return "0000", "9999"
    try:
        if periodo == "ano":
            a = int(valor or datetime.now(timezone.utc).year)
            return f"{a:04d}-01-01", f"{a + 1:04d}-01-01"
        if periodo == "mes":
            v = valor or datetime.now(timezone.utc).strftime("%Y-%m")
            a, m = (int(x) for x in v.split("-"))
            a2, m2 = (a + 1, 1) if m == 12 else (a, m + 1)
            return f"{a:04d}-{m:02d}-01", f"{a2:04d}-{m2:02d}-01"
        if periodo == "semana":
            v = valor or datetime.now(timezone.utc).strftime("%G-W%V")
            a, w = v.split("-W")
            ini = date.fromisocalendar(int(a), int(w), 1)
            return ini.isoformat(), (ini + timedelta(days=7)).isoformat()
        if periodo == "dias":
            ini = datetime.now(timezone.utc) - timedelta(days=int(valor or 30))
            return ini.isoformat(timespec="seconds"), "9999"
    except (ValueError, TypeError):
        pass
    raise ErroGateway(400, "período inválido: ano=AAAA | mes=AAAA-MM | semana=AAAA-Www | dias=N")


def listar_viagens(vin=None, de="0000", ate="9999", limite=200):
    sql = "SELECT * FROM viagens WHERE inicio >= ? AND inicio < ?"
    args = [de, ate]
    if vin:
        sql += " AND vin=?"
        args.append(vin)
    sql += " ORDER BY inicio DESC LIMIT ?"
    args.append(int(limite))
    with db() as c:
        return [dict(r) for r in c.execute(sql, args)]


def resumo_viagens(vin=None, de="0000", ate="9999"):
    sql = ("SELECT COUNT(*) n, COALESCE(SUM(km),0) km, COALESCE(SUM(bateria_inicio-bateria_fim),0) bateria,"
           " MAX(velocidade_max_kmh) vmax FROM viagens WHERE estado='fechada' AND inicio >= ? AND inicio < ?")
    args = [de, ate]
    if vin:
        sql += " AND vin=?"
        args.append(vin)
    with db() as c:
        r = dict(c.execute(sql, args).fetchone())
    r["km"] = round(r["km"], 1)
    return r


def obter_viagem(viagem_id, vin=None):
    with db() as c:
        v = c.execute("SELECT * FROM viagens WHERE id=?" + (" AND vin=?" if vin else ""),
                      (viagem_id, vin) if vin else (viagem_id,)).fetchone()
        if not v:
            raise ErroGateway(404, "viagem não encontrada")
        pts = [dict(r) for r in c.execute(
            "SELECT ts, lat, lon, velocidade_kmh, direcao, bateria, odo_km FROM pontos WHERE viagem_id=? ORDER BY ts",
            (viagem_id,))]
    return {**dict(v), "percurso": pts}


def posicoes(vin=None, de="0000", ate="9999", agregado=True, so_viagens=False, limite=50000):
    """Pontos para mapa. agregado: grelha de ~100 m com contagem (peso) — para mapa de calor."""
    cond, args = "ts >= ? AND ts < ?", [de, ate]
    if vin:
        cond += " AND vin=?"
        args.append(vin)
    if so_viagens:
        cond += " AND viagem_id IS NOT NULL"
    with db() as c:
        if agregado:
            rows = c.execute(
                f"SELECT ROUND(lat,3) lat, ROUND(lon,3) lon, COUNT(*) n FROM pontos WHERE {cond}"
                f" GROUP BY ROUND(lat,3), ROUND(lon,3) ORDER BY n DESC LIMIT ?", args + [int(limite)])
        else:
            rows = c.execute(f"SELECT ts, lat, lon, velocidade_kmh, viagem_id FROM pontos WHERE {cond}"
                             f" ORDER BY ts LIMIT ?", args + [int(limite)])
        return [dict(r) for r in rows]


def anos_com_dados():
    with db() as c:
        return [r[0] for r in c.execute("SELECT DISTINCT substr(ts,1,4) FROM pontos ORDER BY 1 DESC")]


# ---------------------------------------------------------------------------
# Exportação e backups
# ---------------------------------------------------------------------------

BACKUP_DIR = os.path.join(BASE, "backups")


def exportar(tipo, formato, vin=None, de="0000", ate="9999"):
    """Devolve (conteúdo, mimetype, nome). tipo: viagens|pontos|consumo|logs. formato: csv|json|geojson|gpx."""
    import csv
    import io
    if tipo == "viagens":
        linhas = listar_viagens(vin, de, ate, limite=10**7)
        if formato == "json":
            linhas = [obter_viagem(v["id"]) for v in linhas]
        elif formato == "gpx":
            gpx = ['<?xml version="1.0" encoding="UTF-8"?>',
                   '<gpx version="1.1" creator="tesla-key" xmlns="http://www.topografix.com/GPX/1/1">']
            for v in reversed(linhas):
                vg = obter_viagem(v["id"])
                gpx.append(f'<trk><name>Viagem {v["id"]} {v["inicio"][:16]}</name><trkseg>')
                gpx += [f'<trkpt lat="{p["lat"]}" lon="{p["lon"]}"><time>{p["ts"]}</time></trkpt>' for p in vg["percurso"]]
                gpx.append("</trkseg></trk>")
            gpx.append("</gpx>")
            return "\n".join(gpx), "application/gpx+xml", "viagens.gpx"
    elif tipo == "pontos":
        linhas = posicoes(vin, de, ate, agregado=False, limite=10**7)
        if formato == "geojson":
            fc = {"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                 "properties": {k: p[k] for k in ("ts", "velocidade_kmh", "viagem_id")}} for p in linhas]}
            return json.dumps(fc), "application/geo+json", "pontos.geojson"
    elif tipo == "consumo":
        with db() as c:
            linhas = [dict(r) for r in c.execute("SELECT * FROM consumo WHERE ts >= ? AND ts < ? ORDER BY ts", (de, ate))]
    elif tipo == "logs":
        with db() as c:
            linhas = [dict(r) for r in c.execute("SELECT * FROM logs WHERE ts >= ? AND ts < ? ORDER BY ts", (de, ate))]
    else:
        raise ErroGateway(400, "tipo: viagens | pontos | consumo | logs")
    if formato == "json":
        return json.dumps(linhas, ensure_ascii=False, indent=1), "application/json", f"{tipo}.json"
    if formato != "csv":
        raise ErroGateway(400, "formato inválido para este tipo (csv | json; viagens: gpx; pontos: geojson)")
    buf = io.StringIO()
    if linhas:
        w = csv.DictWriter(buf, fieldnames=list(linhas[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(linhas)
    return buf.getvalue(), "text/csv", f"{tipo}.csv"


def criar_backup(motivo="manual"):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    nome = f"gateway-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{motivo}.db"
    destino = os.path.join(BACKUP_DIR, nome)
    src = sqlite3.connect(DB_FILE, timeout=30)
    dst = sqlite3.connect(destino)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.chmod(destino, 0o600)
    log("INFO", "backup", f"backup criado: {nome}")
    limpar_backups()
    return nome


def listar_backups():
    if not os.path.isdir(BACKUP_DIR):
        return []
    out = []
    for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if f.endswith(".db"):
            st = os.stat(os.path.join(BACKUP_DIR, f))
            out.append({"nome": f, "tamanho_kb": round(st.st_size / 1024, 1),
                        "data": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")})
    return out


def caminho_backup(nome):
    if not re.fullmatch(r"gateway-[0-9]{8}-[0-9]{6}-[a-z-]+\.db", nome or ""):
        raise ErroGateway(400, "nome de backup inválido")
    f = os.path.join(BACKUP_DIR, nome)
    if not os.path.exists(f):
        raise ErroGateway(404, "backup não encontrado")
    return f


def apagar_backup(nome):
    os.remove(caminho_backup(nome))
    log("WARNING", "backup", f"backup apagado: {nome}")


def limpar_backups():
    ret = int(get_def("backup_retencao") or 14)
    automaticos = [b for b in listar_backups() if b["nome"].endswith("-auto.db")]
    for b in automaticos[ret:]:
        os.remove(os.path.join(BACKUP_DIR, b["nome"]))


def restaurar_backup(nome):
    f = caminho_backup(nome)
    criar_backup("antes-restauro")
    src = sqlite3.connect(f)
    dst = sqlite3.connect(DB_FILE, timeout=30)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    log("WARNING", "backup", f"base de dados restaurada a partir de {nome}")


def backup_diario():
    """Cria um backup automático se o último tiver mais de 24 h."""
    if get_def("backup_automatico") != "1":
        return None
    autos = [b for b in listar_backups() if b["nome"].endswith("-auto.db")]
    if autos and (datetime.now(timezone.utc) - datetime.fromisoformat(autos[0]["data"])) < timedelta(hours=24):
        return None
    return criar_backup("auto")


# ---------------------------------------------------------------------------
# Referência dos endpoints (portal admin → API, e GET /api/endpoints)
# ---------------------------------------------------------------------------

ENDPOINTS = [
    # metodo, caminho, permissao, descricao, parametros, exemplo de resposta
    ("GET", "/api/health", None, "Gateway a responder. Sem chave.", "", '{"ok": true}'),
    ("GET", "/api/endpoints", None, "Esta lista de endpoints (para documentação). Sem chave.", "", '[{"metodo": "GET", "caminho": "/api/me", ...}]'),
    ("GET", "/api/me", None, "Nome do cliente e permissões efetivas.", "",
     '{"cliente": "portal", "permissoes": ["veiculos", "carga"], "desativadas_no_gateway": []}'),
    ("GET", "/api/token/status", "token", "Validade do token Tesla.", "",
     '{"ok": true, "expira_em": "2026-11-14T20:00:00+00:00", "segundos_restantes": 27000}'),
    ("GET", "/api/vehicles", "veiculos", "Lista de veículos da conta (com cache).", "fresh=1",
     '{"response": [{"vin": "...", "display_name": "Tesla", "state": "online"}], "count": 1}'),
    ("GET", "/api/vehicles/{vin}", "veiculos", "Estado do veículo (online/asleep/offline); não acorda o carro.", "fresh=1",
     '{"response": {"vin": "...", "state": "asleep"}}'),
    ("GET", "/api/vehicles/{vin}/data", "carga, clima, conducao, estado, config", "Dados atuais (vehicle_data) das secções permitidas. 408 se o carro estiver a dormir.",
     "endpoints=charge_state;climate_state · fresh=1",
     '{"response": {"charge_state": {"battery_level": 78, "battery_range": 221.4}}, "cache": false}'),
    ("GET", "/api/vehicles/{vin}/location", "localizacao", "Posição atual (requer o scope vehicle_location). 408 se a dormir.", "fresh=1",
     '{"latitude": 38.72, "longitude": -9.13, "velocidade_kmh": null, "gps_em": "...", "mapa": "https://..."}'),
    ("GET", "/api/vehicles/{vin}/image", "imagem", "Imagem do modelo (PNG/JPEG).", "view=STUD_3QTR|STUD_SIDE|STUD_REAR|STUD_FRONT|STUD_SEAT · size=800 · fresh=1 · formato=url",
     "(imagem) ou {\"url\": \"https://static-assets.tesla.com/...\"}"),
    ("GET", "/api/vehicles/{vin}/trips", "viagens", "Viagens registadas pelo gateway, mais recentes primeiro, com totais.",
     "ano=2026 | mes=2026-11 | semana=2026-W46 | dias=30 | de=AAAA-MM-DD&ate=AAAA-MM-DD · limite=200",
     '{"viagens": [{"id": 12, "inicio": "...", "fim": "...", "km": 23.4, "bateria_inicio": 80, "bateria_fim": 75}], "resumo": {"n": 1, "km": 23.4}}'),
    ("GET", "/api/vehicles/{vin}/trips/{id}", "viagens", "Detalhe de uma viagem com o percurso (pontos).", "",
     '{"id": 12, "km": 23.4, "percurso": [{"ts": "...", "lat": 38.7, "lon": -9.1, "velocidade_kmh": 50}]}'),
    ("GET", "/api/vehicles/{vin}/positions", "viagens", "Posições registadas para mapas. agregado=1 (omissão) devolve uma grelha de ~100 m com contagem, para mapa de calor.",
     "mesmos filtros de /trips · agregado=0|1 · so_viagens=1",
     '{"pontos": [{"lat": 38.722, "lon": -9.139, "n": 14}], "agregado": true}'),
    ("POST", "/api/vehicles/{vin}/wake", "acordar", "Acorda o carro (custo extra, gasta bateria).", "",
     '{"response": {"state": "online"}}'),
    ("GET", "/api/fleet/{caminho}", "fleet", "Passthrough de leitura para /api/1/{caminho} da Fleet API (ex.: dx/charging/history).", "os da Tesla",
     "(resposta da Tesla)"),
]


def endpoints_json():
    return [{"metodo": m, "caminho": p, "permissao": perm, "descricao": d, "parametros": par, "exemplo": ex}
            for m, p, perm, d, par, ex in ENDPOINTS]


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
        "funcionalidades": funcionalidades_ativas(),
        "consumo_mes": {k: consumo_mes()[k] for k in ("mes", "total_usd", "credito_usd", "credito_usado_pct")},
    }
