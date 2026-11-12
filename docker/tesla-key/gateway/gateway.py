"""
tesla-key gateway — API interna para outros contentores lerem dados da Tesla Fleet API.

- Lê a configuração de /opt/tesla/tesla.env e os tokens de /opt/tesla/tokens.json
  (criados com `tesla-setup token`).
- Renova o access token automaticamente (e guarda o novo refresh token).
- Autenticação por header X-API-Key (TESLA_GATEWAY_API_KEY).
- Cache em memória para vehicle_data (TESLA_GATEWAY_CACHE, segundos).

Só deve estar acessível na rede Docker interna (porta 8000 sem publish).
"""
import base64
import fcntl
import json
import os
import threading
import time
from datetime import datetime, timezone

import requests
from flask import Flask, abort, jsonify, request

BASE = os.environ.get("TESLA_BASE", "/opt/tesla")
CONF_FILE = os.path.join(BASE, "tesla.env")
TOKENS_FILE = os.path.join(BASE, "tokens.json")
LOCK_FILE = os.path.join(BASE, ".tokens.lock")

API_KEY = os.environ.get("TESLA_GATEWAY_API_KEY", "")
CACHE_SECONDS = int(os.environ.get("TESLA_GATEWAY_CACHE", "60"))
TIMEOUT = 30

AUDIENCES = {
    "eu": "https://fleet-api.prd.eu.vn.cloud.tesla.com",
    "na": "https://fleet-api.prd.na.vn.cloud.tesla.com",
}
DEFAULT_TOKEN_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
DEFAULT_ENDPOINTS = "charge_state;climate_state;drive_state;vehicle_state;vehicle_config"

app = Flask(__name__)
_lock = threading.Lock()
_cache = {}  # (vin, endpoints) -> (timestamp, payload)


# ---------------------------------------------------------------------------
# Configuração e tokens
# ---------------------------------------------------------------------------

