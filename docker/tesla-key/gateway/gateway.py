"""
tesla-key gateway — API interna (porta 8000, só rede Docker).

  /api/...        dados da Tesla para clientes (header X-API-Key, um por cliente)
  /status/v1/...  estado do gateway para outro portal (Authorization: Bearer <token>),
                  só quando a Status API está ativa

Clientes, token da Status API e definições geridos no portal admin ou com `tesla-setup`.
"""
import time
from datetime import datetime, timezone

from flask import Flask, abort, g, jsonify, request, send_file

import common as c
import imagens

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
        nome, perms = c.validar_cliente(request.headers.get("X-API-Key", ""))
        g.perms = set(perms)
        if not nome:
            c.log("WARNING", "api", f"chave inválida ou cliente inativo ({request.remote_addr})",
                  metodo=request.method, caminho=p, status=401)
            abort(401, "X-API-Key inválida ou cliente inativo")
        g.cliente = nome
        return
    abort(404)


@app.after_request
def registar(resp):
    if request.path == "/api/health" or resp.status_code in (401, 403, 304):
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
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(405)
@app.errorhandler(500)
def erro(e):
    return jsonify({"erro": getattr(e, "description", str(e))}), getattr(e, "code", 500)


def requer(perm):
    if perm not in getattr(g, "perms", set()):
        c.log("WARNING", "api", f"sem permissão '{perm}'", cliente=g.cliente,
              metodo=request.method, caminho=request.path, status=403)
        abort(403, f"o cliente não tem a permissão '{perm}'")


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


@app.get("/api/me")
def me():
    """Nome e permissões do cliente (sempre permitido)."""
    return jsonify({
        "cliente": g.cliente,
        "permissoes": [p for p in c.PERMISSOES if p in g.perms],
        "descricao": {p: c.PERMISSOES[p][0] for p in c.PERMISSOES if p in g.perms},
    })


@app.get("/api/token/status")
def token_status():
    requer("token")
    return jsonify(c.estado_token())


@app.get("/api/vehicles")
def vehicles():
    requer("veiculos")
    return resposta(c.tesla("GET", "vehicles"))


@app.get("/api/vehicles/<vin>")
def vehicle(vin):
    """Estado básico (online/asleep) — não acorda o carro."""
    requer("veiculos")
    return resposta(c.tesla("GET", f"vehicles/{vin}"))


def obter_vehicle_data(vin, endpoints, fresh=False):
    """vehicle_data com cache em memória. Devolve (status, payload, da_cache, idade_s)."""
    chave = (vin, endpoints)
    agora = time.time()
    ttl = int(c.get_def("cache_segundos") or 60)
    if not fresh and chave in _cache:
        ts, payload = _cache[chave]
        if agora - ts < ttl:
            return 200, payload, True, int(agora - ts)
    r = c.tesla("GET", f"vehicles/{vin}/vehicle_data", params={"endpoints": endpoints})
    if r.status_code == 200:
        payload = r.json()
        _cache[chave] = (agora, payload)
        return 200, payload, False, 0
    try:
        corpo = r.json()
    except ValueError:
        corpo = {"erro": r.text[:500]}
    return r.status_code, corpo, False, 0


def adormecido():
    return jsonify({"erro": "veículo a dormir ou offline", "estado": "asleep"}), 408


@app.get("/api/vehicles/<vin>/data")
def vehicle_data(vin):
    """vehicle_data com cache. ?endpoints=charge_state;drive_state  ?fresh=1 ignora a cache."""
    pedidas = [x for x in request.args.get("endpoints", DEFAULT_ENDPOINTS).split(";") if x]
    permitidas = [x for x in pedidas if c.SECCOES.get(x, "fleet") in g.perms]
    negadas = [x for x in pedidas if x not in permitidas]
    if not permitidas:
        c.log("WARNING", "api", f"sem permissão para {', '.join(pedidas)}", cliente=g.cliente,
              metodo=request.method, caminho=request.path, status=403)
        abort(403, "o cliente não tem permissão para nenhuma das secções pedidas")
    st, payload, cache, idade = obter_vehicle_data(vin, ";".join(permitidas), request.args.get("fresh") == "1")
    if st == 408:
        return adormecido()
    if st != 200:
        return jsonify(payload), st
    extra = {"cache": cache, "idade_s": idade} if cache else {"cache": False}
    if negadas:
        extra["seccoes_negadas"] = negadas
    return jsonify({**payload, **extra})


