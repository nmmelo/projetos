import hmac
import secrets

from flask import Blueprint, abort, g, jsonify, make_response, render_template, request, session

from .. import aparencia, auth, manutencao, modulos, store

bp = Blueprint("portal", __name__)

# Paginas que funcionam durante a manutencao (o login continua disponivel para os administradores)
LIVRES_MANUTENCAO = {"static", "portal.login", "portal.logout", "portal.favicon"}


@bp.before_app_request
def before():
    g.user = auth.load_current_user()
    if request.method == "POST":
        sent = request.form.get("csrf") or request.headers.get("X-CSRF", "")
        if not session.get("csrf") or not hmac.compare_digest(sent, session["csrf"]):
            abort(400, "Token CSRF invalido. Recarregue a pagina.")
    g.manutencao = manutencao.ativa()
    if g.manutencao and request.endpoint not in LIVRES_MANUTENCAO \
            and not (g.user and g.user.can("config.portal")):
        return pagina_manutencao()


def pagina_manutencao():
    if "/api/" in request.path:
        resp = jsonify(ok=False, error="Portal em manutenção.")
    else:
        resp = make_response(render_template("manutencao.html", msg=manutencao.estado()["html"],
                                             pagina_minima=True))
    resp.status_code = 503
    resp.headers["Retry-After"] = "120"
    resp.headers["Content-Security-Policy"] = "script-src 'none'; object-src 'none'; base-uri 'none'"
    return resp


@bp.app_context_processor
def inject():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    try:
        look = aparencia.context(store.get_settings())
    except Exception:  # noqa: BLE001 - BD do portal indisponivel
        look = aparencia.context(aparencia.DEFAULTS)
    user = g.get("user")
    return {"csrf_token": session["csrf"], "user": user, "ACTIONS": modulos.todas_acoes(),
            "modulos_menu": modulos.visiveis(user), "manutencao_ativa": g.get("manutencao", False), **look}


@bp.app_errorhandler(403)
def forbidden(_e):
    return render_template("error.html", code=403,
                           msg="Não tem permissão para esta página ou base de dados."), 403


@bp.app_errorhandler(500)
def server_error(e):
    """Erro inesperado: fica no registo (journalctl -u <nome>) com o pedido; a pagina mostra a referencia."""
    import logging
    import secrets as _s
    ref = _s.token_hex(4)
    orig = getattr(e, "original_exception", None) or e
    logging.getLogger("portal").error("erro %s em %s %s", ref, request.method, request.path, exc_info=orig)
    try:
        return render_template("error.html", code=500,
                               msg=f"Ocorreu um erro inesperado (referência {ref}). Está registado no journal do "
                                   f"serviço: journalctl -u <nome> | grep {ref}"), 500
    except Exception:  # noqa: BLE001 - o proprio template falhou
        return f"Erro interno (referencia {ref}).", 500


@bp.app_errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404, msg="Página não encontrada (ou módulo desativado)."), 404


from . import config, main  # noqa: E402,F401  (registam as rotas no blueprint)
