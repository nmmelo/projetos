"""Rotas do modulo Clientes (paginas, gestao e API do agente)."""
from flask import (Response, abort, flash, g, jsonify, redirect, render_template, request, send_file,
                   url_for)

from ... import auth, clientes, clientes_alarmes, crypto, store
from . import bp, MODULO


def _url_portal() -> str:
    u = (store.get_setting("clientes_url_portal") or "").strip().rstrip("/")
    if u:
        return u
    proto = request.headers.get("X-Forwarded-Proto") or request.scheme
    return f"{proto}://{request.host}"


def _cfg() -> dict:
    return {"url": _url_portal(), "url_guardado": store.get_setting("clientes_url_portal") or "",
            "inseguro": store.get_setting("clientes_tls_inseguro") == "1",
            "ssh_padrao": store.get_setting("clientes_ssh_utilizador") or "ansible",
            "zbx_ativa": store.get_setting("zabbix_api_ativa") == "1", "zbx_token": _zbx_token(),
            "zbx_url": store.get_setting("zabbix_url") or "", "zbx_api_guardado": bool(store.get_setting("zabbix_api_token"))}


def _zbx_token() -> str:
    v = store.get_setting("zabbix_token_portal") or ""
    try:
        return crypto.decrypt(v) if v else ""
    except RuntimeError:
        return ""


# ============================================================ paginas
@bp.route("/")
@auth.login_required
def index():
    return redirect(url_for("clientes.painel"))


@bp.route("/painel")
@auth.requires(MODULO.acesso)
def painel():
    por = {}
    for e in clientes_alarmes.ativos():
        por.setdefault(e["cliente_id"], []).append(e)
    return render_template("clientes/painel.html", clientes=clientes.listar(), familias=clientes.FAMILIAS, alarmes_por=por)


@bp.route("/cliente/<int:cid>")
@auth.requires(MODULO.acesso)
def detalhe(cid):
    c = clientes.obter(cid) or abort(404)
    from ... import agente_rpc
    bds = [{"db": d, "via": agente_rpc.estado(d)} for d in store.list_dbs() if str(d.get("cliente_id") or "") == str(cid)]
    return render_template("clientes/detalhe.html", c=c, regras=clientes.regras(cid), bds=bds, url_portal=_url_portal(),
                           eventos=clientes.listar_eventos(cliente_id=cid, limite=50), familias=clientes.FAMILIAS,
                           estados=clientes.ESTADOS_EV, alarmes=clientes_alarmes.ativos(cid))


@bp.route("/regras", methods=["GET", "POST"])
@auth.requires(MODULO.acesso)
def regras():
    if request.method == "POST":
        if not g.user.can("clientes.regras"):
            abort(403)
        try:
            rid = clientes.guardar_regra(request.form, g.user.username, request.form.get("id") or None)
            r = clientes.obter_regra(rid)
            flash(f"Regra guardada: {r['nome']} (id {r['codigo']}).", "ok")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("clientes.regras"))
    return render_template("clientes/regras.html", regras=clientes.regras(), clientes=clientes.listar(),
                           pode=g.user.can("clientes.regras"))


@bp.route("/regras/<int:rid>/apagar", methods=["POST"])
@auth.requires("clientes.regras")
def regra_apagar(rid):
    clientes.apagar_regra(rid)
    flash("Regra apagada.", "ok")
    return redirect(url_for("clientes.regras"))


@bp.route("/eventos")
@auth.requires(MODULO.acesso)
def eventos():
    a = request.args
    return render_template("clientes/eventos.html", clientes=clientes.listar(), regras=clientes.regras(),
                           estados=clientes.ESTADOS_EV, f=a,
                           eventos=clientes.listar_eventos(a.get("cliente") or None, a.get("regra", ""),
                                                           a.get("estado", ""), a.get("q", "")))


@bp.route("/alarmes", methods=["GET", "POST"])
@auth.requires(MODULO.acesso)
def alarmes():
    A = clientes_alarmes
    if request.method == "POST":
        if not g.user.can("clientes.alarmes"):
            abort(403)
        try:
            A.guardar(request.form, g.user.username, request.form.get("id") or None)
            flash("Alarme guardado; é avaliado na próxima sincronização de cada cliente.", "ok")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("clientes.alarmes"))
    from ... import modulos
    return render_template("clientes/alarmes.html", alarmes=A.listar(), ativos=A.ativos(), clientes=clientes.listar(),
                           metricas=A.METRICAS, severidades=A.SEVERIDADES, destinos=A.DESTINOS, zbx=A.CHAVES_ZABBIX,
                           pode=g.user.can("clientes.alarmes"),
                           smtp_ok="smtp" in modulos.ativos() and store.get_setting("smtp_ativo") == "1")