@app.get("/api/vehicles/<vin>/location")
def vehicle_location(vin):
    """Localização simplificada (requer o scope vehicle_location). ?fresh=1 ignora a cache."""
    requer("localizacao")
    st, payload, cache, idade = obter_vehicle_data(vin, "location_data;drive_state",
                                                   request.args.get("fresh") == "1")
    if st == 408:
        return adormecido()
    if st != 200:
        return jsonify(payload), st
    d = (payload.get("response") or {}).get("drive_state") or {}
    lat = d.get("latitude", d.get("active_route_latitude"))
    lon = d.get("longitude", d.get("active_route_longitude"))
    if lat is None or lon is None:
        c.log("WARNING", "api", "localização sem coordenadas (falta o scope vehicle_location?)", cliente=g.cliente)
        return jsonify({"erro": "sem coordenadas: confirme o scope vehicle_location e refaça o OAuth"}), 403
    gps_ts = d.get("gps_as_of") or d.get("timestamp")
    if gps_ts and gps_ts > 10**11:  # milissegundos
        gps_ts = gps_ts // 1000
    velocidade = d.get("speed")
    return jsonify({
        "vin": vin,
        "latitude": lat,
        "longitude": lon,
        "direcao": d.get("heading"),
        "velocidade_kmh": round(velocidade * 1.609344) if velocidade is not None else None,
        "mudanca": d.get("shift_state"),
        "gps_em": datetime.fromtimestamp(gps_ts, timezone.utc).isoformat() if gps_ts else None,
        "mapa": f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=17/{lat}/{lon}",
        "cache": cache,
        **({"idade_s": idade} if cache else {}),
    })


@app.get("/api/vehicles/<vin>/image")
def vehicle_image(vin):
    """Imagem do modelo (render do configurador Tesla), guardada em disco.
    ?view=STUD_3QTR|STUD_SIDE|STUD_REAR|STUD_FRONT|STUD_SEAT  ?size=800  ?fresh=1  ?formato=url"""
    requer("imagem")
    view = request.args.get("view", "STUD_3QTR").upper()
    if view not in imagens.VIEWS:
        abort(400, "view: " + " | ".join(imagens.VIEWS))
    size = max(200, min(int(request.args.get("size", 800)), 2048))
    fresh = request.args.get("fresh") == "1"

    if request.args.get("formato") != "url" and not fresh:
        f = imagens.do_disco(vin, view, size)
        if f:
            return send_file(f, mimetype=imagens.mimetype(f), max_age=86400)

    conf = c.ler_conf()
    vcfg = None
    if not conf.get("TESLA_IMAGE_OPTIONS"):
        st, payload, _, _ = obter_vehicle_data(vin, "vehicle_config", False)
        if st == 408:
            return adormecido()
        if st != 200:
            return jsonify(payload), st
        vcfg = (payload.get("response") or {}).get("vehicle_config") or {}
    url, origem = imagens.url_compositor(conf, vcfg, view, size)
    if request.args.get("formato") == "url":
        return jsonify({"url": url, "origem": origem, "view": view, "size": size})
    f = imagens.descarregar(url, vin, view, size)
    c.log("INFO", "api", f"imagem gerada ({origem}, {view}, {size})", cliente=g.cliente)
    return send_file(f, mimetype=imagens.mimetype(f), max_age=86400)


@app.post("/api/vehicles/<vin>/wake")
def wake(vin):
    """Acorda o carro (custa mais e gasta bateria)."""
    requer("acordar")
    c.log("WARNING", "api", f"wake_up pedido para {vin}", cliente=g.cliente)
    return resposta(c.tesla("POST", f"vehicles/{vin}/wake_up"))


@app.get("/api/fleet/<path:caminho>")
def fleet_get(caminho):
    """Passthrough só de leitura para /api/1/<caminho>."""
    requer("fleet")
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
    return jsonify([{**{k: x[k] for k in ("nome", "ativo", "ultimo_uso", "pedidos")},
                     "permissoes": [p for p in (x["permissoes"] or "").split(",") if p]}
                    for x in c.listar_clientes()])


@app.get("/status/v1/logs")
def status_logs():
    nivel = request.args.get("nivel")
    if nivel and nivel not in ("INFO", "WARNING", "ERROR"):
        abort(400, "nivel: INFO | WARNING | ERROR")
    limite = min(int(request.args.get("limite", 50)), 500)
    return jsonify(c.ler_logs(nivel=nivel, limite=limite))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
