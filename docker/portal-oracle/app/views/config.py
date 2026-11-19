"""Configuracao do portal: Geral, Aparencia, Manutencao, Modulos, Armazenamento, Utilizadores, Perfis, Backups)."""
import os
import re

from flask import abort, flash, g, redirect, render_template, request, send_file, url_for

from .. import aparencia, auth, backup, crypto, manutencao, modulos, settings, store
from . import bp

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


@bp.route("/config")
@auth.login_required
def config():
    if g.user.can("config.portal"):
        return redirect(url_for("portal.cfg_general"))
    abort(403)


# ====================================================================== Portal: Geral
@bp.route("/config/portal", methods=["GET", "POST"])
@auth.requires("config.portal")
def cfg_general():
    if request.method == "POST":
        try:
            minutes = int(request.form.get("session_minutes", "480"))
            keep = int(request.form.get("backup_keep", "30"))
            if not 5 <= minutes <= 10080 or not 1 <= keep <= 1000:
                raise ValueError("Valores fora dos limites.")
            store.save_settings({"session_minutes": minutes, "backup_keep": keep})
            flash("Configuração guardada.", "ok")
            return redirect(url_for("portal.cfg_general"))
        except ValueError as exc:
            flash(f"Erro: {exc}", "error")
    return render_template("config/general.html", s=store.get_settings(), tab="portal", sub="general",
                           storage=store.get().describe())


# ====================================================================== Portal: Aparencia
@bp.route("/config/portal/appearance", methods=["GET", "POST"])
@auth.requires("config.portal")
def cfg_appearance():
    if request.method == "POST":
        try:
            if request.form.get("action") == "reset":
                store.save_settings(aparencia.DEFAULTS)
                flash("Aparência reposta: título, textos, ícone e tema por omissão.", "ok")
                return redirect(url_for("portal.cfg_appearance"))
            theme = request.form.get("theme", aparencia.DEFAULT_THEME)
            if theme not in aparencia.THEMES:
                raise ValueError("Tema inválido.")
            values = {k: aparencia.clean_text(k, request.form.get(k, "")) for k in aparencia.LIMITS}
            values["portal_name"] = values["portal_name"] or aparencia.DEFAULTS["portal_name"]
            values["theme"] = theme
            values["show_icon"] = "1" if request.form.get("show_icon") else "0"
            upload = request.files.get("favicon")
            if upload and upload.filename:
                values.update(aparencia.favicon_settings(upload.read(aparencia.FAVICON_MAX + 1)))
            elif request.form.get("favicon_remove"):
                values.update({"favicon_data": "", "favicon_type": "", "favicon_ver": ""})
            store.save_settings(values)
            flash("Aparência guardada.", "ok")
            return redirect(url_for("portal.cfg_appearance"))
        except ValueError as exc:
            flash(f"Erro: {exc}", "error")
    s = store.get_settings()
    return render_template("config/appearance.html", s=s, themes=aparencia.THEMES,
                           limits=aparencia.LIMITS, favicon_max=aparencia.FAVICON_MAX // 1024,
                           custom_icon=bool(s.get("favicon_ver")), tab="portal", sub="appearance")


# ====================================================================== Portal: Manutencao
@bp.route("/config/portal/maintenance", methods=["GET", "POST"])
@auth.requires("config.portal")
def cfg_maintenance():
    if request.method == "POST":
        action = request.form.get("action", "save")
        html_msg = request.form.get("html", "")
        if action == "reset":
            html_msg = manutencao.DEFAULT_HTML
        ativo = {"on": True, "off": False}.get(action, manutencao.ativa())
        manutencao.definir(ativo, g.user.username, html_msg)
        flash({"on": "Modo de manutenção ATIVO: só quem gere o portal consegue entrar.",
               "off": "Modo de manutenção desativado: o portal está disponível para todos.",
               "reset": "Mensagem reposta."}.get(action, "Mensagem guardada."), "ok")
        return redirect(url_for("portal.cfg_maintenance"))
    return render_template("config/maintenance.html", m=manutencao.estado(), tab="portal", sub="maintenance")


# ====================================================================== Portal: Modulos
@bp.route("/config/portal/modules", methods=["GET", "POST"])
@auth.requires("config.portal")
def cfg_modules():
    if request.method == "POST":
        chave = request.form.get("module", "")
        if chave not in modulos.REGISTO:
            abort(404)
        ativo = request.form.get("active") == "1"
        store.save_settings({f"module.{chave}": "1" if ativo else "0"})
        nome = modulos.REGISTO[chave].nome
        flash(f"Módulo {nome} {'ativado' if ativo else 'desativado'}.", "ok")
        return redirect(url_for("portal.cfg_modules"))
    ativos = modulos.ativos()
    perfis = store.list_profiles()
    lista = []
    for k in modulos.INSTALADOS:
        m = modulos.REGISTO.get(k)
        if m:
            com_acesso = [p["name"] for p in perfis if m.acesso in p["permissions"]]
            lista.append({"m": m, "ativo": k in ativos, "perfis": com_acesso})
    return render_template("config/modules.html", lista=lista, tab="portal", sub="modules")


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
                           tab="portal", sub="profiles",
                           registo=[modulos.REGISTO[k] for k in modulos.INSTALADOS if k in modulos.REGISTO])


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
            catalogo = modulos.todas_acoes()
            perms = [a for a in f.getlist("permissions") if a in catalogo]
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
                           editing=bool(pid), dbs=store.list_dbs(), tab="portal", sub="profiles",
                           registo=[modulos.REGISTO[k] for k in modulos.INSTALADOS if k in modulos.REGISTO],
                           ativos=modulos.ativos(), acoes_portal=modulos.ACOES_PORTAL)


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
