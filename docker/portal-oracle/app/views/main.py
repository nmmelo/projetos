"""Login, Home e Minha area."""
from flask import flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash

from .. import auth, settings, store
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
    return render_template("login.html", no_admin=settings.admin() is None)


@bp.route("/logout", methods=["POST"])
def logout():
    from flask import session
    session.clear()
    return redirect(url_for("portal.login"))


@bp.route("/")
@auth.login_required
def home():
    dbs = g.user.visible_dbs(store.list_dbs())
    visible = {d["id"] for d in dbs}
    jobs = [j for j in store.list_jobs(500) if j["db_id"] in visible]
    since = store.ago(hours=24)
    stats = {
        "dbs": len(dbs),
        "running": sum(1 for j in jobs if j["status"] == "EM CURSO"),
        "last24": sum(1 for j in jobs if j["started_at"] >= since),
        "errors24": sum(1 for j in jobs if j["started_at"] >= since and j["status"] in ("ERRO", "INTERROMPIDO")),
    }
    last_backup = None
    if g.user.can("config.portal"):
        b = store.list_backups()
        last_backup = b[0] if b else None
    return render_template("home.html", dbs=dbs, stats=stats, recent=jobs[:8], last_backup=last_backup)


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
    dbs = store.list_dbs() if u.is_admin or u.all_dbs else [d for d in store.list_dbs() if d["id"] in u.db_ids]
    return render_template("me.html", dbs=dbs)
