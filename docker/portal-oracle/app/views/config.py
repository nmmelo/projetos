"""Configuracao: abas 'Bases de dados' e 'Portal' (Geral, Armazenamento, Utilizadores, Perfis, Backups)."""
import os
import re

from flask import abort, flash, g, redirect, render_template, request, send_file, url_for

from .. import auth, backup, crypto, oracle, settings, store
from . import bp

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


@bp.route("/config")
@auth.login_required
def config():
    if g.user.can("config.bds"):
        return redirect(url_for("portal.cfg_dbs"))
    if g.user.can("config.portal"):
        return redirect(url_for("portal.cfg_general"))
    abort(403)


# ====================================================================== Bases de dados Oracle
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


@bp.route("/config/databases")
@auth.requires("config.bds")
def cfg_dbs():
    return render_template("config/dbs.html", dbs=store.list_dbs(), tab="dbs")


@bp.route("/config/databases/new", methods=["GET", "POST"])
@bp.route("/config/databases/<int:db_id>/edit", methods=["GET", "POST"])
@auth.requires("config.bds")
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
            return redirect(url_for("portal.cfg_dbs", _anchor=f"db{new_id}"))
        except Exception as exc:  # noqa: BLE001 - validacao, nome duplicado, ...
            flash(f"Erro: {exc}", "error")
            db = {**(db or {}), **request.form.to_dict()}
    return render_template("config/db_form.html", db=db or {}, editing=bool(db_id), tab="dbs")


@bp.route("/config/databases/<int:db_id>/delete", methods=["POST"])
@auth.requires("config.bds")
def cfg_db_delete(db_id):
    store.delete_db(db_id)
    flash("Base de dados removida.", "ok")
    return redirect(url_for("portal.cfg_dbs"))


@bp.route("/config/databases/<int:db_id>/test", methods=["POST"])
@auth.requires("config.bds")
def cfg_db_test(db_id):
    db = store.get_db(db_id) or abort(404)
    try:
        conn = oracle.connect(db, crypto.decrypt(db["password_enc"]))
        info = oracle.test_connection(conn)
        conn.close()
        flash(f"{db['name']}: ligação OK — utilizador {info['user']}, contentor {info['container']}, "
              f"BD {info['db']}, versão {info['version']}", "ok")
    except Exception as exc:  # noqa: BLE001
        flash(f"{db['name']}: falha na ligação — {exc}", "error")
    return redirect(url_for("portal.cfg_dbs"))


# ====================================================================== Portal: Geral
@bp.route("/config/portal", methods=["GET", "POST"])
@auth.requires("config.portal")
def cfg_general():
    if request.method == "POST":
        try:
            name = request.form.get("portal_name", "").strip()[:80] or store.DEFAULT_SETTINGS["portal_name"]
            minutes = int(request.form.get("session_minutes", "480"))
            keep = int(request.form.get("backup_keep", "30"))
            if not 5 <= minutes <= 10080 or not 1 <= keep <= 1000:
                raise ValueError("Valores fora dos limites.")
            store.save_settings({"portal_name": name, "session_minutes": minutes, "backup_keep": keep})
            flash("Configuração guardada.", "ok")
            return redirect(url_for("portal.cfg_general"))
        except ValueError as exc:
            flash(f"Erro: {exc}", "error")
    return render_template("config/general.html", s=store.get_settings(), tab="portal", sub="general",
                           storage=store.get().describe())


# ====================================================================== Portal: Armazenamento
def _storage_from_form(form) -> dict:
    kind = form.get("type", "sqlite")
    if kind not in settings.STORAGE_TYPES:
        raise ValueError("Tipo inválido.")
    if kind == "sqlite":
        path = form.get("path", "").strip() or "portal.sqlite3"
        if not re.match(r"^[A-Za-z0-9_./-]+$", path):
            raise ValueError("Caminho do ficheiro SQLite inválido.")
        return {"type": "sqlite", "path": path}
    cfg = {"type": kind, "host": form.get("host", "").strip(),
           "port": int(form.get("port") or settings.DEFAULT_PORTS[kind]),
           "database": form.get("database", "").strip(), "user": form.get("user", "").strip()}
    if not cfg["host"] or not cfg["database"] or not cfg["user"]:
        raise ValueError("Host, base de dados e utilizador são obrigatórios.")
    pwd = form.get("password", "")
    current = settings.storage_config()
    if pwd:
        cfg["password_enc"] = crypto.encrypt(pwd)
    elif current.get("type") == kind and current.get("password_enc"):
        cfg["password_enc"] = current["password_enc"]  # manter a password guardada
    else:
        raise ValueError("Indique a password.")
    return cfg


