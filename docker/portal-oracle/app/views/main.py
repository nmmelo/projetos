"""Login, Home (blocos dos modulos), Minha area e enderecos antigos."""
import base64
import os

from flask import (abort, current_app, flash, g, jsonify, make_response, redirect, render_template, request,
                   url_for)
from werkzeug.security import check_password_hash

from .. import aparencia, auth, manutencao, settings, store
from . import bp


def _proximo(nxt: str) -> str:
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("portal.home")


def _render_login(**kw):
    from .. import seguranca
    manut_msg = manutencao.estado()["html"] if g.get("manutencao") else None
    return render_template("login.html", no_admin=settings.admin() is None, manut_msg=manut_msg,
                           seg=seguranca.ativo(), **kw)


@bp.route("/login", methods=["GET", "POST"])
def login():
    from flask import session
    from .. import seguranca
    if g.user:
        return redirect(url_for("portal.home"))
    ip = seguranca.ip_cliente(request)
    ua = request.headers.get("User-Agent", "")
    seg = seguranca.ativo()
    nxt = request.args.get("next", "")
    tentou = False
    if request.method == "POST":
        tentou = True
        nome = request.form.get("user", "").strip()[:64]
        falta = seguranca.bloqueado_por_tentativas(ip) if seg else 0
        if falta:
            seguranca.auditar("tentativas", "BLOQUEADO", ip=ip, utilizador=nome, detalhe=f"login recusado (faltam {falta} min)",
                              caminho="/login", agente=ua)
            flash(f"Demasiadas tentativas falhadas a partir deste endereço. Tente de novo dentro de {falta} min.", "error")
        elif seg and seguranca.captcha_necessario(ip) and not seguranca.captcha_ok(session, request.form.get("pow", "")):
            seguranca.auditar("captcha", "FALHOU", ip=ip, utilizador=nome, detalhe="verificação 'não sou um robô' em falta ou inválida",
                              caminho="/login", agente=ua)
            flash("Confirme que não é um robô (caixa por baixo da password).", "error")
        else:
            user = auth.authenticate(nome, request.form.get("password", ""), ip)
            if user and seguranca.exige_2fa(user):
                session.clear()
                session["seg_2fa"] = {"uid": user.id, "ts": int(__import__("time").time()), "next": nxt, "falhas": 0}
                return redirect(url_for("portal.login_2fa"))
            if user:
                auth.login_session(user)
                if seg:
                    seguranca.auditar("login", "OK", ip=ip, utilizador=user.username, caminho="/login", agente=ua)
                return redirect(_proximo(nxt))
            motivo = g.get("login_motivo")
            if seg:
                seguranca.auditar("login", "INFO" if motivo else "FALHOU", ip=ip, utilizador=nome,
                                  detalhe=motivo or "utilizador ou password inválidos", caminho="/login", agente=ua)
                _avisar_bloqueio(ip, nome)
            flash(motivo or "Utilizador ou password inválidos.", "warn" if motivo else "error")
    elif seg:
        seguranca.auditar("visita", "INFO", ip=ip, caminho=request.full_path.rstrip("?")[:255], agente=ua, limitar=1800)
    pow_ = seguranca.captcha_novo(session) if seg and seguranca.captcha_necessario(ip) else None
    return _render_login(passo="login", pow=pow_, abrir=tentou)


def _avisar_bloqueio(ip: str, nome: str) -> None:
    """Na falha que atinge o limite: auditoria + evento (regras de notificacao)."""
    from .. import eventos, seguranca
    c = seguranca.cfg()
    try:
        n, m = int(c["seg_bloqueio_falhas"]), int(c["seg_bloqueio_min"])
    except ValueError:
        return
    if n > 0 and seguranca.falhas_recentes(ip, m) == n:
        seguranca.auditar("tentativas", "BLOQUEADO", ip=ip, utilizador=nome, detalhe=f"{n} falhas em {m} min: bloqueado {m} min")
        eventos.emitir("seguranca.tentativas", "bloqueado", f"IP {ip} bloqueado por tentativas falhadas",
                       f"{n} logins falhados em {m} minutos (último utilizador: {nome or '-'}). Bloqueado durante {m} minutos.",
                       "/seguranca/auditoria")


