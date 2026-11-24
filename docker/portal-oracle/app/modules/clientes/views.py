"""Rotas do modulo Clientes (paginas, gestao e API do agente)."""
import datetime
import hashlib
import json
import os
import re

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
    bds = {}
    for d in store.list_dbs():
        if d.get("cliente_id"):
            bds.setdefault(int(d["cliente_id"]), []).append(d["name"])
    upd = {}
    with store.tx() as t:
        for r in t.all("SELECT cliente_id, dados FROM cliente_dados WHERE tipo = 'atualizacoes'"):
            try:
                x = json.loads(r["dados"] or "{}")
                upd[int(r["cliente_id"])] = (x.get("total") or 0, x.get("seguranca") or 0, x.get("reinicio"))
            except ValueError:
                pass
    return render_template("clientes/painel.html", clientes=clientes.listar(), familias=clientes.FAMILIAS, alarmes_por=por,
                           bds_por=bds, upd_por=upd)


@bp.route("/cliente/<int:cid>")
@auth.requires(MODULO.acesso)
def detalhe(cid):
    c = clientes.obter(cid) or abort(404)
    from ... import agente_rpc, tarefas_bg, zabbix_api
    aba = request.args.get("aba") or "resumo"
    abas = [("resumo", "Resumo"), ("processos", "Processos"), ("sistema", "Sistema"), ("logs", "Logs"),
            ("atualizacoes", "Atualizações"), ("bds", "Bases de dados")]
    zbx_ok = zabbix_api.configurado()
    if zbx_ok:
        abas.append(("zabbix", "Zabbix"))
    if aba not in dict(abas):
        aba = "resumo"
    todas = store.list_dbs()
    bds = [{"db": d, "via": agente_rpc.estado(d), "resumo": _resumo_bd(d)} for d in todas
           if str(d.get("cliente_id") or "") == str(cid)]
    zbx = None
    if aba == "zabbix" and zbx_ok:
        ch = f"clientes:{cid}:zabbix"
        zbx = tarefas_bg.pedir(ch, f"Zabbix · {c['nome']}", lambda: zabbix_api.resumo_cliente(c), g.user.username, 60,
                               forcar=request.args.get("atualizar") == "1", link=url_for("clientes.detalhe", cid=cid, aba="zabbix"))
        if zbx["a_atualizar"]:
            zbx = tarefas_bg.esperar(ch, 3)
    return render_template("clientes/detalhe.html", c=c, regras=clientes.regras(cid), bds=bds, url_portal=_url_portal(),
                           eventos=clientes.listar_eventos(cliente_id=cid, limite=50), familias=clientes.FAMILIAS,
                           estados=clientes.ESTADOS_EV, alarmes=clientes_alarmes.ativos(cid), aba=aba, abas=abas,
                           dd=clientes.dados(cid), zbx=zbx, zbx_ok=zbx_ok, logs_cfg=clientes.logs_cfg(cid),
                           lf=request.args.get("f") or "sistema", v_portal=clientes.agente_portal()[0],
                           desatualizado=clientes.desatualizado(c),
                           atualizar_pedido=store.get_setting(f"cliente_atualizar:{cid}") or "",
                           livres=[d for d in todas if str(d.get("cliente_id") or "") != str(cid)],
                           modos=agente_rpc.MODOS)


def _resumo_bd(db: dict) -> dict:
    """Estado da BD da cache do modulo Oracle (o mesmo de Oracle -> Bases de dados); pede atualizacao se velho."""
    from ... import tarefas_bg
    try:
        from ..oracle import inspecao
    except Exception:  # noqa: BLE001 - modulo Oracle indisponivel
        return {}
    pw = crypto.decrypt(db["password_enc"]) if db.get("password_enc") else ""
    return tarefas_bg.pedir(f"oracle:{db['id']}:resumo", f"Oracle {db['name']} · estado",
                            lambda: inspecao.resumo(db, pw), g.user.username, 120)


@bp.route("/cliente/<int:cid>/pedir", methods=["POST"])
@auth.requires(MODULO.acesso)
def pedir_dados(cid):
    clientes.obter(cid) or abort(404)
    clientes.pedir_dados(cid, request.form.getlist("tipo"))
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=True)
    flash("Pedido enviado: o agente envia os dados no próximo contacto.", "ok")
    return redirect(url_for("clientes.detalhe", cid=cid, aba=request.form.get("aba") or "resumo"))


