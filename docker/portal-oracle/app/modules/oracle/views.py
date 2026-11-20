"""Rotas do modulo Oracle: tablespaces, shrink, execucoes e definicoes (bases de dados)."""
import oracledb
from flask import Response, abort, flash, g, jsonify, redirect, render_template, request, url_for

from ... import auth, crypto, store
from . import bd as oracle
from . import bp, jobs, MODULO


def _db_or_403(db_id) -> dict:
    db = store.get_db(db_id) or abort(404)
    if not g.user.can_db(db["id"]):
        abort(403)
    return db


def _can_shrink() -> bool:
    return g.user.can("oracle.shrink.analise") or g.user.can("oracle.shrink.executar")


def _abas_visiveis() -> list:
    out = []
    for endpoint, rotulo, perm in MODULO.abas:
        perms = perm if isinstance(perm, tuple) else ((perm,) if perm else ())
        if not perms or any(g.user.can(p) for p in perms):
            out.append((endpoint, rotulo))
    return out


@bp.app_context_processor
def _ctx():
    return {"oracle_abas": _abas_visiveis} if g.get("user") else {}


@bp.route("/")
@auth.requires(MODULO.acesso)
def index():
    abas = _abas_visiveis()
    return redirect(url_for(abas[0][0])) if abas else abort(403)


# ====================================================================== Tablespaces
@bp.route("/api/databases/<int:db_id>/tablespaces")
@auth.login_required
def api_tablespaces(db_id):
    if not (_can_shrink() or g.user.can("oracle.tablespaces")):
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


@bp.route("/tablespaces")
@auth.requires("oracle.tablespaces")
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
    return render_template("oracle/report.html", dbs=dbs, db_id=db_id, rows=rows, error=error, exact=exact)


# ====================================================================== Shrink
@bp.route("/shrink", methods=["GET", "POST"])
@auth.login_required
def shrink():
    if not _can_shrink():
        abort(403)
    if request.method == "POST":
        try:
            db = _db_or_403(int(request.form.get("db_id", "0")))
            params = oracle.validate_shrink_params(request.form)
            need = "oracle.shrink.executar" if params["modo"] == "EXECUTAR" else "oracle.shrink.analise"
            if not g.user.can(need):
                raise oracle.ValidationError(f"O seu perfil não permite o modo {params['modo']}.")
            if params["modo"] == "EXECUTAR" and request.form.get("confirm") != "on":
                raise oracle.ValidationError("Para EXECUTAR tem de confirmar a operação.")
            job_id = jobs.start(db["id"], params, g.user.username)
            return redirect(url_for("oracle.job_detail", job_id=job_id))
        except (oracle.ValidationError, ValueError) as exc:
            flash(str(exc), "error")
    return render_template("oracle/shrink.html", dbs=g.user.visible_dbs(store.list_dbs()),
                           sel_db=request.args.get("db", type=int),
                           can_exec=g.user.can("oracle.shrink.executar"),
                           can_analise=g.user.can("oracle.shrink.analise"))


# ====================================================================== Execucoes
@bp.route("/execucoes")
@auth.login_required
def jobs_list():
    return render_template("oracle/jobs.html", can_delete=g.user.can("oracle.execucoes.apagar"))


def _resumo(j: dict) -> dict:
    p = j["params"]
    return {"id": j["id"], "db_id": j["db_id"], "db_name": j["db_name"], "ts": p.get("ts"), "modo": p.get("modo"),
            "status": j["status"], "resultado": j["resultado"], "n_erros": j.get("n_erros") or 0,
            "n_avisos": j.get("n_avisos") or 0, "started_at": j["started_at"], "finished_at": j["finished_at"],
            "duracao": _elapsed(j), "created_by": j["created_by"], "progress": j.get("progress"),
            "runner": j.get("runner") or "portal", "erro": (j.get("error") or "")[:300]}


