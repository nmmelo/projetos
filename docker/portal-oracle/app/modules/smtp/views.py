"""Rotas do modulo SMTP: Geral (ligacao, validar, teste), Regras (email), Notificacoes (portal), Logs."""
import re
import threading

from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for

from ... import agente_info, auth, crypto, eventos, modulos, smtp_envio, store
from . import MODULO, bp

EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def _abas():
    return [(e, r) for e, r, p in MODULO.abas if not p or g.user.can(p)]


@bp.app_context_processor
def _ctx():
    return {"smtp_abas": _abas} if g.get("user") else {}


@bp.route("/")
@auth.requires(MODULO.acesso)
def index():
    return redirect(url_for("smtp.geral"))


# ====================================================================== Geral
CAMPOS = ("smtp_host", "smtp_porta", "smtp_seguranca", "smtp_utilizador", "smtp_remetente", "smtp_remetente_nome",
          "smtp_destinatarios", "smtp_prefixo", "smtp_timeout", "smtp_validar_min", "smtp_url_portal", "smtp_helo")


def _emails(texto: str) -> list[str]:
    return [e.strip() for e in re.split(r"[,;\s]+", texto or "") if e.strip()]


@bp.route("/geral", methods=["GET", "POST"])
@auth.requires(MODULO.acesso)
def geral():
    if request.method == "POST":
        if not g.user.can("smtp.configurar"):
            abort(403)
        f = request.form
        try:
            v = {k: (f.get(k) or "").strip() for k in CAMPOS}
            if not v["smtp_porta"].isdigit() or not 1 <= int(v["smtp_porta"]) <= 65535:
                raise ValueError("Porta inválida.")
            if v["smtp_seguranca"] not in ("nenhuma", "starttls", "ssl"):
                raise ValueError("Segurança inválida.")
            for k, lim in (("smtp_timeout", (3, 300)), ("smtp_validar_min", (0, 10080))):
                if not v[k].isdigit() or not lim[0] <= int(v[k]) <= lim[1]:
                    raise ValueError(f"Valor inválido em {k.replace('smtp_', '')}.")
            if v["smtp_remetente"] and not EMAIL_RE.match(v["smtp_remetente"]):
                raise ValueError("Remetente inválido.")
            maus = [e for e in _emails(v["smtp_destinatarios"]) if not EMAIL_RE.match(e)]
            if maus:
                raise ValueError(f"Destinatários inválidos: {', '.join(maus)}")
            v["smtp_destinatarios"] = ", ".join(_emails(v["smtp_destinatarios"]))
            v["smtp_verificar"] = "1" if f.get("smtp_verificar") else "0"
            v["smtp_ativo"] = "1" if f.get("smtp_ativo") else "0"
            if not f.get("smtp_auth"):
                v["smtp_utilizador"] = ""
                v["smtp_password_enc"] = ""
            elif f.get("smtp_password"):
                v["smtp_password_enc"] = crypto.encrypt(f["smtp_password"])
            store.save_settings(v)
            flash("Configuração SMTP guardada.", "ok")
            return redirect(url_for("smtp.geral"))
        except ValueError as exc:
            flash(f"Erro: {exc}", "error")
    s = store.get_settings()
    return render_template("smtp/geral.html", s=s, estado=smtp_envio.estado(), agente=agente_info.estado(),
                           url_sugerido=request.url_root.rstrip("/"), tem_password=bool(s.get("smtp_password_enc")),
                           pendentes=_contar("PENDENTE"))


def _contar(status: str) -> int:
    with store.tx() as t:
        return int(t.val("SELECT COUNT(*) FROM email_queue WHERE status = ?", (status,)) or 0)


@bp.route("/api/validar", methods=["POST"])
@auth.requires("smtp.configurar")
def api_validar():
    reg = smtp_envio.Registo()
    ok, msg = smtp_envio.validar(reg)
    return jsonify(ok=ok, msg=msg, log=reg.texto())