@bp.route("/alarmes/<int:aid>/apagar", methods=["POST"])
@auth.requires("clientes.alarmes")
def alarme_apagar(aid):
    clientes_alarmes.apagar(aid)
    flash("Alarme apagado.", "ok")
    return redirect(url_for("clientes.alarmes"))


# ============================================================ gestao (Configuracao > Clientes)
@bp.route("/gerir")
@auth.requires(MODULO.acesso, "clientes.gerir")
def gerir():
    try:
        chave, erro_chave = clientes.chave_publica(), ""
    except RuntimeError as exc:
        chave, erro_chave = "", str(exc)
    return render_template("clientes/gerir.html", clientes=clientes.listar(), familias=clientes.FAMILIAS,
                           chave=chave, erro_chave=erro_chave, cfg=_cfg(), tab="portal", sub="clientes")


@bp.route("/gerir/definicoes", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def gerir_definicoes():
    f = request.form
    url = (f.get("url") or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        flash("O endereço do portal começa por http:// ou https://.", "error")
    elif f.get("ssh") and not clientes.UTIL_RE.match(f["ssh"].strip()):
        flash("Utilizador SSH inválido.", "error")
    else:
        store.save_settings({"clientes_url_portal": url, "clientes_tls_inseguro": "1" if f.get("inseguro") else "0",
                             "clientes_ssh_utilizador": (f.get("ssh") or "ansible").strip()})
        flash("Definições guardadas.", "ok")
    return redirect(url_for("clientes.gerir"))


@bp.route("/gerir/zabbix", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def gerir_zabbix():
    import secrets
    f = request.form
    novos = {"zabbix_api_ativa": "1" if f.get("ativa") else "0", "zabbix_url": (f.get("url") or "").strip()}
    if novos["zabbix_url"] and not novos["zabbix_url"].startswith(("http://", "https://")):
        flash("O endereço do Zabbix começa por http:// ou https://.", "error")
        return redirect(url_for("clientes.gerir") + "#zabbix")
    if f.get("api_token"):
        novos["zabbix_api_token"] = crypto.encrypt(f["api_token"].strip())
    if f.get("novo_token") or (novos["zabbix_api_ativa"] == "1" and not _zbx_token()):
        novos["zabbix_token_portal"] = crypto.encrypt(secrets.token_urlsafe(32))
    store.save_settings(novos)
    flash("Integração Zabbix guardada.", "ok")
    return redirect(url_for("clientes.gerir") + "#zabbix")


@bp.route("/gerir/novo", methods=["GET", "POST"])
@bp.route("/gerir/<int:cid>", methods=["GET", "POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def gerir_cliente(cid=None):
    c = clientes.obter(cid) if cid else None
    if cid and not c:
        abort(404)
    if request.method == "POST":
        try:
            novo = clientes.guardar(request.form, g.user.username, cid)
            flash("Cliente guardado." if cid else "Cliente criado: instale o agente com os comandos abaixo.", "ok")
            return redirect(url_for("clientes.gerir_cliente", cid=novo))
        except ValueError as exc:
            flash(str(exc), "error")
            c = dict(c or {}, **request.form.to_dict())
    cfg = _cfg()
    return render_template("clientes/cliente_form.html", c=c, cfg=cfg, familias=clientes.FAMILIAS,
                           token=clientes.token(c) if c and c.get("id") else "",
                           fp=clientes.impressao_host(c["endereco"], int(c.get("ssh_porta") or 22)) if c and c.get("id") else "",
                           tab="portal", sub="clientes")


@bp.route("/gerir/<int:cid>/testar", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def gerir_testar(cid):
    c = clientes.obter(cid) or abort(404)
    passos = clientes.testar(c)
    return jsonify(ok=all(p["ok"] is not False for p in passos), passos=passos)


@bp.route("/gerir/<int:cid>/token", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def gerir_token(cid):
    clientes.obter(cid) or abort(404)
    clientes.novo_token(cid)
    flash("Token novo gerado. O agente deixa de conseguir sincronizar até correr no cliente: "
          "agente-portal configurar --token <novo>.", "ok")
    return redirect(url_for("clientes.gerir_cliente", cid=cid))


@bp.route("/gerir/<int:cid>/esquecer", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def gerir_esquecer(cid):
    c = clientes.obter(cid) or abort(404)
    out = clientes.esquecer_host(c["endereco"], int(c.get("ssh_porta") or 22))
    flash("Chave do servidor esquecida; o próximo teste aceita a nova. " + out[-200:], "ok")
    return redirect(url_for("clientes.gerir_cliente", cid=cid))


@bp.route("/gerir/<int:cid>/apagar", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def gerir_apagar(cid):
    c = clientes.obter(cid) or abort(404)
    clientes.apagar(cid)
    flash(f"Cliente {c['nome']} apagado (desinstale o agente: sudo bash setup.sh --desinstalar).", "ok")
    return redirect(url_for("clientes.gerir"))


# ============================================================ API do agente (sem sessao; token)
@bp.route("/agente/<nome>")
def agente_ficheiro(nome):
    if nome == "chave.pub":
        try:
            return Response(clientes.chave_publica() + "\n", mimetype="text/plain")
        except RuntimeError as exc:
            return Response(str(exc), status=500, mimetype="text/plain")
    p = clientes.ficheiro_agente(nome) or abort(404)
    return send_file(p, mimetype="text/plain", as_attachment=bool(request.args.get("download")), download_name=nome,
                     max_age=0)


def _agente():
    tk = request.headers.get("Authorization", "")
    return clientes.autenticar(request.headers.get("X-Agente-Id", ""), tk[7:] if tk.startswith("Bearer ") else "")


def _negado():
    r = jsonify(ok=False, error="id do cliente ou token inválido")
    r.status_code = 401
    return r


def _ip() -> str:
    return request.headers.get("X-Real-IP") or request.remote_addr or ""


@bp.route("/agente/api/sync", methods=["POST"])
def agente_sync():
    c = _agente()
    if not c:
        return _negado()
    d = request.get_json(silent=True) or {}
    link = url_for("clientes.detalhe", cid=c["id"])
    return jsonify(clientes.sync(c, d.get("info") if isinstance(d.get("info"), dict) else {}, _ip(), link))


@bp.route("/agente/api/evento", methods=["POST"])
def agente_evento():
    c = _agente()
    if not c:
        return _negado()
    d = request.get_json(silent=True) or {}
    n = clientes.receber_eventos(c, d.get("eventos") if isinstance(d.get("eventos"), list) else [],
                                 url_for("clientes.detalhe", cid=c["id"]))
    return jsonify(ok=True, recebidos=n)


# ============================================================ API para o Zabbix (HTTP agent), token do portal
def _zbx_ok() -> bool:
    if store.get_setting("zabbix_api_ativa") != "1":
        return False
    tk = request.headers.get("Authorization", "")
    tk = tk[7:] if tk.startswith("Bearer ") else request.args.get("token", "")
    esperado = _zbx_token()
    import hmac
    return bool(esperado and tk) and hmac.compare_digest(tk, esperado)


def _zbx_negado():
    r = jsonify(ok=False, error="API Zabbix desligada ou token inválido (Configuração → Clientes → Integração Zabbix)")
    r.status_code = 401
    return r


@bp.route("/api/zabbix/descoberta")
def zbx_descoberta():
    return jsonify(clientes_alarmes.zabbix_descoberta()) if _zbx_ok() else _zbx_negado()


@bp.route("/api/zabbix/metricas")
def zbx_metricas():
    if not _zbx_ok():
        return _zbx_negado()
    nome = request.args.get("cliente", "")
    c = next((x for x in clientes.listar() if nome in (x["nome"], x.get("zabbix_host"), x["codigo"])), None)
    if not c:
        r = jsonify(ok=False, error="cliente desconhecido (?cliente=<nome, host Zabbix ou id>)")
        r.status_code = 404
        return r
    return jsonify(clientes_alarmes.zabbix_metricas(c))


@bp.route("/api/zabbix/alarmes")
def zbx_alarmes():
    return jsonify(clientes_alarmes.zabbix_alarmes()) if _zbx_ok() else _zbx_negado()