@bp.route("/api/jobs")
@auth.login_required
def api_jobs():
    """Execucoes visiveis para a tabela. q = texto a procurar (tambem no output)."""
    q = (request.args.get("q") or "").strip()[:100]
    rows = [_resumo(j) for j in store.search_jobs(q) if g.user.can_db(j["db_id"])]
    return jsonify(ok=True, jobs=rows, q=q)


@bp.route("/execucoes/apagar", methods=["POST"])
@auth.requires("oracle.execucoes.apagar")
def jobs_delete():
    ids = request.form.getlist("ids") or (request.get_json(silent=True) or {}).get("ids", [])
    visiveis = {j["id"] for j in store.list_jobs(100000) if g.user.can_db(j["db_id"])}
    ids = [int(i) for i in ids if str(i).isdigit() and int(i) in visiveis]
    n = store.delete_jobs(ids)
    if request.is_json:
        return jsonify(ok=True, apagadas=n)
    flash(f"{n} execução(ões) apagada(s)." if n else "Nada apagado (execuções em curso não podem ser apagadas).",
          "ok" if n else "error")
    return redirect(url_for("oracle.jobs_list"))


def _job_or_403(job_id) -> dict:
    job = store.get_job(job_id) or abort(404)
    if not g.user.can_db(job["db_id"]):
        abort(403)
    return job


@bp.route("/execucoes/<int:job_id>")
@auth.login_required
def job_detail(job_id):
    job = _job_or_403(job_id)
    can_cancel = g.user.can("oracle.shrink.executar") or job["created_by"] == g.user.username
    return render_template("oracle/job.html", job=job, can_cancel=can_cancel, elapsed=_elapsed(job))


@bp.route("/api/jobs/<int:job_id>")
@auth.login_required
def api_job(job_id):
    job = _job_or_403(job_id)
    data = {k: job.get(k) for k in ("status", "current_action", "progress", "finished_at", "error", "output")}
    data["elapsed"] = _elapsed(job)
    if request.args.get("full"):
        data.update(_resumo(job), params=job["params"], error=job.get("error"))
    return jsonify(data)


def _elapsed(job) -> int:
    """Segundos desde o inicio (ate ao fim, se ja terminou), calculados no servidor."""
    from datetime import datetime
    fmt = "%Y-%m-%d %H:%M:%S"
    try:
        ini = datetime.strptime(job["started_at"], fmt)
        fim = datetime.strptime(job["finished_at"] or store.now(), fmt)
        return max(0, int((fim - ini).total_seconds()))
    except (TypeError, ValueError):
        return 0


@bp.route("/execucoes/<int:job_id>/log")
@auth.login_required
def job_log(job_id):
    job = _job_or_403(job_id)
    text = (f"Execucao #{job['id']} - BD {job['db_name']} - {job['started_at']} a {job['finished_at']}\n"
            f"Parametros: {job['params']}\nEstado: {job['status']}\nPor: {job['created_by']}\n\n"
            f"{job['output'] or ''}\n" + (f"\nERRO: {job['error']}\n" if job["error"] else ""))
    fname = f"shrink_{job['db_name']}_{job['params']['ts']}_{job['id']}.log".replace(" ", "_")
    return Response(text, mimetype="text/plain", headers={"Content-Disposition": f"attachment; filename={fname}"})


@bp.route("/execucoes/<int:job_id>/cancel", methods=["POST"])
@auth.login_required
def job_cancel(job_id):
    job = _job_or_403(job_id)
    if not (g.user.can("oracle.shrink.executar") or job["created_by"] == g.user.username):
        abort(403)
    try:
        jobs.cancel(job_id)
        flash("Pedido de cancelamento enviado (KILL SESSION).", "ok")
    except Exception as exc:  # noqa: BLE001
        flash(f"Não foi possível cancelar: {exc}", "error")
    return redirect(url_for("oracle.job_detail", job_id=job_id))