@bp.route("/config/portal/storage", methods=["GET", "POST"])
@auth.admin_required
def cfg_storage():
    current = settings.storage_config()
    form_cfg = current
    if request.method == "POST":
        try:
            cfg = _storage_from_form(request.form)
            form_cfg = cfg
            target = store.Store(cfg)
            target.init_schema()                       # testa a ligacao e cria as tabelas
            with target.tx() as t:
                n_target = sum(t.val(f"SELECT COUNT(*) FROM {tb}") for tb in store.TABLES)
            if request.form.get("action") == "test":
                flash(f"Ligação OK a {target.describe()}. Tabelas criadas/verificadas "
                      f"({n_target} registo(s) já existentes).", "ok")
            else:
                same = (cfg.get("type"), cfg.get("host"), cfg.get("database"), cfg.get("path")) == \
                       (current.get("type"), current.get("host"), current.get("database"), current.get("path"))
                if request.form.get("copy") == "on" and not same:
                    data = store.get().dump()
                    target.load(data)
                    flash(f"Dados copiados ({sum(len(v) for v in data.values())} registos).", "ok")
                settings.save_storage_config(cfg)
                flash(f"O portal passou a usar {target.describe()}.", "ok")
                return redirect(url_for("portal.cfg_storage"))
        except ImportError as exc:
            flash(f"Driver Python em falta: {exc}. Instale-o (requirements.txt) e recrie o contentor.", "error")
        except Exception as exc:  # noqa: BLE001
            flash(f"Erro: {exc}", "error")
    return render_template("config/storage.html", cfg=form_cfg, types=settings.STORAGE_TYPES,
                           ports=settings.DEFAULT_PORTS, active=store.get().describe(),
                           tab="portal", sub="storage")


# ====================================================================== Portal: Utilizadores
@bp.route("/config/portal/users")
@auth.requires("config.portal")
def cfg_users():
    return render_template("config/users.html", users=store.list_users(), admin=settings.admin(),
                           tab="portal", sub="users")


@bp.route("/config/portal/users/new", methods=["GET", "POST"])
@bp.route("/config/portal/users/<int:uid>/edit", methods=["GET", "POST"])
@auth.requires("config.portal")
def cfg_user_form(uid=None):
    u = store.get_user(uid=uid) if uid else None
    if uid and not u:
        abort(404)
    if request.method == "POST":
        f = request.form
        try:
            username = f.get("username", "").strip().lower()
            if not USERNAME_RE.match(username):
                raise ValueError("Utilizador inválido: 3-64 caracteres, minúsculas, números, '.', '_' ou '-'.")
            if settings.admin() and username == settings.admin()["username"]:
                raise ValueError("Esse nome está reservado para o administrador local.")
            other = store.get_user(username=username)
            if other and other["id"] != uid:
                raise ValueError("Já existe um utilizador com esse nome.")
            pwd, rep = f.get("password", ""), f.get("repeat", "")
            if not u or pwd:
                auth.check_password_strength(pwd)
                if pwd != rep:
                    raise ValueError("A password e a confirmação não coincidem.")
            profile_id = f.get("profile_id") or None
            data = {"username": username, "name": f.get("name", "").strip()[:150],
                    "email": f.get("email", "").strip()[:255],
                    "profile_id": int(profile_id) if profile_id else None, "active": f.get("active") == "on"}
            store.save_user(data, auth.hash_password(pwd) if pwd else None, uid)
            flash("Utilizador guardado.", "ok")
            return redirect(url_for("portal.cfg_users"))
        except ValueError as exc:
            flash(str(exc), "error")
            u = {**(u or {}), **f.to_dict(), "active": f.get("active") == "on"}
    return render_template("config/user_form.html", u=u or {"active": 1}, editing=bool(uid),
                           profiles=store.list_profiles(), tab="portal", sub="users")


@bp.route("/config/portal/users/<int:uid>/delete", methods=["POST"])
@auth.requires("config.portal")
def cfg_user_delete(uid):
    if g.user.id == uid:
        flash("Não pode remover o seu próprio utilizador.", "error")
    else:
        store.delete_user(uid)
        flash("Utilizador removido.", "ok")
    return redirect(url_for("portal.cfg_users"))


# ====================================================================== Portal: Perfis
@bp.route("/config/portal/profiles")
@auth.requires("config.portal")
def cfg_profiles():
    dbs = {d["id"]: d["name"] for d in store.list_dbs()}
    return render_template("config/profiles.html", profiles=store.list_profiles(), dbs=dbs,
                           tab="portal", sub="profiles")