@bp.route("/cliente/<int:cid>/logs", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def logs_ficheiros(cid):
    clientes.obter(cid) or abort(404)
    f = request.form
    acao = f.get("acao")
    destino = {"cid": cid, "aba": "logs"}
    try:
        if acao == "scan":
            clientes.pedir_dados(cid, ["scan"])
            flash("Pedido enviado: o agente procura os ficheiros em /var/log no próximo contacto.", "ok")
            destino["scan"] = 1
        elif acao == "adicionar":
            clientes.guardar_log(cid, f.get("pasta"), f.get("ficheiro"), f.get("descricao"), f.get("tag"), f.get("linhas") or 200)
            flash(f"Ficheiro {f.get('ficheiro')} adicionado: as linhas chegam no próximo contacto.", "ok")
        elif acao == "adicionar_scan":
            sel = f.getlist("sel")
            for caminho in sel:
                pasta, _, nome = caminho.rpartition("/")
                clientes.guardar_log(cid, pasta or "/", nome, f.get(f"d:{caminho}") or nome, f.get("tag") or "", f.get("linhas") or 200)
            flash(f"{len(sel)} ficheiro(s) adicionado(s).", "ok" if sel else "error")
        elif acao == "remover":
            clientes.apagar_log(cid, int(f.get("lid") or 0))
            flash("Ficheiro retirado dos logs.", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("clientes.detalhe", **destino))


@bp.route("/cliente/<int:cid>/atualizar-agente", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def atualizar_agente(cid):
    c = clientes.obter(cid) or abort(404)
    clientes.pedir_atualizacao(cid)
    flash(f"{c['nome']}: o agente atualiza-se para {clientes.agente_portal()[0]} no próximo contacto e reinicia.", "ok")
    return redirect(request.form.get("voltar") or url_for("clientes.detalhe", cid=cid))


@bp.route("/ferramentas/atualizar-todos", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def atualizar_todos():
    n = 0
    for c in clientes.listar():
        if clientes.desatualizado(c):
            clientes.pedir_atualizacao(c["id"])
            n += 1
    flash(f"{n} agente(s) desatualizado(s) vão atualizar-se no próximo contacto." if n else "Todos os agentes estão na versão do portal.", "ok")
    return redirect(url_for("clientes.ferramentas"))


@bp.route("/cliente/<int:cid>/bds", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def associar_bd(cid):
    c = clientes.obter(cid) or abort(404)
    f = request.form
    db = store.get_db(int(f.get("db_id") or 0)) or abort(404)
    if f.get("acao") == "remover":
        store.save_db({**db, "cliente_id": None, "agente_modo": "nao"}, None, db["id"])
        flash(f"{db['name']} deixou de estar associada a {c['nome']}.", "ok")
    else:
        modo = f.get("modo") if f.get("modo") in ("auto", "obrigatorio") else "auto"
        store.save_db({**db, "cliente_id": cid, "agente_modo": modo}, None, db["id"])
        flash(f"{db['name']} associada a {c['nome']}.", "ok")
    return redirect(url_for("clientes.detalhe", cid=cid, aba="bds"))


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


@bp.route("/ferramentas")
@auth.login_required
def ferramentas():
    fich = []
    for nome, desc in (("setup.sh", "Instalador: dependências, utilizador SSH, chave do portal, serviço systemd e serviço Oracle"),
                       ("agente-portal.py", "O agente (só Python 3, sem dependências); o setup.sh descarrega-o do portal")):
        p = clientes.ficheiro_agente(nome)
        if not p:
            continue
        dados = open(p, "rb").read()
        v = re.search(rb'^VERSAO = "([^"]+)"', dados, re.M)
        fich.append({"nome": nome, "desc": desc, "tamanho": len(dados), "sha256": hashlib.sha256(dados).hexdigest(),
                     "versao": v.group(1).decode() if v else "",
                     "alterado": datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")})
    try:
        chave = clientes.chave_publica()
    except RuntimeError:
        chave = ""
    v_portal = clientes.agente_portal()[0]
    agentes = [{"c": c, "desatualizado": clientes.desatualizado(c),
                "pedido": store.get_setting(f"cliente_atualizar:{c['id']}") or "",
                "ult": (c.get("info") or {}).get("atualizacao")} for c in clientes.listar()]
    return render_template("clientes/ferramentas.html", fich=fich, url=_url_portal(), chave=chave, agentes=agentes, v_portal=v_portal,
                           inseguro=_cfg()["inseguro"], utilizador=_cfg()["ssh_padrao"])


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
    return jsonify(clientes.sync(c, d.get("info") if isinstance(d.get("info"), dict) else {}, _ip(), link,
                                 d.get("extras") if isinstance(d.get("extras"), dict) else None))


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
