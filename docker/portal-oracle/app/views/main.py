"""Login, Home (blocos dos modulos), Minha area e enderecos antigos."""
import base64
import os

from flask import (abort, current_app, flash, g, jsonify, make_response, redirect, render_template, request,
                   url_for)
from werkzeug.security import check_password_hash

from .. import aparencia, auth, manutencao, settings, store
from . import bp


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("portal.home"))
    if request.method == "POST":
        ip = request.headers.get("X-Real-IP") or request.remote_addr or ""
        user = auth.authenticate(request.form.get("user", ""), request.form.get("password", ""), ip)
        if user:
            auth.login_session(user)
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("portal.home"))
        flash("Utilizador ou password inválidos.", "error")
    manut_msg = manutencao.estado()["html"] if g.get("manutencao") else None
    return render_template("login.html", no_admin=settings.admin() is None, manut_msg=manut_msg)


@bp.route("/i18n/<codigo>.js")
def i18n_js(codigo):
    """Dicionario de uma lingua para o static/i18n.js (publico: tambem e usado na pagina de login)."""
    from .. import i18n
    if not i18n.CODIGO_RE.match(codigo) or codigo not in {x["codigo"] for x in i18n.linguas()}:
        abort(404)
    resp = make_response(i18n.js(codigo))
    resp.headers["Content-Type"] = "application/javascript; charset=utf-8"
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable" if request.args.get("v") else "no-cache"
    return resp


@bp.route("/idioma", methods=["POST"])
def idioma():
    """Muda a lingua (do utilizador, e num cookie para a pagina de login)."""
    from .. import i18n
    codigo = (request.get_json(silent=True) or request.form).get("lingua", "")
    user = g.get("user")
    if not i18n.escolher(str(user.id) if user else None, codigo):
        return jsonify(ok=False, error="Língua desconhecida."), 400
    resp = jsonify(ok=True)
    resp.set_cookie("lingua", codigo, max_age=365 * 86400, samesite="Lax", httponly=True,
                    secure=current_app.config.get("SESSION_COOKIE_SECURE", False))
    return resp


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
    from .. import layouts
    return render_template("home.html", cartoes=_cartoes_home(),
                           layout=layouts.obter("home", str(g.user.id)), ocultos_padrao=_ocultos_padrao())


@bp.route("/home/cartoes")
@auth.login_required
def home_cartoes():
    """Conteudo atualizado dos cartoes (a Home atualiza-os de x em x segundos)."""
    return jsonify({c["id"]: c["html"] for c in _cartoes_home()})


@bp.route("/painel/layout/<painel>", methods=["POST"])
@auth.login_required
def painel_layout(painel):
    from .. import layouts
    try:
        layouts.guardar(painel, str(g.user.id), request.get_json(silent=True) or {})
    except ValueError:
        abort(404)
    return jsonify(ok=True)


def _definicoes_home() -> list[tuple[dict, dict, object]]:
    """[(cartao, contexto, modulo)] visiveis para o utilizador, pela ordem por omissao."""
    from .. import modulos
    out = []
    for m in g.user.modulos:
        for cid, c in m.cartoes.items():
            if not c.get("perm") or g.user.can(c["perm"]):
                out.append(({"id": cid, **c}, m, m.home_context))
    out.append(({"id": "portal.tarefas", "titulo": "Tarefas", "icone": "⏳", "template": "home/c_tarefas.html",
                 "link": "oracle.jobs_list" if "oracle" in modulos.ativos() and g.user.can("modulo.oracle") else None,
                 "link_txt": "Execuções"}, None, _ctx_tarefas))
    out.append(({"id": "portal.notificacoes", "titulo": "Notificações", "icone": "🔔", "template": "home/c_notificacoes.html",
                 "link": "smtp.notificacoes" if "smtp" in modulos.ativos() and g.user.can("modulo.smtp") else None,
                 "link_txt": "Regras de notificação"}, None, _ctx_notificacoes))
    if g.user.can("config.portal"):
        out.append(({"id": "portal.estado", "titulo": "Portal", "icone": "🧭", "template": "home/c_portal.html",
                     "link": "portal.config", "link_txt": "Configuração"}, None, _ctx_portal))
    return out


def _ocultos_padrao() -> list[str]:
    return [c["id"] for c, _m, _f in _definicoes_home() if c.get("oculto")]


