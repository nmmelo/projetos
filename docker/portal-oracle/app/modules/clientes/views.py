"""Rotas do modulo Clientes (paginas, gestao e API do agente)."""
import datetime
import hashlib
import json
import os
import re

from flask import (Response, abort, flash, g, jsonify, redirect, render_template, request, send_file,
                   url_for)

from ... import auth, clientes, clientes_alarmes, crypto, layouts, store
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
            ("atualizacoes", "Atualizações"), ("bds", "Bases de dados"), ("firewall", "Firewall"), ("agente", "Agente")]
    clientes.expirar_comandos(cid)
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
                           comandos=clientes.comandos(cid, 15) if aba == "agente" else [], descrever=clientes.descrever,
                           intervalos=clientes.intervalos_recolha(), fw_ops=clientes.FW_OPS,
                           fw_ac=clientes.fw_acessos(c) if aba == "firewall" else None,
                           fw_sub=request.args.get("fw") or "geral", servicos_monit=clientes.servicos_monit(c["id"]),
                           upg_ativo=next((x for x in clientes.todos_comandos(c["id"], "", "upgrade", limite=5)
                                           if x["estado"] in ("PENDENTE", "EM CURSO")), None),
                           layout=layouts.obter("cliente", str(g.user.id)),
                           fw_v=clientes._v(c.get("versao") or "0") >= (1, 7, 0),
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
    cmd = clientes.pedir_dados(cid, request.form.getlist("tipo"), g.user.username)
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=True, id=cmd)
    return redirect(url_for("clientes.detalhe", cid=cid, aba=request.form.get("aba") or "resumo", cmd=cmd))


