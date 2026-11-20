"""Configuracao do portal: Geral, Aparencia, Manutencao, Modulos, Armazenamento, Utilizadores, Perfis, Backups)."""
import os
import re

from flask import abort, flash, g, jsonify, make_response, redirect, render_template, request, send_file, url_for

from .. import agendamento, agente_info, aparencia, auth, backup, crypto, i18n, manutencao, modulos, settings, store
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
                           custom_icon=bool(s.get("favicon_ver")), tab="portal", sub="appearance", sub2="geral")


# ====================================================================== Portal: Aparencia > Linguagem
def _lingua_ou_404(codigo: str) -> dict:
    l = next((x for x in i18n.linguas() if x["codigo"] == codigo), None)
    if not l:
        abort(404)
    return l


@bp.route("/config/portal/appearance/linguas", methods=["GET", "POST"])
@auth.requires("config.portal")
def cfg_linguas():
    if request.method == "POST":
        acao = request.form.get("acao", "")
        lista = i18n.linguas()
        try:
            if acao == "adicionar":
                cod = request.form.get("codigo", "").strip()
                nome = request.form.get("nome", "").strip()[:40]
                if not i18n.CODIGO_RE.match(cod):
                    raise ValueError("Código inválido: use o código ISO da língua (ex. es, fr, pt-BR).")
                if any(x["codigo"] == cod for x in lista):
                    raise ValueError(f"A língua '{cod}' já existe.")
                if not nome:
                    raise ValueError("Indique o nome da língua (ex. Español).")
                lista.append({"codigo": cod, "nome": nome, "ativa": False})
                i18n.guardar_linguas(lista)
                flash(f"Língua {nome} acrescentada (inativa). Traduza os textos ou importe um XML e depois ative-a.", "ok")
            elif acao == "guardar":
                ativas = set(request.form.getlist("ativa"))
                for x in lista:
                    x["ativa"] = x["base"] or x["codigo"] in ativas
                    novo = request.form.get(f"nome_{x['codigo']}", "").strip()[:40]
                    if novo:
                        x["nome"] = novo
                i18n.guardar_linguas(lista)
                pad = request.form.get("padrao", i18n.BASE)
                if not any(x["codigo"] == pad and x["ativa"] for x in lista):
                    pad = i18n.BASE
                store.save_settings({"lingua_padrao": pad})
                flash("Línguas guardadas.", "ok")
            elif acao == "remover":
                cod = request.form.get("codigo", "")
                if cod in (i18n.BASE, "en"):
                    raise ValueError("O português e o inglês não podem ser removidos (pode desativar o inglês).")
                i18n.guardar_linguas([x for x in lista if x["codigo"] != cod])
                i18n.repor(cod)
                flash(f"Língua '{cod}' removida, com as suas traduções.", "ok")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("portal.cfg_linguas"))
    lista = i18n.linguas()
    for x in lista:
        x["cont"] = None if x["base"] else i18n.contagens(x["codigo"])
    return render_template("config/linguas.html", linguas=lista, padrao=i18n.padrao(), total=len(i18n.catalogo()),
                           tab="portal", sub="appearance", sub2="linguas")


@bp.route("/config/portal/appearance/linguas/<codigo>")
@auth.requires("config.portal")
def cfg_traducoes(codigo):
    l = _lingua_ou_404(codigo)
    if l["base"]:
        flash("O português é a língua original do portal: não tem traduções.", "info")
        return redirect(url_for("portal.cfg_linguas"))
    return render_template("config/traducoes.html", l=l, cont=i18n.contagens(codigo),
                           tab="portal", sub="appearance", sub2="linguas")


@bp.route("/config/portal/appearance/linguas/<codigo>/api")
@auth.requires("config.portal")
def cfg_traducoes_api(codigo):
    _lingua_ou_404(codigo)
    return jsonify(linhas=i18n.listar(codigo), cont=i18n.contagens(codigo))


@bp.route("/config/portal/appearance/linguas/<codigo>/texto", methods=["POST"])
@auth.requires("config.portal")
def cfg_traducao_guardar(codigo):
    l = _lingua_ou_404(codigo)
    if l["base"]:
        abort(400)
    d = request.get_json(silent=True) or {}
    try:
        if d.get("repor"):
            o = i18n.normalizar(d.get("origem", ""))
            i18n.repor(codigo, [i18n.chave(o)])
        else:
            i18n.guardar(codigo, d.get("origem", ""), d.get("texto", ""), g.user.username)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    o = i18n.normalizar(d.get("origem", ""))
    linha = next((x for x in i18n.listar(codigo) if x["origem"] == o), None)
    return jsonify(ok=True, linha=linha, cont=i18n.contagens(codigo))