@bp.route("/api/teste", methods=["POST"])
@auth.requires("smtp.configurar")
def api_teste():
    para = ((request.get_json(silent=True) or {}).get("para") or "").strip()
    if not EMAIL_RE.match(para):
        return jsonify(ok=False, error="Endereço de destino inválido."), 400
    eid = smtp_envio.pedir_teste(para, g.user.username)
    via = "agente"
    if not agente_info.ativo():                       # sem agente: o portal envia ja
        threading.Thread(target=smtp_envio.processar_fila, daemon=True).start()
        via = "portal"
    return jsonify(ok=True, id=eid, via=via)


@bp.route("/api/email/<int:eid>")
@auth.requires(MODULO.acesso)
def api_email(eid):
    with store.tx() as t:
        e = t.one("SELECT * FROM email_queue WHERE id = ?", (eid,))
    if not e:
        abort(404)
    return jsonify(e)


# ====================================================================== Regras (email) e Notificacoes (portal)
def _form_regra(canal: str) -> dict:
    f = request.form
    evs = modulos.todos_eventos()
    ev = f.get("evento", "")
    if ev not in evs:
        raise ValueError("Escolha o evento.")
    sit = [x for x in f.getlist("situacoes") if x in evs[ev]["situacoes"]]
    if not sit:
        raise ValueError("Escolha pelo menos uma situação.")
    par = None
    if evs[ev].get("parametro"):
        p = (f.get("parametro") or "").strip()
        if not p.isdigit() or not 1 <= int(p) <= 100:
            raise ValueError("Limiar inválido (1 a 100).")
        par = int(p)
    destino = {"lancador": bool(f.get("lancador")), "admins": bool(f.get("admins")), "modulo": bool(f.get("modulo")),
               "perfis": [int(i) for i in f.getlist("perfis") if i.isdigit()]}
    if canal == "email":
        emails = _emails(f.get("emails", ""))
        maus = [e for e in emails if not EMAIL_RE.match(e)]
        if maus:
            raise ValueError(f"Emails inválidos: {', '.join(maus)}")
        destino.update(emails=emails, padrao=bool(f.get("padrao")))
    if not any(destino.get(k) for k in ("lancador", "admins", "modulo", "perfis", "emails", "padrao")):
        raise ValueError("Indique pelo menos um destinatário.")
    nome = (f.get("nome") or "").strip() or f"{evs[ev]['nome']}: {', '.join(evs[ev]['situacoes'][x] for x in sit)}"
    return {"nome": nome, "canal": canal, "evento": ev, "situacoes": sit, "parametro": par, "destino": destino,
            "ativo": bool(f.get("ativo"))}


def _pagina_regras(canal: str):
    regras = eventos.listar_regras(canal)
    perfis = {p["id"]: p["name"] for p in store.list_profiles()}
    tpl = "smtp/regras.html" if canal == "email" else "smtp/notificacoes.html"
    extra = {"historico": eventos.historico(100)} if canal == "portal" else {}
    return render_template(tpl, regras=regras, eventos_def=modulos.todos_eventos(), perfis=perfis,
                           canal=canal, ativos=modulos.ativos(), smtp_ativo=store.get_setting("smtp_ativo") == "1",
                           pode=g.user.can("smtp.regras"), **extra)


@bp.route("/regras")
@auth.requires(MODULO.acesso)
def regras():
    return _pagina_regras("email")


@bp.route("/notificacoes")
@auth.requires(MODULO.acesso)
def notificacoes():
    return _pagina_regras("portal")


@bp.route("/regras/<canal>/nova", methods=["GET", "POST"])
@bp.route("/regras/<canal>/<int:rid>/editar", methods=["GET", "POST"])
@auth.requires("smtp.regras")
def regra_form(canal, rid=None):
    if canal not in ("email", "portal"):
        abort(404)
    r = eventos.obter_regra(rid) if rid else None
    if rid and (not r or r["canal"] != canal):
        abort(404)
    if request.method == "POST":
        try:
            eventos.guardar_regra(_form_regra(canal), rid)
            flash("Regra guardada.", "ok")
            return redirect(url_for("smtp.regras" if canal == "email" else "smtp.notificacoes"))
        except ValueError as exc:
            flash(f"Erro: {exc}", "error")
            r = {"id": rid, "nome": request.form.get("nome"), "evento": request.form.get("evento"),
                 "situacoes": request.form.getlist("situacoes"), "parametro": request.form.get("parametro"),
                 "ativo": bool(request.form.get("ativo")),
                 "destino": {"lancador": bool(request.form.get("lancador")), "admins": bool(request.form.get("admins")),
                             "modulo": bool(request.form.get("modulo")), "padrao": bool(request.form.get("padrao")),
                             "perfis": [int(i) for i in request.form.getlist("perfis") if i.isdigit()],
                             "emails": _emails(request.form.get("emails", ""))}}
    return render_template("smtp/regra_form.html", r=r or {"ativo": True, "situacoes": [], "destino": {}},
                           canal=canal, eventos_def=modulos.todos_eventos(), perfis=store.list_profiles(),
                           editing=bool(rid))


