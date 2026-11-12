"""
tesla-key gateway — API interna (porta 8000, só rede Docker).

  /api/...        dados da Tesla para clientes (header X-API-Key, um por cliente)
  /status/v1/...  estado do gateway para outro portal (Authorization: Bearer <token>),
                  só quando a Status API está ativa

Clientes, token da Status API e definições geridos no portal admin ou com `tesla-setup`.
"""
import time

from flask import Flask, abort, g, jsonify, request

import common as c

DEFAULT_ENDPOINTS = "charge_state;climate_state;drive_state;vehicle_state;vehicle_config"

app = Flask(__name__)
c.init_db()
_cache = {}  # (vin, endpoints) -> (timestamp, payload)


# ---------------------------------------------------------------------------
# Autenticação e logs
# ---------------------------------------------------------------------------

@app.before_request
def autenticar():
    g.inicio = time.time()
    g.cliente = None
    p = request.path
    if p == "/api/health":
        return
    if p.startswith("/status/"):
        if c.get_def("status_api_ativa") != "1":
            abort(404)
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not c.validar_token_status(token):
            c.log("WARNING", "status", "token inválido", metodo=request.method, caminho=p,
                  status=401, cliente=request.remote_addr)
            abort(401, "token inválido")
        g.cliente = "status-api"
        return
    if p.startswith("/api/"):
        nome = c.validar_cliente(request.headers.get("X-API-Key", ""))
        if not nome:
            c.log("WARNING", "api", f"chave inválida ou cliente inativo ({request.remote_addr})",
                  metodo=request.method, caminho=p, status=401)
            abort(401, "X-API-Key inválida ou cliente inativo")
        g.cliente = nome
        return
    abort(404)


@app.after_request
def registar(resp):
    if request.path == "/api/health" or resp.status_code == 401:
        return resp
    ms = int((time.time() - getattr(g, "inicio", time.time())) * 1000)
    nivel = "ERROR" if resp.status_code >= 500 else "WARNING" if resp.status_code >= 400 else "INFO"
    origem = "status" if request.path.startswith("/status/") else "api"
    c.log(nivel, origem, "", cliente=getattr(g, "cliente", None), metodo=request.method,
          caminho=request.full_path.rstrip("?"), status=resp.status_code, ms=ms)
    return resp


@app.errorhandler(c.ErroGateway)
def erro_gateway(e):
    return jsonify({"erro": e.mensagem}), e.codigo


@app.errorhandler(400)
@app.errorhandler(401)
@app.errorhandler(404)
@app.errorhandler(405)
@app.errorhandler(500)
def erro(e):
    return jsonify({"erro": getattr(e, "description", str(e))}), getattr(e, "code", 500)


def resposta(r):
    try:
        corpo = r.json()
    except ValueError:
        corpo = {"erro": r.text[:500]}
    return jsonify(corpo), r.status_code


# ---------------------------------------------------------------------------
# API para clientes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.get("/api/token/status")
def token_status():
    return jsonify(c.estado_token())


@app.get("/api/vehicles")
def vehicles():
    return resposta(c.tesla("GET", "vehicles"))


@app.get("/api/vehicles/<vin>")
def vehicle(vin):
    """Estado básico (online/asleep) — não acorda o carro."""
    return resposta(c.tesla("GET", f"vehicles/{vin}"))


@app.get("/api/vehicles/<vin>/data")
def vehicle_data(vin):
    """vehicle_data com cache. ?endpoints=charge_state;drive_state  ?fresh=1 ignora a cache."""
    endpoints = request.args.get("endpoints", DEFAULT_ENDPOINTS)
    chave = (vin, endpoints)
    agora = time.time()
    ttl = int(c.get_def("cache_segundos") or 60)
    if request.args.get("fresh") != "1" and chave in _cache:
        ts, payload = _cache[chave]
        if agora - ts < ttl:
            return jsonify({**payload, "cache": True, "idade_s": int(agora - ts)})
    r = c.tesla("GET", f"vehicles/{vin}/vehicle_data", params={"endpoints": endpoints})
    if r.status_code == 408:
        return jsonify({"erro": "veículo a dormir ou offline", "estado": "asleep"}), 408
    if r.status_code == 200:
        payload = r.json()
        _cache[chave] = (agora, payload)
        return jsonify({**payload, "cache": False})
    return resposta(r)


@app.post("/api/vehicles/<vin>/wake")
def wake(vin):
    """Acorda o carro (custa mais e gasta bateria)."""
    c.log("WARNING", "api", f"wake_up pedido para {vin}", cliente=g.cliente)
    return resposta(c.tesla("POST", f"vehicles/{vin}/wake_up"))


@app.get("/api/fleet/<path:caminho>")
def fleet_get(caminho):
    """Passthrough só de leitura para /api/1/<caminho>."""
    return resposta(c.tesla("GET", caminho, params=request.args))


# ---------------------------------------------------------------------------
# Status API (para outro portal)
# ---------------------------------------------------------------------------

@app.get("/status/v1/health")
def status_health():
    return jsonify({"ok": True, "hora": c.agora_iso()})


@app.get("/status/v1/resumo")
def status_resumo():
    return jsonify(c.resumo())


@app.get("/status/v1/clientes")
def status_clientes():
    return jsonify([{k: x[k] for k in ("nome", "ativo", "ultimo_uso", "pedidos")} for x in c.listar_clientes()])


@app.get("/status/v1/logs")
def status_logs():
    nivel = request.args.get("nivel")
    if nivel and nivel not in ("INFO", "WARNING", "ERROR"):
        abort(400, "nivel: INFO | WARNING | ERROR")
    limite = min(int(request.args.get("limite", 50)), 500)
    return jsonify(c.ler_logs(nivel=nivel, limite=limite))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
