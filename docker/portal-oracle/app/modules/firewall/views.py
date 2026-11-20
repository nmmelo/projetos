"""Rotas do modulo Firewall."""
from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for

from ... import auth, firewall, sistema_info
from . import bp, MODULO


def _ctx() -> dict:
    est = firewall.estado()
    return {"est": est, "pode": g.user.can("firewall.alterar"), "instancia": sistema_info.instancia() or "<nome>",
            "docker": sistema_info.servicos_backend() == "supervisor"}


@bp.route("/")
@auth.login_required
def index():
    return redirect(url_for("firewall.geral"))


@bp.route("/geral")
@auth.requires(MODULO.acesso)
def geral():
    return render_template("firewall/geral.html", historico=firewall.historico(15), **_ctx())


@bp.route("/regras")
@auth.requires(MODULO.acesso)
def regras():
    c = _ctx()
    z, regras_ufw, erro = None, [], ""
    perm = request.args.get("perm") == "1"
    try:
        if c["est"].get("backend") == "firewalld" and c["est"].get("ativo"):
            nome = request.args.get("zona") or c["est"].get("zona_padrao") or "public"
            z = firewall.zona(nome, perm)
        elif c["est"].get("backend") == "ufw" and c["est"].get("ok"):
            regras_ufw = firewall.regras_ufw()
    except ValueError as exc:
        erro = str(exc)
    return render_template("firewall/regras.html", z=z, regras_ufw=regras_ufw, erro=erro, perm=perm,
                           protos=firewall.PROTOS, **c)


@bp.route("/ipsets")
@auth.requires(MODULO.acesso)
def ipsets():
    c = _ctx()
    lista, erro, uso = [], "", {}
    try:
        if c["est"].get("backend") == "firewalld" and c["est"].get("ativo"):
            lista = firewall.ipsets()
            uso = firewall.uso_ipsets(c["est"].get("zonas", []))
    except ValueError as exc:
        erro = str(exc)
    return render_template("firewall/ipsets.html", ipsets=lista, erro=erro, uso=uso, tipos=firewall.TIPOS_IPSET, **c)


@bp.route("/ipsets/<nome>")
@auth.requires(MODULO.acesso)
def ipset(nome):
    c = _ctx()
    s, erro, usado = None, "", []
    try:
        if c["est"].get("backend") == "firewalld" and c["est"].get("ativo"):
            s = next((x for x in firewall.ipsets() if x["nome"] == nome), None)
            if not s:
                abort(404)
            usado = firewall.uso_ipsets(c["est"].get("zonas", [])).get(nome, [])
    except ValueError as exc:
        erro = str(exc)
    return render_template("firewall/ipset.html", s=s, erro=erro, usado=usado, q=request.args.get("q", ""), **c)


@bp.route("/logs")
@auth.requires(MODULO.acesso)
def logs():
    return render_template("firewall/logs.html", **_ctx())


@bp.route("/api/logs")
@auth.requires(MODULO.acesso)
def api_logs():
    try:
        return jsonify(ok=True, linhas=firewall.logs(request.args.get("tipo", "servico"), request.args.get("q", "")))
    except PermissionError as exc:
        return jsonify(ok=False, error=f"Sem permissão para ler o registo: {exc}"), 403


@bp.route("/definicoes")
@auth.requires(MODULO.acesso)
def definicoes():
    c = _ctx()
    extra = {}
    if c["est"].get("backend") == "firewalld" and c["est"].get("ativo"):
        extra = {k: firewall.fc(arg)[1].split() for k, arg in
                 (("servicos", "--get-services"), ("icmp", "--get-icmptypes"), ("helpers", "--get-helpers"))}
        extra["conf"] = firewall.fc("--permanent", "--get-ipset-types")[1].split()
    return render_template("firewall/definicoes.html", extra=extra, **c)


@bp.route("/acao", methods=["POST"])
@auth.requires("firewall.alterar")
def acao():
    d = request.form.to_dict()
    voltar = d.pop("voltar", "") or url_for("firewall.geral")
    try:
        ok, out = firewall.aplicar(g.user.username, d.pop("acao", ""), d)
        flash(("Feito. " if ok else "A firewall recusou: ") + (out.strip().splitlines()[-1] if out.strip() else ""),
              "ok" if ok else "error")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(voltar if voltar.startswith("/") else url_for("firewall.geral"))
