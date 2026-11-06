import hmac
import secrets

from flask import Blueprint, abort, g, render_template, request, session

from .. import auth, store

bp = Blueprint("portal", __name__)


@bp.before_app_request
def before():
    g.user = auth.load_current_user()
    if request.method == "POST":
        sent = request.form.get("csrf") or request.headers.get("X-CSRF", "")
        if not session.get("csrf") or not hmac.compare_digest(sent, session["csrf"]):
            abort(400, "Token CSRF invalido. Recarregue a pagina.")


@bp.app_context_processor
def inject():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    try:
        portal_name = store.get_settings()["portal_name"]
    except Exception:  # noqa: BLE001 - BD do portal indisponivel
        portal_name = store.DEFAULT_SETTINGS["portal_name"]
    return {"csrf_token": session["csrf"], "user": g.get("user"), "portal_name": portal_name,
            "ACTIONS": auth.ACTIONS}


@bp.app_errorhandler(403)
def forbidden(_e):
    return render_template("error.html", code=403,
                           msg="Não tem permissão para esta página ou base de dados."), 403


@bp.app_errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404, msg="Página não encontrada."), 404


from . import config, main, ops  # noqa: E402,F401  (registam as rotas no blueprint)