@bp.route("/cliente/<int:cid>/logs", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def logs_ficheiros(cid):
    clientes.obter(cid) or abort(404)
    f = request.form
    acao = f.get("acao")
    destino = {"cid": cid, "aba": "logs"}
    try:
        destino["f"] = "gerir"
        if acao == "scan":
            destino["cmd"] = clientes.pedir_dados(cid, ["scan"], g.user.username)
        elif acao == "adicionar":
            fich = "" if f.get("tipo") == "pasta" else f.get("ficheiro")
            clientes.guardar_log(cid, f.get("pasta"), fich, f.get("descricao"), f.get("tag"), f.get("linhas") or 200)
            destino["cmd"] = clientes.pedir_dados(cid, ["logs"], g.user.username,
                                                  f"ficheiro novo: {fich}" if fich else f"pasta nova: {f.get('pasta')}")
        elif acao == "editar":
            fich = "" if f.get("tipo") == "pasta" else f.get("ficheiro")
            clientes.editar_log(cid, int(f.get("lid") or 0), f.get("pasta"), fich, f.get("descricao"), f.get("tag"), f.get("linhas") or 200)
            destino["cmd"] = clientes.pedir_dados(cid, ["logs"], g.user.username, "log alterado")
        elif acao == "adicionar_scan":
            sel = f.getlist("sel")
            for caminho in sel:
                pasta, _, nome = caminho.rpartition("/")
                clientes.guardar_log(cid, pasta or "/", nome, f.get(f"d:{caminho}") or nome, f.get("tag") or "", f.get("linhas") or 200)
            if sel:
                destino["cmd"] = clientes.pedir_dados(cid, ["logs"], g.user.username, f"{len(sel)} ficheiro(s) novo(s)")
            else:
                flash("Nenhum ficheiro selecionado.", "error")
        elif acao == "remover":
            clientes.apagar_log(cid, int(f.get("lid") or 0))
            flash("Log apagado da lista deste cliente (o ficheiro no servidor não é alterado).", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("clientes.detalhe", **destino))


@bp.route("/cliente/<int:cid>/ler", methods=["POST"])
@auth.requires(MODULO.acesso)
def ler_ficheiro(cid):
    clientes.obter(cid) or abort(404)
    caminho = os.path.normpath((request.form.get("caminho") or "").strip())
    if not caminho.startswith("/") or ".." in caminho.split("/"):
        return jsonify(ok=False, erro="caminho inválido"), 400
    if not clientes.pode_ler(cid, caminho, g.user.can("clientes.gerir")):
        return jsonify(ok=False, erro="só pode abrir os ficheiros configurados nos logs deste cliente"), 403
    cmd = clientes.pedir_leitura(cid, caminho, int(request.form.get("linhas") or 0),
                                 request.form.get("listar") == "1", g.user.username)
    return jsonify(ok=True, id=cmd)


@bp.route("/cliente/<int:cid>/firewall", methods=["POST"])
@auth.requires(MODULO.acesso)
def firewall(cid):
    """Ler o estado (qualquer utilizador do modulo) ou alterar (permissao clientes.firewall). Resposta JSON com o id do pedido."""
    clientes.obter(cid) or abort(404)
    f = request.form
    acao = f.get("acao") or "ler"
    if acao != "ler" and not g.user.can("clientes.firewall"):
        return jsonify(ok=False, erro="sem permissão para alterar a firewall (clientes.firewall)"), 403
    try:
        if acao == "ler":
            ops = []
        elif acao == "oracle":
            estado = (clientes.dados(cid).get("firewall") or {}).get("dados") or {}
            entradas = [x for x in re.split(r"[\s,;]+", f.get("entradas") or "") if x]
            ops = clientes.fw_preset_oracle(estado, (f.get("ipset") or "ipset-agente-portal").strip(), entradas, int(f.get("porta") or 8795))
            if not ops:
                return jsonify(ok=False, erro="nada a fazer: o ipset, as entradas e as regras já existem"), 400
        elif acao == "reiniciar":
            ops = [{"op": "reiniciar"}]
        elif acao == "rich_editar":                       # nova primeiro: se falhar, a antiga fica
            zona, velha, nova = (f.get("zona") or "").strip(), (f.get("regra") or "").strip(), (f.get("nova") or "").strip()
            if not nova or nova == velha:
                return jsonify(ok=False, erro="a regra não mudou"), 400
            ops = [{"op": "rich_add", "zona": zona, "regra": nova}, {"op": "rich_del", "zona": zona, "regra": velha}]
        elif acao == "ipset_editar":
            ops = clientes.fw_ops_ipset_editar(clientes.obter(cid), (f.get("nome") or "").strip(), f.get("entradas") or "")
            if not ops:
                return jsonify(ok=False, erro="as entradas não mudaram"), 400
        elif acao == "acessos":
            ops = clientes.fw_ops_acessos(clientes.obter(cid), f.getlist("ipsets"))
            if not ops:
                return jsonify(ok=False, erro="nada a fazer: o IP do portal já tem todos os acessos"), 400
        else:
            ops = [{k: (f.get(k) or "").strip() for k in clientes.FW_CAMPOS if (f.get(k) or "").strip()}]
            ops[0]["op"] = acao
            if acao == "ipset_novo" and f.get("entradas"):
                ops += [{"op": "entrada_add", "nome": ops[0].get("nome", ""), "entrada": x}
                        for x in re.split(r"[\s,;]+", f.get("entradas")) if x]
        cmd = clientes.pedir_firewall(cid, ops, g.user.username)
    except ValueError as exc:
        return jsonify(ok=False, erro=str(exc)), 400
    return jsonify(ok=True, id=cmd)


@bp.route("/cliente/<int:cid>/servicos", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def servicos_monit(cid):
    """Servicos monitorizados no cartao Servicos do resumo (o agente recebe a lista na sincronizacao seguinte)."""
    clientes.obter(cid) or abort(404)
    try:
        lista = clientes.guardar_servicos(cid, re.split(r"[\s,;]+", request.form.get("servicos") or ""))
    except ValueError as exc:
        return jsonify(ok=False, erro=str(exc)), 400
    return jsonify(ok=True, servicos=lista)


@bp.route("/cliente/<int:cid>/leitura/<int:cmd_id>")
@auth.requires(MODULO.acesso)
def leitura(cid, cmd_id):
    d = clientes.leitura(cid, cmd_id) or abort(404)
    if request.args.get("download"):
        nome = os.path.basename(d.get("caminho") or "log") or "log"
        return Response(d.get("texto") or "", mimetype="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{nome}"'})
    return jsonify(d)


@bp.route("/cliente/<int:cid>/ver")
@auth.requires(MODULO.acesso)
def ver_ficheiro(cid):
    c = clientes.obter(cid) or abort(404)
    caminho = os.path.normpath(request.args.get("caminho") or "/")
    return render_template("clientes/visualizador.html", c=c, caminho=caminho, linhas=request.args.get("n", "5000"),
                           pasta=request.args.get("pasta") == "1", voltar=request.args.get("voltar") or
                           url_for("clientes.detalhe", cid=cid, aba="logs"))


@bp.route("/cliente/<int:cid>/atualizar-agente", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def atualizar_agente(cid):
    c = clientes.obter(cid) or abort(404)
    cmd = clientes.pedir_atualizacao(cid, g.user.username)
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=True, id=cmd, cid=cid, nome=c["nome"])
    flash(f"{c['nome']}: atualização do agente pedida.", "ok")
    return redirect(request.form.get("voltar") or url_for("clientes.detalhe", cid=cid, aba="agente"))


@bp.route("/ferramentas/atualizar", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def atualizar_selecionados():
    out = []
    for x in request.form.getlist("cid"):
        c = clientes.obter(int(x)) if x.isdigit() else None
        if c:
            out.append({"cid": c["id"], "nome": c["nome"], "id": clientes.pedir_atualizacao(c["id"], g.user.username)})
    return jsonify(ok=True, comandos=out)


@bp.route("/agentes")
@auth.requires(MODULO.acesso)
def agentes():
    """Todos os agentes: versao, contacto, servico Oracle, pedido em curso; atualizar / reiniciar os escolhidos."""
    clientes.expirar_comandos()
    v_portal = clientes.agente_portal()[0]
    lista = []
    for c in clientes.listar():
        i = c.get("info") or {}
        lista.append({"c": c, "desatualizado": clientes.desatualizado(c), "ativo": clientes.comando_ativo(c["id"]),
                      "ult": i.get("atualizacao"), "rpc": i.get("rpc"), "python": (i.get("python") or (i.get("rpc") or {}).get("python") or ""),
                      "so": i.get("so") or i.get("sistema") or ""})
    return render_template("clientes/agentes.html", lista=lista, v_portal=v_portal,
                           n_desat=sum(1 for x in lista if x["desatualizado"]))


@bp.route("/agentes/acao", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def agentes_acao():
    acao = request.form.get("acao")
    if acao not in ("atualizar", "reiniciar", "atualizar_todos"):
        return jsonify(ok=False, erro="ação inválida"), 400
    alvo = ([c for c in clientes.listar() if clientes.desatualizado(c)] if acao == "atualizar_todos" else
            [c for x in request.form.getlist("cid") if x.isdigit() for c in [clientes.obter(int(x))] if c])
    out = []
    for c in alvo:
        if acao == "reiniciar":
            cmd = clientes.pedir_reinicio(c["id"], g.user.username)
        else:
            cmd = clientes.pedir_atualizacao(c["id"], g.user.username)
        out.append({"cid": c["id"], "nome": c["nome"], "id": cmd})
    return jsonify(ok=True, comandos=out)


@bp.route("/comando/<int:cmd_id>/cancelar", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def cancelar_comando(cmd_id):
    r = clientes.comando(cmd_id) or abort(404)
    clientes.cancelar_comando(cmd_id, g.user.username)
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(ok=True)
    return redirect(request.form.get("voltar") or url_for("clientes.detalhe", cid=r["cliente_id"], aba="agente"))


@bp.route("/comando/<int:cmd_id>/repetir", methods=["POST"])
@auth.requires(MODULO.acesso)
def repetir_comando(cmd_id):
    """Repete um pedido que falhou, com os mesmos argumentos (JSON com o id do novo pedido)."""
    r = clientes.comando(cmd_id) or abort(404)
    try:
        args = json.loads(r.get("args") or "{}")
    except ValueError:
        args = {}
    precisa = ("clientes.gerir" if r["tipo"] in ("atualizar", "reiniciar") else "clientes.atualizar" if r["tipo"] == "upgrade" else
               "clientes.firewall" if r["tipo"] == "firewall" and args.get("ops") else None)
    if precisa and not g.user.can(precisa):
        return jsonify(ok=False, erro=f"sem permissão ({precisa})"), 403
    try:
        novo = clientes.repetir_comando(cmd_id, g.user.username)
    except ValueError as exc:
        return jsonify(ok=False, erro=str(exc)), 400
    return jsonify(ok=True, id=novo)


@bp.route("/api/comando/<int:cmd_id>")
@auth.requires(MODULO.acesso)
def api_comando(cmd_id):
    r = clientes.comando(cmd_id) or abort(404)
    c = clientes.obter(r["cliente_id"]) or {}
    extra = {}
    if r["tipo"] == "upgrade":
        extra = {"erros": clientes.erros_upgrade(r.get("output") or ""),
                 "reinicio": "precisa de ser reiniciado" in (r.get("output") or "")}
    return jsonify({**{k: r[k] for k in ("id", "cliente_id", "tipo", "estado", "pedido_por", "criado", "iniciado", "terminado", "output")},
                    "descricao": clientes.descrever(r), "cliente": c.get("nome", ""), **extra})


# ====================================================================== upgrade do sistema (execucoes)
@bp.route("/cliente/<int:cid>/upgrade", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.atualizar")
def upgrade(cid):
    clientes.obter(cid) or abort(404)
    try:
        cmd = clientes.pedir_upgrade(cid, request.form.get("modo") or "todos", request.form.getlist("pacotes"), g.user.username)
    except ValueError as exc:
        return jsonify(ok=False, erro=str(exc)), 400
    return jsonify(ok=True, id=cmd, url=url_for("clientes.execucao", cmd_id=cmd))


@bp.route("/execucoes")
@auth.requires(MODULO.acesso)
def execucoes():
    """Upgrades do sistema pedidos aos clientes (como as execucoes do Oracle)."""
    clientes.expirar_comandos()
    f = {"cliente": request.args.get("cliente", type=int), "estado": request.args.get("estado") or ""}
    lista = clientes.todos_comandos(f["cliente"], f["estado"], "upgrade", limite=500)
    for x in lista:
        try:
            a = json.loads(x.get("args") or "{}")
        except ValueError:
            a = {}
        x["modo"], x["n"], x["retoma"] = a.get("modo"), len(a.get("pacotes") or []), a.get("retoma")
    return render_template("clientes/execucoes.html", lista=lista, f=f, todos=clientes.listar(),
                           ativos=sum(1 for x in lista if x["estado"] in ("PENDENTE", "EM CURSO")))


@bp.route("/execucao/<int:cmd_id>")
@auth.requires(MODULO.acesso)
def execucao(cmd_id):
    r = clientes.comando(cmd_id) or abort(404)
    if r["tipo"] != "upgrade":
        return redirect(url_for("clientes.pedidos", cmd=cmd_id))
    clientes.expirar_comandos(r["cliente_id"])
    r = clientes.comando(cmd_id)
    try:
        a = json.loads(r.get("args") or "{}")
    except ValueError:
        a = {}
    with store.tx() as t:
        retomas = t.all("SELECT id, estado FROM cliente_comandos WHERE tipo = 'upgrade' AND args LIKE ? ORDER BY id",
                        (f'%"retoma": {cmd_id}}}%',))
    return render_template("clientes/execucao.html", r=r, a=a, c=clientes.obter(r["cliente_id"]) or {},
                           descricao=clientes.descrever(r), erros=clientes.erros_upgrade(r.get("output") or ""),
                           retomas=retomas)


@bp.route("/pedidos")
@auth.requires(MODULO.acesso)
def pedidos():
    clientes.expirar_comandos()
    f = {"cliente": request.args.get("cliente", type=int), "estado": request.args.get("estado") or "",
         "tipo": request.args.get("tipo") or ""}
    lista = clientes.todos_comandos(f["cliente"], f["estado"], f["tipo"])
    return render_template("clientes/pedidos.html", pedidos=lista, f=f, todos=clientes.listar(), tipos=clientes.TIPOS_COMANDO,
                           sel=request.args.get("cmd", type=int))


@bp.route("/agente/api/comandos", methods=["POST"])
def agente_comandos():
    c = _agente()
    if not c:
        return _negado()
    return jsonify(clientes.entregar_comandos(c))


@bp.route("/agente/api/comando/<int:cmd_id>", methods=["POST"])
def agente_comando(cmd_id):
    c = _agente()
    if not c:
        return _negado()
    r = clientes.comando(cmd_id)
    if not r or r["cliente_id"] != c["id"]:
        abort(404)
    d = request.get_json(silent=True) or {}
    for l in (d.get("linhas") or ([d["linha"]] if d.get("linha") else []))[:50]:
        clientes.comando_linha(cmd_id, str(l)[:1000])
    if r["tipo"] == "ler" and isinstance(d.get("dados"), dict):
        clientes.guardar_leitura(c["id"], cmd_id, d["dados"])
    if r["tipo"] == "firewall" and isinstance(d.get("dados"), dict):
        clientes.guardar_dados(c["id"], {"firewall": d["dados"]})
    if d.get("estado") in ("OK", "ERRO"):
        clientes.comando_estado(cmd_id, d["estado"])
    return jsonify(ok=True)


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
                           chave=chave, erro_chave=erro_chave, cfg=_cfg(), tab="portal", sub="clientes",
                           recolha=clientes.RECOLHA, intervalos=clientes.intervalos_recolha())


@bp.route("/gerir/recolha", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def gerir_recolha():
    novos = {}
    for k, (nome, padrao, _h) in clientes.RECOLHA.items():
        try:
            v = int(float(request.form.get(k) or 0) * (60 if k != "processos" else 1))
        except ValueError:
            v = padrao
        novos[f"clientes_recolha_{k}"] = str(max(0 if k == "processos" else 60, min(7 * 86400, v)))
    store.save_settings(novos)
    flash("Intervalos de recolha guardados: os agentes (1.6.0+) aplicam-nos na sincronização seguinte.", "ok")
    return redirect(url_for("clientes.gerir") + "#recolha")


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
    clientes.expirar_comandos()
    agentes = [{"c": c, "desatualizado": clientes.desatualizado(c), "ativo": clientes.comando_ativo(c["id"]),
                "pedido": "",
                "ult": (c.get("info") or {}).get("atualizacao")} for c in clientes.listar()]
    from ... import wheels
    return render_template("clientes/ferramentas.html", fich=fich, url=_url_portal(), chave=chave, agentes=agentes, v_portal=v_portal,
                           inseguro=_cfg()["inseguro"], utilizador=_cfg()["ssh_padrao"], wheels=wheels.listar(),
                           wheels_a_correr=wheels.a_correr(), wheels_log=wheels.log())


# ============================================================ wheels do python-oracledb (clientes sem internet)
@bp.route("/wheels/estado")
@auth.requires(MODULO.acesso)
def wheels_estado():
    from ... import wheels
    return jsonify(a_correr=wheels.a_correr(), log=wheels.log(), wheels=wheels.listar())


@bp.route("/wheels/obter", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def wheels_obter():
    from ... import wheels
    return jsonify(ok=wheels.obter_em_fundo())


@bp.route("/wheels/carregar", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def wheels_carregar():
    from ... import wheels
    nomes = []
    try:
        for f in request.files.getlist("wheel"):
            nomes.append(wheels.guardar(f.filename, f.read()))
    except ValueError as exc:
        return jsonify(ok=False, erro=str(exc)), 400
    return jsonify(ok=True, nomes=nomes)


@bp.route("/wheels/apagar", methods=["POST"])
@auth.requires(MODULO.acesso, "clientes.gerir")
def wheels_apagar():
    from ... import wheels
    n = sum(wheels.apagar(x) for x in request.form.getlist("nome"))
    return jsonify(ok=True, apagados=n)


@bp.route("/agente/wheels/")
def agente_wheels():
    """Lista (um nome por linha) dos wheels guardados no portal; o setup.sh escolhe os compativeis com o cliente."""
    from ... import wheels
    return Response("".join(w["nome"] + "\n" for w in wheels.listar()), mimetype="text/plain")


@bp.route("/agente/wheels/<nome>")
def agente_wheel(nome):
    from ... import wheels
    p = wheels.caminho(nome) or abort(404)
    return send_file(p, mimetype="application/octet-stream", as_attachment=True, download_name=nome, max_age=0)


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
    info = d.get("info") if isinstance(d.get("info"), dict) else {}
    if d.get("teste") and (c.get("info") or {}).get("rpc") and isinstance(info.get("rpc"), dict) and not info["rpc"].get("servico"):
        # 'agente-portal testar' corre noutro processo (sem o servico Oracle): nao apaga o estado enviado pelo servico
        info["rpc"] = {**c["info"]["rpc"], **{k: v for k, v in info["rpc"].items() if k in ("ativo", "erro") and info["rpc"].get("sonda")}}
    return jsonify(clientes.sync(c, info, _ip(), link,
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
