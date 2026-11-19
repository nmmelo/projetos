"""Login, Home (blocos dos modulos), Minha area e enderecos antigos."""
import base64
import os

from flask import (current_app, flash, g, make_response, redirect, render_template, request,
                   url_for)
from werkzeug.security import check_password_hash

from .. import aparencia, auth, manutencao, settings, store
from . import bp


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("portal.home"))
    if request.method == "POST":
        user = auth.authenticate(request.form.get("user", ""), request.form.get("password", ""))
        if user:
            auth.login_session(user)
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("portal.home"))
        flash("Utilizador ou password inválidos.", "error")
    manut_msg = manutencao.estado()["html"] if g.get("manutencao") else None
    return render_template("login.html", no_admin=settings.admin() is None, manut_msg=manut_msg)


@bp.route("/favicon.ico")
def favicon():
    """Icone configurado em Configuracao > Portal > Aparencia, ou o de omissao (static/favicon.svg).
    Publico (aparece no login). O URL leva ?v=<versao>, por isso pode ficar em cache."""
    cache = 86400 if request.args.get("v") else 3600
    try:
        data, kind = store.get_setting("favicon_data"), store.get_setting("favicon_type")
    except Exception:  # noqa: BLE001 - BD do portal indisponivel
        data = kind = ""
    if data and kind:
        resp = make_response(base64.b64decode(data))
        resp.headers["Content-Type"] = kind
    else:   # icone por omissao, na cor de destaque do tema
        try:
            theme = store.get_setting("theme")
        except Exception:  # noqa: BLE001
            theme = aparencia.DEFAULT_THEME
        with open(os.path.join(current_app.static_folder, "favicon.svg"), encoding="utf-8") as fh:
            svg = fh.read().replace("#1f5fbf", aparencia.accent(theme))
        resp = make_response(svg)
        resp.headers["Content-Type"] = "image/svg+xml"
    resp.headers["Cache-Control"] = f"public, max-age={cache}"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    # um SVG aberto diretamente no browser nao pode correr scripts
    resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return resp


@bp.route("/logout", methods=["POST"])
def logout():
    from flask import session
    session.clear()
    return redirect(url_for("portal.login"))


@bp.route("/")
@auth.login_required
def home():
    blocos = []
    for m in g.user.modulos:
        if m.home_template:
            blocos.append({"modulo": m, "ctx": m.home_context(g.user) if m.home_context else {}})
    last_backup = None
    if g.user.can("config.portal"):
        b = store.list_backups()
        last_backup = b[0] if b else None
    return render_template("home.html", blocos=blocos, last_backup=last_backup)


# Enderecos anteriores aos modulos (marcadores, links antigos) -> modulo Oracle
@bp.route("/report")
def old_report():
    return redirect(url_for("oracle.report", **request.args), 301)


@bp.route("/shrink")
def old_shrink():
    return redirect(url_for("oracle.shrink", **request.args), 301)


@bp.route("/jobs")
@bp.route("/jobs/<int:job_id>")
def old_jobs(job_id=None):
    return redirect(url_for("oracle.job_detail", job_id=job_id) if job_id else url_for("oracle.jobs_list"), 301)


@bp.route("/config/databases")
def old_dbs():
    return redirect(url_for("oracle.cfg_dbs"), 301)


@bp.route("/me", methods=["GET", "POST"])
@auth.login_required
def me():
    u = g.user
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "dados":
                name = request.form.get("name", "").strip()[:150]
                email = request.form.get("email", "").strip()[:255]
                if u.is_admin:
                    adm = settings.admin()
                    settings.save_admin({**adm, "name": name, "email": email})
                else:
                    store.update_user_fields(u.id, name=name, email=email)
                flash("Dados atualizados.", "ok")
            elif action == "password":
                cur, new, rep = (request.form.get(k, "") for k in ("current", "new", "repeat"))
                stored = settings.admin()["password_hash"] if u.is_admin else store.get_user(uid=u.id)["password_hash"]
                if not check_password_hash(stored, cur):
                    raise ValueError("A password atual está errada.")
                if new != rep:
                    raise ValueError("A nova password e a confirmação não coincidem.")
                auth.check_password_strength(new)
                if u.is_admin:
                    settings.save_admin({**settings.admin(), "password_hash": auth.hash_password(new)})
                else:
                    store.update_user_fields(u.id, password_hash=auth.hash_password(new))
                flash("Password alterada.", "ok")
            return redirect(url_for("portal.me"))
        except ValueError as exc:
            flash(str(exc), "error")
    dbs = None
    if u.can("modulo.oracle"):
        dbs = store.list_dbs() if u.is_admin or u.all_dbs else [d for d in store.list_dbs() if d["id"] in u.db_ids]
    return render_template("me.html", dbs=dbs)