@bp.route("/config/portal/appearance/linguas/<codigo>/repor", methods=["POST"])
@auth.requires("config.portal")
def cfg_traducoes_repor(codigo):
    _lingua_ou_404(codigo)
    n = i18n.repor(codigo)
    flash(f"{n} alteração(ões) apagadas: voltam as traduções do ficheiro do portal.", "ok")
    return redirect(url_for("portal.cfg_traducoes", codigo=codigo))


@bp.route("/config/portal/appearance/linguas/<codigo>/exportar")
@auth.requires("config.portal")
def cfg_traducoes_exportar(codigo):
    l = _lingua_ou_404(codigo)
    resp = make_response(i18n.exportar(codigo))
    resp.headers["Content-Type"] = "application/xml; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="portal-traducoes-{l["codigo"]}.xml"'
    return resp


@bp.route("/config/portal/appearance/linguas/<codigo>/importar", methods=["POST"])
@auth.requires("config.portal")
def cfg_traducoes_importar(codigo):
    l = _lingua_ou_404(codigo)
    if l["base"]:
        abort(400)
    f = request.files.get("xml")
    if not f or not f.filename:
        flash("Escolha o ficheiro XML.", "error")
        return redirect(url_for("portal.cfg_traducoes", codigo=codigo))
    r = i18n.validar_xml(codigo, f.read(5 * 1024 * 1024 + 1))
    token = i18n.guardar_pendente(r) if r["ok"] and (r["novas"] or r["mudadas"]) else ""
    return render_template("config/traducoes_importar.html", l=l, r=r, token=token, ficheiro=f.filename,
                           tab="portal", sub="appearance", sub2="linguas")


@bp.route("/config/portal/appearance/linguas/<codigo>/importar/aplicar", methods=["POST"])
@auth.requires("config.portal")
def cfg_traducoes_aplicar(codigo):
    _lingua_ou_404(codigo)
    try:
        n = i18n.aplicar_pendente(codigo, request.form.get("token", ""), g.user.username)
        flash(f"Importação concluída: {n} tradução(ões) atualizadas.", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("portal.cfg_traducoes", codigo=codigo))


# ====================================================================== Portal: Agendamento
@bp.route("/config/portal/agendamento", methods=["GET", "POST"])
@auth.requires("config.portal")
def cfg_agendamento():
    if request.method == "POST":
        ativo = request.form.get("ativo") == "1"
        store.save_settings({"agendamento_ativo": "1" if ativo else "0"})
        flash("Agendamentos ativos: as tarefas ativas correm à hora marcada." if ativo
              else "Agendamentos desativados: nenhuma tarefa corre até voltar a ativar.", "ok")
        return redirect(url_for("portal.cfg_agendamento"))
    agendamento.garantir_padrao()
    tarefas = agendamento.listar()
    grupos: dict[str, list] = {}
    for t in tarefas:
        grupos.setdefault(t["modulo"], []).append(t)
    return render_template("config/agendamento.html", grupos=grupos, ativo=agendamento.ativo_global(),
                           agente=agente_info.estado(), historico=agendamento.historico(None, 15),
                           nomes={t["id"]: t["nome"] for t in tarefas}, cron_backup=_cron_backup(),
                           tab="portal", sub="agendamento")


def _cron_backup() -> bool:
    """Ha um backup no cron do sistema (instalar.sh)? Para avisar que seriam dois."""
    try:
        return any("manage.py backup" in open(os.path.join("/etc/cron.d", f), encoding="utf-8", errors="ignore").read()
                   for f in os.listdir("/etc/cron.d"))
    except OSError:
        return False


@bp.route("/config/portal/agendamento/<tid>", methods=["GET", "POST"])
@auth.requires("config.portal")
def cfg_tarefa(tid):
    t = agendamento.obter(tid)
    if not t:
        abort(404)
    if request.method == "POST":
        dados = request.form.to_dict()
        dados["dias"] = request.form.getlist("dias")
        try:
            agendamento.guardar(tid, dados, g.user.username)
            flash(f"Agendamento \"{t['nome']}\" guardado.", "ok")
            return redirect(request.form.get("voltar") or url_for("portal.cfg_agendamento"))
        except ValueError as exc:
            flash(str(exc), "error")
            t = {**t, **{k: v for k, v in dados.items() if k in ("freq", "hora", "intervalo", "notificar")},
                 "ativo": bool(dados.get("ativo"))}
    return render_template("config/tarefa.html", t=t, freqs=agendamento.FREQS, dias=agendamento.DIAS,
                           notificar=agendamento.NOTIFICAR, historico=agendamento.historico(tid, 20),
                           voltar=request.args.get("voltar", ""), tab="portal", sub="agendamento")