def _cartoes_home() -> list[dict]:
    ctxs, out = {}, []
    for c, m, fn in _definicoes_home():
        k = m.chave if m else c["id"]
        try:
            if k not in ctxs:
                ctxs[k] = fn(g.user) if fn else {}
            html = render_template(c["template"], ctx=ctxs[k])
        except Exception:  # noqa: BLE001 - um cartao com erro nao deve partir a Home
            current_app.logger.exception("cartao %s", c["id"])
            html = '<p class="muted small">Não foi possível obter estes dados.</p>'
        out.append({**c, "html": html, "url": url_for(c["link"]) if c.get("link") else None})
    return out


def _ctx_tarefas(user) -> dict:
    """Em curso, na fila e agendadas: execucoes Oracle, atualizacoes em segundo plano e proximas tarefas agendadas."""
    from .. import agendamento, modulos, tarefas_bg
    execs = []
    if "oracle" in modulos.ativos() and user.can("modulo.oracle"):
        visiveis = {d["id"] for d in user.visible_dbs(store.list_dbs())}
        with store.tx() as t:
            rows = t.all("SELECT id, db_id, db_name, params, status, started_at, progress, current_action, agendado_para, "
                         "created_by FROM jobs WHERE status IN ('EM CURSO', 'PENDENTE', 'AGENDADO') ORDER BY "
                         "COALESCE(agendado_para, started_at)")
        import json as _json
        for r in rows:
            if r["db_id"] in visiveis:
                r["params"] = _json.loads(r["params"])
                execs.append(r)
    proximas = []
    if user.can_config:
        for c in agendamento.listar():
            if c.get("ativo") and c.get("modulo_ativo"):
                try:
                    proximas.append({"nome": c["nome"], "quando": agendamento.proxima(c).strftime("%Y-%m-%d %H:%M"),
                                     "a_correr": c.get("a_correr")})
                except Exception:  # noqa: BLE001
                    pass
        proximas.sort(key=lambda x: x["quando"])
    return {"execs": execs, "bg": [x for x in tarefas_bg.tarefas(user.username) if x["estado"] == "a_atualizar"],
            "proximas": proximas[:5]}


def _ctx_notificacoes(user) -> dict:
    from .. import eventos
    return {"itens": eventos.listar(str(user.id), 6)}


def _ctx_portal(user) -> dict:
    from .. import agente_info, modulos
    b = store.list_backups()
    st = store.get()
    return {"backup": b[0] if b else None, "agente": agente_info.estado(), "manutencao": manutencao.ativa(),
            "armazenamento": settings.STORAGE_TYPES.get(st.kind, st.kind),
            "modulos": [modulos.REGISTO[k].nome for k in modulos.ativos() if k in modulos.REGISTO],
            "utilizadores": len([u for u in store.list_users() if u.get("active")]) + 1}


# Barra "A atualizar": tarefas em segundo plano (cache de dados lentos) do utilizador
@bp.route("/api/tarefas")
@auth.login_required
def api_tarefas():
    from .. import tarefas_bg
    return jsonify(tarefas=tarefas_bg.tarefas(g.user.username), processos=_processos())


