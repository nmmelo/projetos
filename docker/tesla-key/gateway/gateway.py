"""
tesla-key gateway — API interna (porta 8000, só rede Docker).

  /api/...        dados da Tesla para clientes (header X-API-Key, um por cliente)
  /status/v1/...  estado do gateway para outro portal (Authorization: Bearer <token>),
                  só quando a Status API está ativa

Clientes, token da Status API e definições geridos no portal admin ou com `tesla-setup`.
"""
import json
import os
import re
import time
from datetime import datetime, timezone

from flask import Flask, abort, g, jsonify, request, send_file
from markupsafe import escape
from werkzeug.exceptions import HTTPException

import common as c
import imagens

DEFAULT_ENDPOINTS = "charge_state;climate_state;drive_state;vehicle_state;vehicle_config"

app = Flask(__name__)
c.init_db()
_cache = {}  # (vin, endpoints) -> (timestamp, payload)


# ---------------------------------------------------------------------------
# Autenticação e logs
# ---------------------------------------------------------------------------

# Caminhos sem chave (documentação e verificação)
PUBLICOS = ("/api/health", "/api/endpoints", "/docs", "/docs/", "/openapi.yaml", "/api/docs", "/API.md")
DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")


@app.before_request
def autenticar():
    g.inicio = time.time()
    g.cliente = None
    p = request.path
    if p in PUBLICOS:
        return
    if p.startswith("/status/"):
        if c.get_def("status_api_ativa") != "1":
            abort(404)
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not c.validar_token_status(token):
            abort(401, f"token da Status API inválido ou em falta ({request.remote_addr})")
        g.cliente = "status-api"
        return
    if p.startswith("/api/"):
        nome, perms = c.validar_cliente(request.headers.get("X-API-Key", ""))
        g.perms = set(perms)
        if not nome:
            pref = (request.headers.get("X-API-Key", "") or "")[:9]
            abort(401, f"X-API-Key {'em falta' if not pref else 'inválida (' + pref + '…)'} ou cliente inativo; IP {request.remote_addr}")
        g.cliente = nome
        return
    abort(404, f"caminho inexistente: {p}")


def falha(codigo, motivo, **extra):
    """Resposta de erro: regista o motivo nos logs (ver Definições → Logs → API) e devolve ao cliente
    só o código e a referência (ou também o motivo, se 'erros_detalhados' estiver ativo)."""
    ms = int((time.time() - getattr(g, "inicio", time.time())) * 1000)
    origem = "status" if request.path.startswith("/status/") else "api"
    nivel = "ERROR" if codigo >= 500 else "INFO" if codigo == 408 else "WARNING"
    ref = c.log(nivel, origem, motivo, cliente=getattr(g, "cliente", None) or request.remote_addr,
                metodo=request.method, caminho=request.full_path.rstrip("?"), status=codigo, ms=ms)
    g.registado = True
    corpo = {"codigo": codigo, "erro": c.CODIGOS_HTTP.get(codigo, ("Erro", ""))[0], "ref": ref}
    if c.get_def("erros_detalhados") == "1":
        corpo["detalhe"] = motivo
    corpo.update(extra)
    return jsonify(corpo), codigo


@app.after_request
def registar(resp):
    if request.path in PUBLICOS or resp.status_code == 304 or getattr(g, "registado", False):
        return resp
    ms = int((time.time() - getattr(g, "inicio", time.time())) * 1000)
    nivel = "ERROR" if resp.status_code >= 500 else "WARNING" if resp.status_code >= 400 else "INFO"
    origem = "status" if request.path.startswith("/status/") else "api"
    c.log(nivel, origem, "", cliente=getattr(g, "cliente", None), metodo=request.method,
          caminho=request.full_path.rstrip("?"), status=resp.status_code, ms=ms)
    return resp


@app.errorhandler(c.ErroGateway)
def erro_gateway(e):
    return falha(e.codigo, e.mensagem)


@app.errorhandler(400)
@app.errorhandler(401)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(405)
@app.errorhandler(500)
def erro(e):
    codigo = getattr(e, "code", 500) or 500
    motivo = getattr(e, "description", str(e))
    if codigo == 405:
        motivo = f"método {request.method} não permitido em {request.path}"
    elif codigo == 404 and request.url_rule is None:
        motivo = f"caminho inexistente: {request.method} {request.path} (ver GET /api/endpoints)"
    return falha(codigo, motivo)