# ====================================================================== Definicoes (bases de dados)
def _form_to_db(form) -> dict:
    data = {
        "name": form.get("name", "").strip(),
        "host": form.get("host", "").strip(),
        "port": form.get("port", "1521").strip() or "1521",
        "sid": form.get("sid", "").strip() or None,
        "service_name": form.get("service_name", "").strip() or None,
        "pdb": form.get("pdb", "").strip().upper() or None,
        "username": form.get("username", "").strip(),
        "role": "SYSDBA" if form.get("role") == "SYSDBA" else "NORMAL",
    }
    if not data["name"] or not data["host"] or not data["username"]:
        raise ValueError("Nome, host e utilizador são obrigatórios.")
    if not data["port"].isdigit():
        raise ValueError("Porta inválida.")
    data["port"] = int(data["port"])
    if not data["sid"] and not data["service_name"]:
        raise ValueError("Indique o SID ou o Service Name.")
    if data["pdb"]:
        oracle.valid_ident(data["pdb"], "PDB")
    return data


@bp.route("/definicoes")
@auth.requires("oracle.definicoes")
def cfg_dbs():
    return render_template("oracle/dbs.html", dbs=store.list_dbs())


@bp.route("/definicoes/nova", methods=["GET", "POST"])
@bp.route("/definicoes/<int:db_id>/editar", methods=["GET", "POST"])
@auth.requires("oracle.definicoes")
def cfg_db_form(db_id=None):
    db = store.get_db(db_id) if db_id else None
    if db_id and not db:
        abort(404)
    if request.method == "POST":
        try:
            data = _form_to_db(request.form)
            pwd = request.form.get("password", "")
            if not db and not pwd:
                raise ValueError("A password é obrigatória.")
            new_id = store.save_db(data, crypto.encrypt(pwd) if pwd else None, db_id)
            flash("Base de dados guardada.", "ok")
            return redirect(url_for("oracle.cfg_dbs", _anchor=f"db{new_id}"))
        except Exception as exc:  # noqa: BLE001 - validacao, nome duplicado, ...
            flash(f"Erro: {exc}", "error")
            db = {**(db or {}), **request.form.to_dict()}
    return render_template("oracle/db_form.html", db=db or {}, editing=bool(db_id))


@bp.route("/definicoes/<int:db_id>/apagar", methods=["POST"])
@auth.requires("oracle.definicoes")
def cfg_db_delete(db_id):
    store.delete_db(db_id)
    flash("Base de dados removida.", "ok")
    return redirect(url_for("oracle.cfg_dbs"))


@bp.route("/definicoes/<int:db_id>/testar", methods=["POST"])
@auth.requires("oracle.definicoes")
def cfg_db_test(db_id):
    db = store.get_db(db_id) or abort(404)
    try:
        conn = oracle.connect(db, crypto.decrypt(db["password_enc"]))
        info = oracle.test_connection(conn)
        conn.close()
        rapido = info["hwm"] != "DBA_FREE_SPACE"
        flash(f"{db['name']}: ligação OK — utilizador {info['user']}, contentor {info['container']}, "
              f"BD {info['db']}, versão {info['version']}. Cálculo do HWM: {info['hwm']}"
              + (" (rápido e exato)." if rapido else
                 f" (estimativa rápida; exato só com DBA_EXTENTS, lento). Para o método rápido e exato, "
                 f"como SYS{' na PDB ' + info['container'] if info['container'] not in ('CDB$ROOT', None) else ''}: "
                 f"CREATE OR REPLACE VIEW SYS.X_$KTFBUE AS SELECT * FROM SYS.X$KTFBUE; "
                 f"GRANT SELECT ON SYS.X_$KTFBUE TO {info['user']};"), "ok")
    except Exception as exc:  # noqa: BLE001
        flash(f"{db['name']}: falha na ligação — {exc}", "error")
    return redirect(url_for("oracle.cfg_dbs"))


# ====================================================================== Bases de dados (estado e detalhe)
from . import inspecao  # noqa: E402


def _db_visivel(db_id: int) -> dict:
    db = store.get_db(db_id) or abort(404)
    if not g.user.can_db(db_id):
        abort(403)
    return db


def _portal_users(db_id: int) -> list[dict]:
    with store.tx() as t:
        return t.all("SELECT * FROM oracle_utilizadores WHERE db_id = ? ORDER BY username", (db_id,))