def _processos(recentes: int = 20) -> list[dict]:
    """Execucoes Oracle e pedidos aos agentes em curso (ou acabados ha menos de 'recentes' s), para a caixa
    de processos em baixo a direita. So dos modulos ativos a que o utilizador tem acesso."""
    import datetime
    from .. import modulos
    agora = datetime.datetime.now()
    lim = (agora - datetime.timedelta(seconds=recentes)).strftime("%Y-%m-%d %H:%M:%S")

    def seg(v):
        try:
            return max(0, int((agora - datetime.datetime.fromisoformat(str(v)[:19].replace("T", " "))).total_seconds()))
        except (TypeError, ValueError):
            return None

    def dur(a, b):
        try:
            return int((datetime.datetime.fromisoformat(str(b)[:19].replace("T", " "))
                        - datetime.datetime.fromisoformat(str(a)[:19].replace("T", " "))).total_seconds())
        except (TypeError, ValueError):
            return None
    out = []
    ativos = modulos.ativos()
    pode = lambda k: k in ativos and k in modulos.REGISTO and g.user.can(modulos.REGISTO[k].acesso)  # noqa: E731
    try:
        if pode("oracle"):
            with store.tx() as t:
                rs = t.all("SELECT id, db_name, params, status, started_at, finished_at, current_action, progress, created_by, via "
                           "FROM jobs WHERE status IN ('EM CURSO', 'PENDENTE') OR finished_at >= ? ORDER BY id DESC LIMIT 30", (lim,))
            for r in rs:
                a = r["status"] in ("EM CURSO", "PENDENTE")
                det = (r["current_action"] or "") if a else ""
                pct = None
                if a and r["progress"] is not None:            # jobs.progress esta em permilagem (0-1000)
                    pct = round(min(max(float(r["progress"]), 0), 1000) / 10, 1)
                    txt = f"{pct:.1f}".rstrip("0").rstrip(".").replace(".", ",") + "%"
                    det = f"{det} · {txt}" if det else txt
                out.append({"chave": f"job:{r['id']}", "grupo": "oracle", "titulo": f"Shrink #{r['id']} · {r['db_name']}",
                            "estado": "a_atualizar" if a else ("ok" if r["status"] == "OK" else "erro"),
                            "detalhe": det or ("na fila" if r["status"] == "PENDENTE" else ""), "por": r["created_by"],
                            "segundos": seg(r["started_at"]) if a else None, "duracao": None if a else dur(r["started_at"], r["finished_at"]),
                            "progresso": pct, "link": url_for("oracle.job_detail", job_id=r["id"]),
                            "erro": r["status"].lower() if not a and r["status"] != "OK" else None})
    except Exception:  # noqa: BLE001 - a caixa de processos nunca pode partir a pagina
        current_app.logger.exception("caixa de processos (oracle)")
    try:
        if pode("clientes"):
            from .. import clientes
            with store.tx() as t:
                rs = t.all("SELECT c.id, c.cliente_id, c.tipo, c.args, c.estado, c.pedido_por, c.criado, c.terminado, k.nome AS cliente "
                           "FROM cliente_comandos c LEFT JOIN clientes k ON k.id = c.cliente_id "
                           "WHERE c.estado IN ('PENDENTE', 'EM CURSO') OR c.terminado >= ? ORDER BY c.id DESC LIMIT 30", (lim,))
            for r in rs:
                a = r["estado"] in ("PENDENTE", "EM CURSO")
                out.append({"chave": f"cmd:{r['id']}", "grupo": "clientes", "titulo": f"{r['cliente'] or '#' + str(r['cliente_id'])} · "
                            f"{clientes.descrever(r)}", "estado": "a_atualizar" if a else ("ok" if r["estado"] == "OK" else "erro"),
                            "detalhe": ("à espera do agente" if r["estado"] == "PENDENTE" else "em curso") if a else "",
                            "por": r["pedido_por"], "segundos": seg(r["criado"]) if a else None,
                            "duracao": None if a else dur(r["criado"], r["terminado"]),
                            "link": url_for("clientes.pedidos") + f"?cmd={r['id']}", "erro": None if a or r["estado"] == "OK" else "falhou"})
    except Exception:  # noqa: BLE001 - a caixa de processos nunca pode partir a pagina
        current_app.logger.exception("caixa de processos (clientes)")
    return out


# Sininho: notificacoes do utilizador (regras em SMTP > Notificacoes)
@bp.route("/notificacoes/api")
@auth.login_required
def notif_api():
    from .. import eventos
    k = str(g.user.id)
    itens = [{**i, "created_at": str(i["created_at"] or "")} for i in eventos.listar(k, min(int(request.args.get("n", 15)), 50))]
    return jsonify(n=eventos.nao_lidas(k), itens=itens)


@bp.route("/notificacoes/lida", methods=["POST"])
@auth.login_required
def notif_lida():
    from .. import eventos
    nid = (request.get_json(silent=True) or {}).get("id")
    eventos.marcar_lida(str(g.user.id), int(nid) if str(nid).isdigit() else None)
    return jsonify(ok=True, n=eventos.nao_lidas(str(g.user.id)))


@bp.route("/notificacoes/limpar", methods=["POST"])
@auth.login_required
def notif_limpar():
    from .. import eventos
    nid = (request.get_json(silent=True) or {}).get("id")
    eventos.limpar(str(g.user.id), int(nid) if str(nid).isdigit() else None)
    return jsonify(ok=True, n=eventos.nao_lidas(str(g.user.id)))


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
                if u.auth_tipo == "ldap":
                    raise ValueError("A password das contas LDAP muda-se no diretório (LDAP / Active Directory).")
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