@app.errorhandler(Exception)
def erro_inesperado(e):
    if isinstance(e, HTTPException):
        return falha(e.code or 500, e.description or str(e))
    return falha(500, f"{type(e).__name__}: {e}")


def requer(perm):
    if perm not in c.funcionalidades_ativas():
        abort(403, f"funcionalidade '{perm}' desativada no gateway (Definições → Geral → Funcionalidades ativas)")
    if perm not in getattr(g, "perms", set()):
        abort(403, f"o cliente '{g.cliente}' não tem a permissão '{perm}' (Clientes → Permissões)")


def motivo_tesla(st, corpo):
    texto = corpo if isinstance(corpo, str) else (corpo.get("error") or corpo.get("erro") or corpo.get("error_description")
                                                  or json.dumps(corpo, ensure_ascii=False)) if isinstance(corpo, dict) else str(corpo)
    return f"Tesla respondeu {st}: {str(texto)[:400]}"


def resposta(r):
    try:
        corpo = r.json()
    except ValueError:
        corpo = r.text[:500]
    if r.status_code >= 400:
        if r.status_code == 408:
            return adormecido()
        return falha(r.status_code, motivo_tesla(r.status_code, corpo))
    return jsonify(corpo), r.status_code


# ---------------------------------------------------------------------------
# API para clientes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.get("/openapi.yaml")
def openapi_yaml():
    """Especificação OpenAPI (sem chave)."""
    f = os.path.join(DOCS_DIR, "openapi.yaml")
    if not os.path.exists(f):
        abort(404, "openapi.yaml não incluído na imagem")
    return send_file(f, mimetype="application/yaml")


@app.get("/API.md")
def api_md():
    f = os.path.join(DOCS_DIR, "API.md")
    if not os.path.exists(f):
        abort(404, "API.md não incluído na imagem")
    return send_file(f, mimetype="text/markdown")


@app.get("/docs")
@app.get("/docs/")
@app.get("/api/docs")
def docs():
    """Página simples com a referência dos endpoints (sem chave)."""
    linhas = "".join(
        f"<tr><td><b class='m {e['metodo']}'>{e['metodo']}</b></td><td><code>{escape(e['caminho'])}</code></td>"
        f"<td>{escape(e['permissao'] or '—')}</td><td>{escape(e['descricao'])}"
        f"{'<br><small>Parâmetros: <code>' + escape(e['parametros']) + '</code></small>' if e['parametros'] else ''}</td></tr>"
        for e in c.endpoints_json())
    html = f"""<!doctype html><html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gateway Tesla API · referência</title><style>
body{{font:14px/1.5 system-ui,sans-serif;margin:0;background:#f5f6f8;color:#1d2433}}main{{max-width:1000px;margin:24px auto;padding:0 12px}}
.card{{background:#fff;border:1px solid #e3e6ec;border-radius:8px;padding:16px;margin-bottom:14px}}
table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;padding:7px;border-bottom:1px solid #e3e6ec;vertical-align:top;overflow-wrap:anywhere}}
code{{background:#eceef2;padding:1px 5px;border-radius:4px}}.m{{color:#fff;padding:2px 6px;border-radius:4px;font:700 12px monospace}}
.GET{{background:#1f8a4c}}.POST{{background:#b7791f}}@media(prefers-color-scheme:dark){{body{{background:#0f1218;color:#e6e9f0}}
.card{{background:#171b23;border-color:#2a303c}}td,th{{border-color:#2a303c}}code{{background:#262c38}}}}</style></head><body><main>
<div class="card"><h1 style="font-size:20px;margin:0 0 6px">Gateway Tesla API</h1>
<p>URL base: <code>http://tesla-key:8000</code> · Autenticação: header <code>X-API-Key: tk_...</code> ·
Erros: <code>{{"codigo": 403, "erro": "Forbidden", "ref": 1234}}</code></p>
<p>Especificação: <a href="/openapi.yaml">openapi.yaml</a> · Referência completa: <a href="/API.md">API.md</a> ·
JSON: <a href="/api/endpoints">/api/endpoints</a></p></div>
<div class="card"><table><tr><th>Método</th><th>Caminho</th><th>Permissão</th><th>Descrição</th></tr>{linhas}</table></div>
</main></body></html>"""
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.get("/api/endpoints")
def endpoints():
    """Referência dos endpoints (sempre permitido)."""
    return jsonify(c.endpoints_json())