@bp.route("/config/portal/agendamento/<tid>/ativar", methods=["POST"])
@auth.requires("config.portal")
def cfg_tarefa_ativar(tid):
    try:
        t = agendamento.ativar(tid, request.form.get("ativo") == "1", g.user.username)
        flash(f"\"{t['nome']}\" {'ativada' if t['ativo'] else 'desativada'}.", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(request.form.get("voltar") or url_for("portal.cfg_agendamento"))


@bp.route("/config/portal/agendamento/<tid>/executar", methods=["POST"])
@auth.requires("config.portal")
def cfg_tarefa_executar(tid):
    import threading
    t = agendamento.obter(tid)
    if not t:
        abort(404)
    if t["a_correr"]:
        flash("A tarefa já está a correr.", "error")
    else:
        threading.Thread(target=agendamento.executar, args=(tid, f"manual ({g.user.username})"), daemon=True).start()
        flash(f"\"{t['nome']}\" a correr: o resultado aparece no histórico daqui a pouco.", "ok")
    return redirect(request.form.get("voltar") or url_for("portal.cfg_tarefa", tid=tid))


# ====================================================================== Portal: Autenticacao
@bp.route("/config/portal/autenticacao")
@auth.requires("config.portal")
def cfg_autenticacao():
    ativo = "autenticacao" in modulos.ativos()
    ctx = {}
    if ativo:
        from ..modules.autenticacao import views as av
        ctx = av.contexto()
        cont = ctx["cont"]
    else:
        with store.tx() as t:
            rows = t.all("SELECT COALESCE(auth_tipo, 'local') AS tipo, COUNT(*) AS n FROM users WHERE active = 1 "
                         "GROUP BY COALESCE(auth_tipo, 'local')")
        cont = {"local": 0, "ldap": 0, "local_inativos": 0, "ldap_inativos": 0}
        for r in rows:
            cont[r["tipo"] if r["tipo"] in ("local", "ldap") else "local"] += int(r["n"])
    return render_template("config/autenticacao.html", modulo_ativo=ativo, cont=cont, pode=True,
                           admin=settings.admin(), tab="portal", sub="autenticacao", **{k: v for k, v in ctx.items() if k != "cont"})


# ====================================================================== Portal: Clientes (agente)
@bp.route("/config/portal/clientes")
@auth.requires("config.portal")
def cfg_clientes():
    if "clientes" in modulos.ativos() and g.user.can("modulo.clientes") and g.user.can("clientes.gerir"):
        return redirect(url_for("clientes.gerir"))
    return render_template("config/clientes_off.html", ativo="clientes" in modulos.ativos(), tab="portal", sub="clientes")


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


@bp.route("/config/portal/modules/<chave>", methods=["GET", "POST"])
@auth.requires("config.portal")
def cfg_module_edit(chave):
    from .. import eventos
    m = modulos.REGISTO.get(chave) or abort(404)
    perfis = store.list_profiles()
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()[:40]
        if not nome:
            flash("O nome não pode ficar vazio.", "error")
        else:
            store.save_settings({f"module_nome.{chave}": "" if nome == m.nome_original else nome,
                                 f"module.{chave}": "1" if request.form.get("ativo") else "0"})
            escolhidos = {int(x) for x in request.form.getlist("perfis") if x.isdigit()}
            mudou = 0
            for p in perfis:
                perms = set(p["permissions"])
                tem = m.acesso in perms
                if p["id"] in escolhidos and not tem:
                    perms.add(m.acesso)
                elif p["id"] not in escolhidos and tem:
                    perms = {x for x in perms if x != m.acesso and not x.startswith(f"{chave}.")}
                else:
                    continue
                store.save_profile({**p, "permissions": sorted(perms)}, p["id"])
                mudou += 1
            flash(f"Módulo {nome} guardado{f' ({mudou} perfil(is) alterado(s))' if mudou else ''}.", "ok")
            return redirect(url_for("portal.cfg_module_edit", chave=chave))
    evs = set(m.eventos)
    regras = [r for r in eventos.listar_regras() if r["evento"] in evs]
    todos = modulos.todos_eventos()
    for r in regras:
        r["evento_nome"] = todos.get(r["evento"], {}).get("nome", r["evento"])
    return render_template("config/module_edit.html", m=m, ativo=chave in modulos.ativos(), perfis=perfis,
                           tarefas=agendamento.listar(chave), regras=regras, eventos=todos,
                           smtp_ativo="smtp" in modulos.ativos(), ativo_global=agendamento.ativo_global(),
                           tab="portal", sub="modules")


@bp.route("/config/portal/modules/<chave>/regra/<int:rid>", methods=["POST"])
@auth.requires("config.portal")
def cfg_module_regra(chave, rid):
    from .. import eventos
    r = eventos.obter_regra(rid) or abort(404)
    r["ativo"] = not r["ativo"]
    eventos.guardar_regra(r, rid)
    flash(f"Regra \"{r['nome']}\" {'ativada' if r['ativo'] else 'desativada'}.", "ok")
    return redirect(url_for("portal.cfg_module_edit", chave=chave) + "#notificacoes")


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
            tipo = f.get("auth_tipo", "local")
            if tipo not in ("local", "ldap"):
                raise ValueError("Tipo de autenticação inválido.")
            if tipo == "ldap" and (u or {}).get("auth_tipo") != "ldap" and "autenticacao" not in modulos.ativos():
                raise ValueError("Para usar LDAP ative o módulo Autenticação (Configuração → Módulos).")
            pwd, rep = f.get("password", ""), f.get("repeat", "")
            if tipo == "ldap":
                pwd = rep = ""
            elif (not u or pwd) or (u and u.get("auth_tipo") == "ldap" and tipo == "local"):
                auth.check_password_strength(pwd)
                if pwd != rep:
                    raise ValueError("A password e a confirmação não coincidem.")
            profile_id = f.get("profile_id") or None
            data = {"username": username, "first_name": f.get("first_name", "").strip()[:100],
                    "last_name": f.get("last_name", "").strip()[:100], "name": f.get("name", "").strip()[:150],
                    "email": f.get("email", "").strip()[:255],
                    "profile_id": int(profile_id) if profile_id else None, "active": f.get("active") == "on",
                    "auth_tipo": tipo}
            store.save_user(data, auth.hash_password(pwd) if pwd else ("!ldap" if tipo == "ldap" else None), uid)
            flash("Utilizador guardado.", "ok")
            return redirect(url_for("portal.cfg_users"))
        except ValueError as exc:
            flash(str(exc), "error")
            u = {**(u or {}), **f.to_dict(), "active": f.get("active") == "on"}
    return render_template("config/user_form.html", u=u or {"active": 1}, editing=bool(uid),
                           profiles=store.list_profiles(), ldap_disponivel="autenticacao" in modulos.ativos(),
                           tab="portal", sub="users")


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


@bp.route("/config/portal/users/ldap")
@auth.requires("config.portal")
def cfg_user_ldap():
    """Dados de um utilizador no LDAP/AD (atributos da correspondencia) para preencher a ficha."""
    from .. import ldap_auth
    if not auth.ldap_ativo():
        return jsonify(ok=False, error="LDAP desligado (Configuração → Autenticação).")
    u = (request.args.get("u") or "").strip().lower()
    if not USERNAME_RE.match(u):
        return jsonify(ok=False, error="Utilizador inválido.")
    return jsonify(ldap_auth.consultar(u))


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
            adm = f.get("administrador") == "on"
            if adm != bool(p and p.get("administrador")) and not g.user.total:
                raise ValueError("Só um administrador pode dar ou retirar o perfil de administrador.")
            data = {"name": name, "description": f.get("description", "").strip()[:255],
                    "all_dbs": f.get("all_dbs") == "on", "permissions": perms, "administrador": adm,
                    "db_ids": [int(i) for i in f.getlist("db_ids") if i.isdigit()]}
            store.save_profile(data, pid)
            flash("Perfil guardado.", "ok")
            return redirect(url_for("portal.cfg_profiles"))
        except Exception as exc:  # noqa: BLE001 - nome duplicado, etc.
            flash(f"Erro: {exc}", "error")
            p = {**(p or {}), "name": f.get("name"), "description": f.get("description"),
                 "all_dbs": f.get("all_dbs") == "on", "permissions": f.getlist("permissions"),
                 "administrador": f.get("administrador") == "on",
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