@bp.route("/bds")
@auth.requires("oracle.bds")
def bds():
    dbs = g.user.visible_dbs(store.list_dbs())
    return render_template("oracle/bds.html", dbs=dbs, estados=inspecao.ESTADOS)


@bp.route("/api/bds/<int:db_id>/resumo")
@auth.requires("oracle.bds")
def api_bd_resumo(db_id):
    db = _db_visivel(db_id)
    try:
        r = inspecao.resumo(db, crypto.decrypt(db["password_enc"]))
    except Exception as exc:  # noqa: BLE001
        r = {"ok": False, "estado": "erro", "cor": "bad", "estado_txt": f"Erro: {exc}", "erro": str(exc)}
    r["ligacao"] = {"utilizador": db["username"], "role": db["role"], "portal": db["username"].upper() in
                    {u["username"].upper() for u in _portal_users(db_id)}}
    return jsonify(r)


@bp.route("/api/bds/<int:db_id>/detalhe/<parte>")
@auth.requires("oracle.bds")
def api_bd_detalhe(db_id, parte):
    db = _db_visivel(db_id)
    extra = {"owner": request.args.get("owner", ""), "portal": [u["username"] for u in _portal_users(db_id)]}
    try:
        return jsonify(ok=True, **inspecao.detalhe(db, crypto.decrypt(db["password_enc"]), parte, extra))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, error=str(exc).splitlines()[0][:400])


# ====================================================================== Utilizador dedicado do portal
@bp.route("/definicoes/<int:db_id>/utilizador", methods=["GET", "POST"])
@auth.requires("oracle.definicoes")
def cfg_dboperator(db_id):
    db = store.get_db(db_id) or abort(404)
    resultado = None
    nome = (request.form.get("nome") or request.args.get("nome") or "DBOPERATOR").strip().upper()
    if request.method == "POST":
        pw, rep = request.form.get("password", ""), request.form.get("repeat", "")
        try:
            if pw != rep:
                raise ValueError("A password e a confirmação não coincidem.")
            resultado = inspecao.criar_dboperator(db, crypto.decrypt(db["password_enc"]), nome, pw)
            if resultado["ok"]:
                with store.tx() as t:
                    if not t.val("SELECT COUNT(*) FROM oracle_utilizadores WHERE db_id = ? AND username = ?",
                                 (db_id, resultado["nome"])):
                        t.insert("oracle_utilizadores", {"db_id": db_id, "username": resultado["nome"],
                                                         "finalidade": "ligação do portal (dboperator)",
                                                         "created_at": store.now(), "created_by": g.user.username})
                if request.form.get("usar"):
                    store.save_db({**db, "username": resultado["nome"], "role": "NORMAL"}, crypto.encrypt(pw), db_id)
                    flash(f"Utilizador {resultado['nome']} criado; a ligação {db['name']} passou a usá-lo (Normal).", "ok")
                    return redirect(url_for("oracle.cfg_dbs", _anchor=f"db{db_id}"))
                flash(f"Utilizador {resultado['nome']} criado com os privilégios do portal.", "ok")
            else:
                flash("Alguns comandos falharam: veja o resultado abaixo.", "error")
        except ValueError as exc:
            flash(str(exc), "error")
        except Exception as exc:  # noqa: BLE001
            flash(f"Falha na ligação: {str(exc).splitlines()[0][:300]}", "error")
    try:
        script_cdb = inspecao.texto_script(inspecao.script_dboperator(nome, True))
        script_pdb = inspecao.texto_script(inspecao.script_dboperator(nome, False))
    except ValueError as exc:
        flash(str(exc), "error")
        nome = "DBOPERATOR"
        script_cdb = inspecao.texto_script(inspecao.script_dboperator(nome, True))
        script_pdb = inspecao.texto_script(inspecao.script_dboperator(nome, False))
    return render_template("oracle/dboperator.html", db=db, nome=nome, grants=inspecao.GRANTS, script_cdb=script_cdb,
                           script_pdb=script_pdb, resultado=resultado, portal_users=_portal_users(db_id))