def filtros_periodo():
    a = request.args
    for p in ("ano", "mes", "semana", "dias"):
        if a.get(p):
            return c.intervalo_periodo(p, a.get(p))
    return c.intervalo_periodo(de=a.get("de"), ate=a.get("ate"))


@app.get("/api/vehicles/<vin>/trips")
def trips(vin):
    requer("viagens")
    de, ate = filtros_periodo()
    limite = min(int(request.args.get("limite", 200)), 5000)
    return jsonify({"viagens": c.listar_viagens(vin, de, ate, limite),
                    "resumo": c.resumo_viagens(vin, de, ate), "de": de, "ate": ate})


@app.get("/api/vehicles/<vin>/trips/<int:viagem_id>")
def trip(vin, viagem_id):
    requer("viagens")
    return jsonify(c.obter_viagem(viagem_id, vin))


@app.get("/api/vehicles/<vin>/charges")
def charges(vin):
    requer("carregamentos")
    de, ate = filtros_periodo()
    origem = request.args.get("origem") or None
    if origem and origem not in ("leituras", "inferido", "supercharger"):
        abort(400, "origem: leituras | inferido | supercharger")
    limite = min(int(request.args.get("limite", 200)), 5000)
    return jsonify({"carregamentos": c.listar_carregamentos(vin, de, ate, limite, origem),
                    "resumo": c.resumo_carregamentos(vin, de, ate), "de": de, "ate": ate})


@app.get("/api/vehicles/<vin>/charges/<int:cid>")
def charge(vin, cid):
    requer("carregamentos")
    return jsonify(c.obter_carregamento(cid, vin))


@app.get("/api/vehicles/<vin>/positions")
def positions(vin):
    requer("viagens")
    de, ate = filtros_periodo()
    agregado = request.args.get("agregado", "1") != "0"
    pts = c.posicoes(vin, de, ate, agregado=agregado, so_viagens=request.args.get("so_viagens") == "1")
    return jsonify({"pontos": pts, "agregado": agregado, "de": de, "ate": ate})


@app.get("/api/me/consumo")
def me_consumo():
    """Consumo estimado só deste cliente (sempre permitido)."""
    return jsonify(c.consumo_cliente(g.cliente, request.args.get("mes")))


@app.get("/api/consumo")
def consumo():
    requer("consumo")
    mes = request.args.get("mes")
    if mes and not re.fullmatch(r"\d{4}-\d{2}", mes):
        abort(400, "mes: AAAA-MM")
    return jsonify(c.consumo_mes(mes))


@app.get("/api/recolha")
def recolha():
    requer("viagens")
    return jsonify(c.estado_recolha())


@app.get("/api/me")
def me():
    """Nome e permissões do cliente (sempre permitido)."""
    ativas = set(c.funcionalidades_ativas())
    efetivas = [p for p in c.PERMISSOES if p in g.perms and p in ativas]
    return jsonify({
        "cliente": g.cliente,
        "permissoes": efetivas,
        "desativadas_no_gateway": [p for p in c.PERMISSOES if p in g.perms and p not in ativas],
        "descricao": {p: c.PERMISSOES[p][0] for p in efetivas},
    })


@app.get("/api/token/status")
def token_status():
    requer("token")
    return jsonify(c.estado_token())


def com_cache(chave, caminho):
    """GET à Tesla com cache (cache_veiculos_segundos) só para respostas 200."""
    ttl = int(c.get_def("cache_veiculos_segundos") or 60)
    agora = time.time()
    if request.args.get("fresh") != "1" and chave in _cache:
        ts, payload = _cache[chave]
        if agora - ts < ttl:
            return jsonify(payload)
    r = c.tesla("GET", caminho)
    if r.status_code == 200:
        _cache[chave] = (agora, r.json())
    return resposta(r)


@app.get("/api/vehicles")
def vehicles():
    requer("veiculos")
    return com_cache(("vehicles",), "vehicles")