@bp.route("/regras/<int:rid>/apagar", methods=["POST"])
@auth.requires("smtp.regras")
def regra_apagar(rid):
    r = eventos.obter_regra(rid) or abort(404)
    eventos.apagar_regra(rid)
    flash("Regra apagada.", "ok")
    return redirect(url_for("smtp.regras" if r["canal"] == "email" else "smtp.notificacoes"))


@bp.route("/regras/<int:rid>/ativar", methods=["POST"])
@auth.requires("smtp.regras")
def regra_ativar(rid):
    r = eventos.obter_regra(rid) or abort(404)
    r["ativo"] = not r["ativo"]
    eventos.guardar_regra(r, rid)
    return redirect(url_for("smtp.regras" if r["canal"] == "email" else "smtp.notificacoes"))


@bp.route("/notificacoes/teste", methods=["POST"])
@auth.requires(MODULO.acesso)
def notificacao_teste():
    with store.tx() as t:
        t.insert("notifications", {"user_key": str(g.user.id), "created_at": store.now(), "evento": "smtp.teste",
                                   "nivel": "info", "titulo": "Notificação de teste",
                                   "texto": "O sininho do portal está a funcionar.", "link": "/smtp/notificacoes", "lida": 0})
    flash("Notificação de teste criada: veja o sininho no topo.", "ok")
    return redirect(url_for("smtp.notificacoes"))


# ====================================================================== Logs
@bp.route("/logs")
@auth.requires(MODULO.acesso)
def logs():
    return render_template("smtp/logs.html", pode=g.user.can("smtp.configurar"))


@bp.route("/api/logs")
@auth.requires(MODULO.acesso)
def api_logs():
    q = (request.args.get("q") or "").strip().lower()[:100]
    sql = ("SELECT id, created_at, tipo, evento, regra, destinatarios, assunto, status, tentativas, enviado_em, erro, "
           "pedido_por FROM email_queue")
    args: tuple = ()
    if q:
        sql += (" WHERE LOWER(COALESCE(destinatarios, '')) LIKE ? OR LOWER(COALESCE(assunto, '')) LIKE ?"
                " OR LOWER(COALESCE(erro, '')) LIKE ? OR LOWER(COALESCE(regra, '')) LIKE ?")
        args = (f"%{q}%",) * 4
    with store.tx() as t:
        rows = t.all(sql + " ORDER BY id DESC LIMIT 1000", args)
    return jsonify(ok=True, emails=rows)


@bp.route("/logs/<int:eid>/reenviar", methods=["POST"])
@auth.requires("smtp.configurar")
def reenviar(eid):
    with store.tx() as t:
        n = t.run("UPDATE email_queue SET status = 'PENDENTE', tentativas = 0, proxima = NULL WHERE id = ? "
                  "AND status = 'ERRO'", (eid,))
    if n and not agente_info.ativo():
        threading.Thread(target=smtp_envio.processar_fila, daemon=True).start()
    return jsonify(ok=bool(n))


@bp.route("/logs/limpar", methods=["POST"])
@auth.requires("smtp.configurar")
def limpar():
    dias = request.form.get("dias", "30")
    dias = int(dias) if dias.isdigit() else 30
    with store.tx() as t:
        n = t.run("DELETE FROM email_queue WHERE created_at < ? AND status IN ('ENVIADO', 'ERRO')",
                  (store.ago(days=dias),))
    flash(f"{n} registo(s) com mais de {dias} dias apagado(s).", "ok")
    return redirect(url_for("smtp.logs"))
