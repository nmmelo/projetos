"""Operacoes sobre as bases de dados Oracle: tablespaces, shrink e execucoes."""
import oracledb
from flask import Response, abort, flash, g, jsonify, redirect, render_template, request, url_for

from .. import auth, crypto, jobs, oracle, store
from . import bp


def _db_or_403(db_id) -> dict:
    db = store.get_db(db_id) or abort(404)
    if not g.user.can_db(db["id"]):
        abort(403)
    return db


def _can_shrink() -> bool:
    return g.user.can("shrink.analise") or g.user.can("shrink.executar")


@bp.route("/api/databases/<int:db_id>/tablespaces")
@auth.login_required
def api_tablespaces(db_id):
    if not (_can_shrink() or g.user.can("tablespaces.ver")):
        abort(403)
    db = _db_or_403(db_id)
    try:
        conn = oracle.connect(db, crypto.decrypt(db["password_enc"]))
        try:
            return jsonify(ok=True, tablespaces=oracle.list_tablespaces(conn))
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, error=str(exc)), 502


@bp.route("/report")
@auth.requires("tablespaces.ver")
def report():
    dbs = g.user.visible_dbs(store.list_dbs())
    db_id = request.args.get("db", type=int)
    exact = request.args.get("exact") == "1"
    rows, error = None, None
    if db_id:
        db = _db_or_403(db_id)
        try:
            conn = oracle.connect(db, crypto.decrypt(db["password_enc"]))
            try:
                rows = oracle.tablespace_report(conn, exact)
            finally:
                conn.close()
        except (oracledb.Error, RuntimeError) as exc:
            error = str(exc)
    return render_template("report.html", dbs=dbs, db_id=db_id, rows=rows, error=error, exact=exact)


@bp.route("/shrink", methods=["GET", "POST"])
@auth.login_required
def shrink():
    if not _can_shrink():
        abort(403)
    if request.method == "POST":
        try:
            db = _db_or_403(int(request.form.get("db_id", "0")))
            params = oracle.validate_shrink_params(request.form)
            need = "shrink.executar" if params["modo"] == "EXECUTAR" else "shrink.analise"
            if not g.user.can(need):
                raise oracle.ValidationError(f"O seu perfil não permite o modo {params['modo']}.")
            if params["modo"] == "EXECUTAR" and request.form.get("confirm") != "on":
                raise oracle.ValidationError("Para EXECUTAR tem de confirmar a operação.")
            job_id = jobs.start(db["id"], params, g.user.username)
            return redirect(url_for("portal.job_detail", job_id=job_id))
        except (oracle.ValidationError, ValueError) as exc:
            flash(str(exc), "error")
    return render_template("shrink.html", dbs=g.user.visible_dbs(store.list_dbs()),
                           sel_db=request.args.get("db", type=int),
                           can_exec=g.user.can("shrink.executar"), can_analise=g.user.can("shrink.analise"))


@bp.route("/jobs")
@auth.login_required
def jobs_list():
    rows = [j for j in store.list_jobs() if g.user.can_db(j["db_id"])]
    return render_template("jobs.html", jobs=rows)


def _job_or_403(job_id) -> dict:
    job = store.get_job(job_id) or abort(404)
    if not g.user.can_db(job["db_id"]):
        abort(403)
    return job


@bp.route("/jobs/<int:job_id>")
@auth.login_required
def job_detail(job_id):
    job = _job_or_403(job_id)
    can_cancel = g.user.can("shrink.executar") or job["created_by"] == g.user.username
    return render_template("job.html", job=job, can_cancel=can_cancel)


@bp.route("/api/jobs/<int:job_id>")
@auth.login_required
def api_job(job_id):
    job = _job_or_403(job_id)
    return jsonify({k: job[k] for k in ("status", "current_action", "finished_at", "error", "output")})


@bp.route("/jobs/<int:job_id>/log")
@auth.login_required
def job_log(job_id):
    job = _job_or_403(job_id)
    text = (f"Execucao #{job['id']} - BD {job['db_name']} - {job['started_at']} a {job['finished_at']}\n"
            f"Parametros: {job['params']}\nEstado: {job['status']}\nPor: {job['created_by']}\n\n"
            f"{job['output'] or ''}\n" + (f"\nERRO: {job['error']}\n" if job["error"] else ""))
    fname = f"shrink_{job['db_name']}_{job['params']['ts']}_{job['id']}.log".replace(" ", "_")
    return Response(text, mimetype="text/plain", headers={"Content-Disposition": f"attachment; filename={fname}"})


@bp.route("/jobs/<int:job_id>/cancel", methods=["POST"])
@auth.login_required
def job_cancel(job_id):
    job = _job_or_403(job_id)
    if not (g.user.can("shrink.executar") or job["created_by"] == g.user.username):
        abort(403)
    try:
        jobs.cancel(job_id)
        flash("Pedido de cancelamento enviado (KILL SESSION).", "ok")
    except Exception as exc:  # noqa: BLE001
        flash(f"Não foi possível cancelar: {exc}", "error")
    return redirect(url_for("portal.job_detail", job_id=job_id))