@bp.route("/login/2fa", methods=["GET", "POST"])
def login_2fa():
    """Segundo fator: codigo da aplicacao (ou de recuperacao); sem 2FA e obrigatorio: configurar aqui (QR)."""
    import time
    from flask import session
    from .. import seguranca, totp
    p = session.get("seg_2fa")
    if g.user:
        return redirect(url_for("portal.home"))
    if not p or time.time() - p.get("ts", 0) > 300:
        session.pop("seg_2fa", None)
        flash("A verificação expirou: entre de novo.", "warn")
        return redirect(url_for("portal.login"))
    user = auth.user_by_id(p["uid"])
    if not user:
        session.pop("seg_2fa", None)
        return redirect(url_for("portal.login"))
    ip, ua = seguranca.ip_cliente(request), request.headers.get("User-Agent", "")
    tem = seguranca.tem_2fa(user.id)
    if not tem and "segredo" not in p:
        p["segredo"] = totp.novo_segredo()
        session["seg_2fa"] = p
    if request.method == "POST":
        if request.form.get("acao") == "cancelar":
            session.clear()
            return redirect(url_for("portal.login"))
        cod = request.form.get("codigo", "").strip()[:20]
        if seguranca.bloqueado_por_tentativas(ip):
            session.clear()
            flash("Demasiadas tentativas falhadas a partir deste endereço.", "error")
            return redirect(url_for("portal.login"))
        if not tem:
            rec = seguranca.ativar_2fa(user.id, p["segredo"], cod)
            if rec:
                auth.login_session(user)
                session["seg_rec"] = rec
                seguranca.auditar("2fa", "OK", ip=ip, utilizador=user.username, detalhe="dois fatores configurados no login",
                                  caminho="/login/2fa", agente=ua)
                seguranca.auditar("login", "OK", ip=ip, utilizador=user.username, detalhe="com dois fatores", caminho="/login", agente=ua)
                return redirect(url_for("portal.me", _anchor="dois-fatores"))
        else:
            tipo = seguranca.verificar_2fa(user.id, cod)
            if tipo:
                auth.login_session(user)
                seguranca.auditar("login", "OK", ip=ip, utilizador=user.username,
                                  detalhe="com dois fatores" + (" (código de recuperação)" if tipo == "recuperacao" else ""),
                                  caminho="/login", agente=ua)
                if tipo == "recuperacao":
                    flash("Entrou com um código de recuperação (já não serve outra vez). Restam "
                          f"{len(seguranca.dados_2fa(user.id)['rec'])}.", "warn")
                return redirect(_proximo(p.get("next", "")))
        p["falhas"] = p.get("falhas", 0) + 1
        seguranca.auditar("2fa", "FALHOU", ip=ip, utilizador=user.username, detalhe="código inválido", caminho="/login/2fa", agente=ua)
        _avisar_bloqueio(ip, user.username)
        if p["falhas"] >= 5:
            session.clear()
            flash("Demasiados códigos errados: entre de novo.", "error")
            return redirect(url_for("portal.login"))
        session["seg_2fa"] = p
        flash("Código inválido.", "error")
    qr = None if tem else seguranca.qr_2fa(p["segredo"], user.username)
    return _render_login(passo="2fa" if tem else "2fa_ativar", qr=qr, conta=user.username, abrir=True)


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
    from .. import seguranca
    if g.user and seguranca.ativo():
        seguranca.auditar("logout", "INFO", ip=seguranca.ip_cliente(request), utilizador=g.user.username, caminho="/logout")
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
                            "link": url_for("clientes.execucao", cmd_id=r["id"]) if r["tipo"] == "upgrade" else url_for("clientes.pedidos") + f"?cmd={r['id']}", "erro": None if a or r["estado"] == "OK" else "falhou"})
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
            elif action and action.startswith("2fa_"):
                _me_2fa(u, action)
                return redirect(url_for("portal.me", _anchor="dois-fatores"))
            return redirect(url_for("portal.me"))
        except ValueError as exc:
            flash(str(exc), "error")
            if action and action.startswith("2fa_"):
                return redirect(url_for("portal.me", _anchor="dois-fatores"))
    from flask import session
    from .. import seguranca
    f2 = None
    if seguranca.ativo() and (seguranca.cfg()["seg_2fa"] != "desligado" or seguranca.tem_2fa(u.id)):
        d = seguranca.dados_2fa(u.id)
        novo = session.get("seg_2fa_novo")
        f2 = {"ativo": bool(d["segredo"]), "rec": len(d["rec"]), "obrigatorio": seguranca.cfg()["seg_2fa"] == "obrigatorio" or
              (seguranca.cfg()["seg_2fa"] == "admins" and u.total),
              "qr": seguranca.qr_2fa(novo, u.username) if novo and not d["segredo"] else None,
              "codigos": session.pop("seg_rec", None)}
    dbs = None
    if u.can("modulo.oracle"):
        dbs = store.list_dbs() if u.is_admin or u.all_dbs else [d for d in store.list_dbs() if d["id"] in u.db_ids]
    return render_template("me.html", dbs=dbs, f2=f2)


def _me_2fa(u, action: str) -> None:
    """Minha area > Dois fatores: iniciar (QR), confirmar, novos codigos de recuperacao, desligar."""
    from flask import session
    from .. import seguranca, totp
    if not seguranca.ativo():
        raise ValueError("O módulo Segurança está desligado.")
    ip = seguranca.ip_cliente(request)
    cod = request.form.get("codigo", "").strip()
    if action == "2fa_iniciar":
        session["seg_2fa_novo"] = totp.novo_segredo()
    elif action == "2fa_cancelar":
        session.pop("seg_2fa_novo", None)
    elif action == "2fa_confirmar":
        seg = session.get("seg_2fa_novo")
        if not seg:
            raise ValueError("Comece de novo: Ativar.")
        rec = seguranca.ativar_2fa(u.id, seg, cod)
        if not rec:
            raise ValueError("Código inválido: confirme a hora do telemóvel e tente de novo.")
        session.pop("seg_2fa_novo", None)
        session["seg_rec"] = rec
        seguranca.auditar("2fa", "OK", ip=ip, utilizador=u.username, detalhe="dois fatores ativados (Minha área)")
        flash("Dois fatores ativados. Guarde os códigos de recuperação.", "ok")
    elif action in ("2fa_codigos", "2fa_desligar"):
        if not seguranca.verificar_2fa(u.id, cod):
            raise ValueError("Código inválido.")
        if action == "2fa_codigos":
            rec = totp.codigos_recuperacao()
            seguranca.guardar_2fa(u.id, rec=[totp.hash_recuperacao(x) for x in rec])
            session["seg_rec"] = rec
            flash("Novos códigos de recuperação (os anteriores deixaram de servir).", "ok")
        else:
            modo = seguranca.cfg()["seg_2fa"]
            if modo == "obrigatorio" or (modo == "admins" and u.total):
                raise ValueError("Os dois fatores são obrigatórios para a sua conta.")
            seguranca.guardar_2fa(u.id, remover=True)
            seguranca.auditar("2fa", "INFO", ip=ip, utilizador=u.username, detalhe="dois fatores desligados (Minha área)")
            flash("Dois fatores desligados.", "ok")