def ler_conf():
    conf = {}
    if not os.path.exists(CONF_FILE):
        abort(500, f"falta {CONF_FILE}")
    with open(CONF_FILE, encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            k, v = linha.split("=", 1)
            conf[k.strip()] = v.strip().strip('"').strip("'")
    regiao = conf.get("TESLA_REGION", "eu")
    conf["AUDIENCE"] = AUDIENCES.get(regiao, AUDIENCES["eu"])
    conf.setdefault("TESLA_TOKEN_URL", DEFAULT_TOKEN_URL)
    if not conf.get("TESLA_TOKEN_URL"):
        conf["TESLA_TOKEN_URL"] = DEFAULT_TOKEN_URL
    return conf


def jwt_exp(token):
    """Data de expiração (epoch) lida do JWT; 0 se não conseguir."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return int(json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0))
    except Exception:
        return 0


def ler_tokens():
    if not os.path.exists(TOKENS_FILE):
        abort(503, "sem tokens: correr `tesla-setup url` e `tesla-setup token <code>` no contentor")
    with open(TOKENS_FILE, encoding="utf-8") as f:
        return json.load(f)


def guardar_tokens(dados):
    dados["obtido_em"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = TOKENS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, TOKENS_FILE)


def access_token(forcar=False):
    """Devolve um access token válido, renovando se faltarem < 5 min."""
    with _lock, open(LOCK_FILE, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        tokens = ler_tokens()
        at = tokens.get("access_token", "")
        if not forcar and jwt_exp(at) - time.time() > 300:
            return at

        conf = ler_conf()
        rt = tokens.get("refresh_token")
        if not rt:
            abort(503, "tokens.json sem refresh_token (falta o scope offline_access?)")
        r = requests.post(conf["TESLA_TOKEN_URL"], data={
            "grant_type": "refresh_token",
            "client_id": conf.get("TESLA_CLIENT_ID", ""),
            "refresh_token": rt,
        }, timeout=TIMEOUT)
        if r.status_code != 200:
            abort(502, f"falhou a renovação do token: {r.status_code} {r.text[:300]}")
        novos = r.json()
        # a Tesla roda o refresh token: guardar sempre o novo
        tokens.update(novos)
        guardar_tokens(tokens)
        app.logger.info("access token renovado")
        return tokens["access_token"]


def tesla(metodo, caminho, **kw):
    """Chamada à Fleet API com renovação automática em caso de 401."""
    conf = ler_conf()
    url = f"{conf['AUDIENCE']}/api/1/{caminho.lstrip('/')}"
    for tentativa in (0, 1):
        h = {"Authorization": f"Bearer {access_token(forcar=tentativa == 1)}"}
        r = requests.request(metodo, url, headers=h, timeout=TIMEOUT, **kw)
        if r.status_code != 401:
            return r
    return r


def resposta(r):
    try:
        corpo = r.json()
    except ValueError:
        corpo = {"erro": r.text[:500]}
    return jsonify(corpo), r.status_code


# ---------------------------------------------------------------------------
# Segurança
# ---------------------------------------------------------------------------

@app.before_request
def autenticar():
    if request.path in ("/api/health",):
        return
    if not API_KEY:
        abort(500, "TESLA_GATEWAY_API_KEY não definida")
    if request.headers.get("X-API-Key") != API_KEY:
        abort(401, "X-API-Key inválida")


@app.errorhandler(400)
@app.errorhandler(401)
@app.errorhandler(404)
@app.errorhandler(500)
@app.errorhandler(502)
@app.errorhandler(503)
def erro(e):
    return jsonify({"erro": getattr(e, "description", str(e))}), getattr(e, "code", 500)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.get("/api/token/status")
def token_status():
    tokens = ler_tokens()
    exp = jwt_exp(tokens.get("access_token", ""))
    return jsonify({
        "obtido_em": tokens.get("obtido_em"),
        "expira_em": datetime.fromtimestamp(exp, timezone.utc).isoformat() if exp else None,
        "segundos_restantes": max(0, int(exp - time.time())) if exp else None,
        "tem_refresh_token": bool(tokens.get("refresh_token")),
    })


@app.post("/api/token/refresh")
def token_refresh():
    access_token(forcar=True)
    return token_status()


@app.get("/api/vehicles")
def vehicles():
    return resposta(tesla("GET", "vehicles"))


@app.get("/api/vehicles/<vin>")
def vehicle(vin):
    """Estado básico (online/asleep) — não acorda o carro."""
    return resposta(tesla("GET", f"vehicles/{vin}"))


@app.get("/api/vehicles/<vin>/data")
def vehicle_data(vin):
    """vehicle_data com cache. ?endpoints=charge_state;drive_state  ?fresh=1 ignora a cache."""
    endpoints = request.args.get("endpoints", DEFAULT_ENDPOINTS)
    chave = (vin, endpoints)
    agora = time.time()
    if request.args.get("fresh") != "1" and chave in _cache:
        ts, payload = _cache[chave]
        if agora - ts < CACHE_SECONDS:
            return jsonify({**payload, "cache": True, "idade_s": int(agora - ts)})

    r = tesla("GET", f"vehicles/{vin}/vehicle_data", params={"endpoints": endpoints})
    if r.status_code == 408:
        return jsonify({"erro": "veículo a dormir ou offline", "estado": "asleep"}), 408
    if r.status_code == 200:
        payload = r.json()
        _cache[chave] = (agora, payload)
        return jsonify({**payload, "cache": False})
    return resposta(r)


@app.post("/api/vehicles/<vin>/wake")
def wake(vin):
    """Acorda o carro (custa mais e gasta bateria — usar com moderação)."""
    return resposta(tesla("POST", f"vehicles/{vin}/wake_up"))


@app.get("/api/fleet/<path:caminho>")
def fleet_get(caminho):
    """Passthrough só de leitura para outros endpoints GET da Fleet API (/api/1/<caminho>)."""
    return resposta(tesla("GET", caminho, params=request.args))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