@app.get("/api/vehicles/<vin>")
def vehicle(vin):
    """Estado básico (online/asleep) — não acorda o carro."""
    requer("veiculos")
    return com_cache(("vehicle", vin), f"vehicles/{vin}")


def obter_vehicle_data(vin, endpoints, fresh=False, ttl=None):
    """vehicle_data com cache em memória. Devolve (status, payload, da_cache, idade_s)."""
    chave = (vin, endpoints)
    agora = time.time()
    ttl = int(c.get_def("cache_segundos") or 60) if ttl is None else ttl
    if not fresh and chave in _cache:
        ts, payload = _cache[chave]
        if agora - ts < ttl:
            return 200, payload, True, int(agora - ts)
    r = c.tesla("GET", f"vehicles/{vin}/vehicle_data", params={"endpoints": endpoints})
    if r.status_code == 200:
        payload = r.json()
        _cache[chave] = (agora, payload)
        return 200, payload, False, 0
    if r.status_code == 408:
        return 408, {}, False, 0
    try:
        corpo = r.json()
    except ValueError:
        corpo = {"erro": r.text[:500]}
    return r.status_code, corpo, False, 0


def adormecido():
    return falha(408, "veículo a dormir ou offline (a Tesla devolveu 408)", estado="asleep")


@app.get("/api/vehicles/<vin>/data")
def vehicle_data(vin):
    """vehicle_data com cache. ?endpoints=charge_state;drive_state  ?fresh=1 ignora a cache."""
    omissao = c.get_def("data_endpoints_omissao") or DEFAULT_ENDPOINTS
    pedidas = [x for x in request.args.get("endpoints", omissao).split(";") if x]
    ativas = set(c.funcionalidades_ativas())
    permitidas = [x for x in pedidas if c.SECCOES.get(x, "fleet") in g.perms
                  and c.SECCOES.get(x, "fleet") in ativas]
    negadas = [x for x in pedidas if x not in permitidas]
    if not permitidas:
        abort(403, f"o cliente '{g.cliente}' não tem permissão para nenhuma das secções pedidas: {', '.join(pedidas)}")
    st, payload, cache, idade = obter_vehicle_data(vin, ";".join(permitidas), request.args.get("fresh") == "1")
    if st == 408:
        return adormecido()
    if st != 200:
        return falha(st, motivo_tesla(st, payload))
    extra = {"cache": cache, "idade_s": idade} if cache else {"cache": False}
    if negadas:
        extra["seccoes_negadas"] = negadas
    return jsonify({**payload, **extra})


@app.get("/api/vehicles/<vin>/location")
def vehicle_location(vin):
    """Localização simplificada (requer o scope vehicle_location). ?fresh=1 ignora a cache."""
    requer("localizacao")
    st, payload, cache, idade = obter_vehicle_data(vin, "location_data;drive_state",
                                                   request.args.get("fresh") == "1",
                                                   ttl=int(c.get_def("cache_localizacao_segundos") or 60))
    if st == 408:
        return adormecido()
    if st != 200:
        return falha(st, motivo_tesla(st, payload))
    d = (payload.get("response") or {}).get("drive_state") or {}
    lat = d.get("latitude", d.get("active_route_latitude"))
    lon = d.get("longitude", d.get("active_route_longitude"))
    if lat is None or lon is None:
        return falha(403, "a Tesla não devolveu coordenadas: falta o scope vehicle_location no token (ativar na aplicação e refazer o login)")
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
            return send_file(f, mimetype=imagens.mimetype(f), max_age=300)

    conf = c.ler_conf()
    vcfg = None
    if not conf.get("TESLA_IMAGE_OPTIONS"):
        st, payload, _, _ = obter_vehicle_data(vin, "vehicle_config", False)
        if st == 408:
            return adormecido()
        if st != 200:
            return falha(st, motivo_tesla(st, payload))
        vcfg = (payload.get("response") or {}).get("vehicle_config") or {}
    url, origem = imagens.url_compositor(conf, vcfg, view, size)
    if request.args.get("formato") == "url":
        return jsonify({"url": url, "origem": origem, "view": view, "size": size})
    f = imagens.descarregar(url, vin, view, size)
    c.log("INFO", "api", f"imagem gerada ({origem}, {view}, {size})", cliente=g.cliente)
    return send_file(f, mimetype=imagens.mimetype(f), max_age=300)


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