@bp.route("/config/portal/profiles/new", methods=["GET", "POST"])
@bp.route("/config/portal/profiles/<int:pid>/edit", methods=["GET", "POST"])
@auth.requires("config.portal")
def cfg_profile_form(pid=None):
    p = store.get_profile(pid) if pid else None
    if pid and not p:
        abort(404)
    if request.method == "POST":
        f = request.form
        try:
            name = f.get("name", "").strip()[:100]
            if not name:
                raise ValueError("O nome do perfil é obrigatório.")
            perms = [a for a in f.getlist("permissions") if a in auth.ACTIONS]
            data = {"name": name, "description": f.get("description", "").strip()[:255],
                    "all_dbs": f.get("all_dbs") == "on", "permissions": perms,
                    "db_ids": [int(i) for i in f.getlist("db_ids") if i.isdigit()]}
            store.save_profile(data, pid)
            flash("Perfil guardado.", "ok")
            return redirect(url_for("portal.cfg_profiles"))
        except Exception as exc:  # noqa: BLE001 - nome duplicado, etc.
            flash(f"Erro: {exc}", "error")
            p = {**(p or {}), "name": f.get("name"), "description": f.get("description"),
                 "all_dbs": f.get("all_dbs") == "on", "permissions": f.getlist("permissions"),
                 "db_ids": [int(i) for i in f.getlist("db_ids") if i.isdigit()]}
    return render_template("config/profile_form.html", p=p or {"permissions": [], "db_ids": []},
                           editing=bool(pid), dbs=store.list_dbs(), tab="portal", sub="profiles")


@bp.route("/config/portal/profiles/<int:pid>/delete", methods=["POST"])
@auth.requires("config.portal")
def cfg_profile_delete(pid):
    try:
        store.delete_profile(pid)
        flash("Perfil removido.", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("portal.cfg_profiles"))


# ====================================================================== Portal: Backups
@bp.route("/config/portal/backups")
@auth.requires("config.portal")
def cfg_backups():
    rows = store.list_backups()
    for r in rows:
        r["exists"] = os.path.exists(backup.file_path(r))
    return render_template("config/backups.html", backups=rows, tab="portal", sub="backups")


@bp.route("/config/portal/backups/create", methods=["POST"])
@auth.requires("config.portal")
def cfg_backup_create():
    backup.create(g.user.username, "manual", request.form.get("note", "").strip())
    flash("Backup criado.", "ok")
    return redirect(url_for("portal.cfg_backups"))


@bp.route("/config/portal/backups/<int:bid>/download")
@auth.requires("config.portal")
def cfg_backup_download(bid):
    row = store.get_backup(bid) or abort(404)
    path = backup.file_path(row)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=row["filename"], mimetype="application/json")


@bp.route("/config/portal/backups/<int:bid>/delete", methods=["POST"])
@auth.requires("config.portal")
def cfg_backup_delete(bid):
    try:
        backup.delete(bid)
        flash("Backup e respetivo registo removidos.", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("portal.cfg_backups"))


def _do_import(data: dict, mode: str) -> None:
    backup.create(g.user.username, "pre-import", f"Automático antes de importar ({mode})")
    st = backup.import_data(data, mode)
    flash(f"Importação concluída ({mode}): {st['bds']} base(s) de dados, {st['perfis']} perfil(is), "
          f"{st['utilizadores']} utilizador(es). Foi criado um backup automático antes da importação.", "ok")
    if st["chave_diferente"]:
        flash("Atenção: o backup foi criado com outra chave de cifra (fernet.key). As passwords das bases "
              "de dados Oracle não vão poder ser decifradas — volte a introduzi-las.", "error")


@bp.route("/config/portal/backups/<int:bid>/import", methods=["POST"])
@auth.requires("config.portal")
def cfg_backup_import(bid):
    try:
        _do_import(backup.load_file(bid), request.form.get("mode", "juntar"))
    except Exception as exc:  # noqa: BLE001
        flash(f"Erro na importação: {exc}", "error")
    return redirect(url_for("portal.cfg_backups"))


@bp.route("/config/portal/backups/upload", methods=["POST"])
@auth.requires("config.portal")
def cfg_backup_upload():
    fh = request.files.get("file")
    try:
        if not fh or not fh.filename:
            raise ValueError("Escolha um ficheiro.")
        raw = fh.read()
        bid = backup.save_upload(raw, g.user.username, fh.filename)
        if request.form.get("now") == "on":
            _do_import(backup.load_file(bid), request.form.get("mode", "juntar"))
        else:
            flash("Ficheiro carregado e registado. Use 'Importar' na lista para o aplicar.", "ok")
    except Exception as exc:  # noqa: BLE001
        flash(f"Erro: {exc}", "error")
    return redirect(url_for("portal.cfg_backups"))
