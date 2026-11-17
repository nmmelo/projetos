"""Portal web do Watchtower: login com verificação humana, contentores, histórico e configuração."""
import functools
import os
import random
import re
import secrets
import time
from datetime import timedelta

from flask import (Flask, abort, flash, g, redirect, render_template, request, session, url_for)
from markupsafe import Markup
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import core

core.setup_logging()

MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5") or 5)
LOCK_MINUTES = int(os.environ.get("LOGIN_LOCK_MINUTES", "15") or 15)
SESSION_HOURS = int(os.environ.get("SESSION_HOURS", "8") or 8)
PROXY_HOPS = int(os.environ.get("PROXY_HOPS", "1") or 1)
ROLES = {"admin": "Administrador", "leitura": "Só leitura"}
USER_RE = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")
LOG_SOURCES = {
    "portal": "Portal",
    "watchtower": "Watchtower",
    "nginx-access": "nginx (acessos)",
    "nginx-error": "nginx (erros)",
}

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=PROXY_HOPS, x_proto=PROXY_HOPS)
core.init_db(generate_password_hash)
app.secret_key = core.secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_HOURS),
)
core.start_background()


# ---------------------------------------------------------------- filtros
@app.template_filter("dt")
def fmt_dt(value):
    if not value:
        return "—"
    v = str(value)
    return f"{v[8:10]}/{v[5:7]}/{v[0:4]} {v[11:16]}" if len(v) >= 16 else v


@app.template_filter("sid")
def fmt_sid(value):
    return core.short_id(value) if value else ""


# ---------------------------------------------------------------- sessão, CSRF, permissões
def client_ip():
    return request.remote_addr or "-"


@app.before_request
def load_user():
    g.user = None
    uid = session.get("uid")
    if uid:
        u = core.q("SELECT * FROM users WHERE id=?", (uid,), one=True)
        if u and u["active"]:
            g.user = u
        else:
            session.clear()
    if request.method == "POST" and not request.path.startswith("/api/webhook"):
        token = request.form.get("csrf", "")
        if not token or not secrets.compare_digest(token, session.get("csrf", "")):
            abort(400, "Pedido inválido (CSRF). Recarregue a página.")


@app.context_processor
def inject():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return {"csrf": session["csrf"], "user": g.get("user"), "roles": ROLES,
            "update_state": core.update_state}


def login_required(fn):
    @functools.wraps(fn)
    def wrap(*a, **kw):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        return fn(*a, **kw)
    return wrap


def admin_required(fn):
    @functools.wraps(fn)
    @login_required
    def wrap(*a, **kw):
        if g.user["role"] != "admin":
            abort(403)
        return fn(*a, **kw)
    return wrap


def is_admin():
    return bool(g.user) and g.user["role"] == "admin"


# ---------------------------------------------------------------- verificação humana
def new_challenge():
    op = random.choice(["+", "-", "×"])
    if op == "+":
        a, b = random.randint(2, 20), random.randint(2, 20)
        ans = a + b
    elif op == "-":
        a = random.randint(10, 30)
        b = random.randint(1, a - 1)
        ans = a - b
    else:
        a, b = random.randint(2, 9), random.randint(2, 9)
        ans = a * b
    session["captcha"] = str(ans)
    session["captcha_ts"] = time.time()
    text = f"{a} {op} {b} = ?"
    w, h = 24 + 15 * len(text), 44
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
             f'role="img" aria-label="Desafio de verificação">']
    for _ in range(5):
        parts.append(f'<line x1="{random.randint(0, w)}" y1="{random.randint(0, h)}" x2="{random.randint(0, w)}" '
                     f'y2="{random.randint(0, h)}" stroke="#4b5866" stroke-width="1"/>')
    x = 12
    for ch in text:
        rot = random.randint(-18, 18)
        y = random.randint(27, 33)
        parts.append(f'<text x="{x}" y="{y}" transform="rotate({rot} {x} {y})" font-family="monospace" '
                     f'font-size="{random.randint(20, 24)}" fill="#e6e9ec">{ch}</text>')
        x += 15
    parts.append("</svg>")
    return Markup("".join(parts))


def blocked(ip):
    since = time.time() - LOCK_MINUTES * 60
    r = core.q("SELECT COUNT(*) n FROM login_attempts WHERE ip=? AND ts>?", (ip, since), one=True)
    return r["n"] >= MAX_ATTEMPTS


# ---------------------------------------------------------------- login
@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("painel"))
    ip, erro = client_ip(), None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        answer = request.form.get("captcha", "").strip()
        expected, issued = session.pop("captcha", None), session.pop("captcha_ts", 0)
        if blocked(ip):
            erro = f"Demasiadas tentativas. Tente novamente daqui a {LOCK_MINUTES} minutos."
            core.audit(username or None, ip, "Login bloqueado", "IP bloqueado temporariamente")
        elif not expected or answer != expected or time.time() - issued > 600:
            erro = "A verificação humana não está correta."
            core.ex("INSERT INTO login_attempts(ip,ts) VALUES(?,?)", (ip, time.time()))
            core.audit(username or None, ip, "Login falhado", "verificação humana")
        else:
            u = core.q("SELECT * FROM users WHERE username=?", (username,), one=True)
            if u and u["active"] and check_password_hash(u["password_hash"], password):
                core.ex("DELETE FROM login_attempts WHERE ip=?", (ip,))
                session.clear()
                session.permanent = True
                session["uid"] = u["id"]
                session["csrf"] = secrets.token_hex(16)
                core.ex("UPDATE users SET last_login=? WHERE id=?", (core.now(), u["id"]))
                core.audit(u["username"], ip, "Login")
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("painel"))
            erro = "Utilizador ou password inválidos."
            core.ex("INSERT INTO login_attempts(ip,ts) VALUES(?,?)", (ip, time.time()))
            motivo = "utilizador desativado" if u and not u["active"] else "credenciais inválidas"
            core.audit(username or None, ip, "Login falhado", motivo)
    return render_template("login.html", challenge=new_challenge(), erro=erro,
                           max_attempts=MAX_ATTEMPTS, lock_minutes=LOCK_MINUTES)


@app.route("/logout", methods=["POST"])
def logout():
    if g.user:
        core.audit(g.user["username"], client_ip(), "Logout")
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- painel e contentores
@app.route("/")
@login_required
def painel():
    metrics, wt_error = core.wt_metrics()
    resumo = core.q("""SELECT COUNT(*) total,
                              SUM(state='running') ativos,
                              SUM(state NOT IN ('running','removido')) parados
                       FROM containers WHERE state != 'removido'""", one=True)
    recentes = core.q("""SELECT * FROM updates WHERE result != 'Registado'
                         ORDER BY ts DESC, id DESC LIMIT 10""")
    relatorio = core.q("SELECT * FROM reports ORDER BY id DESC LIMIT 1", one=True)
    return render_template("painel.html", metrics=metrics, wt_error=wt_error, resumo=resumo,
                           recentes=recentes, relatorio=relatorio, docker_ok=core.docker_ok(),
                           last_sync=core.meta_get("last_sync"))


@app.route("/contentores")
@login_required
def contentores():
    filtro = request.args.get("q", "").strip()
    sql = "SELECT * FROM containers"
    args = ()
    if filtro:
        sql += " WHERE name LIKE ? OR image LIKE ?"
        args = (f"%{filtro}%", f"%{filtro}%")
    sql += " ORDER BY state='removido', name"
    return render_template("contentores.html", lista=core.q(sql, args), filtro=filtro,
                           docker_ok=core.docker_ok(), last_sync=core.meta_get("last_sync"))


@app.route("/contentores/<nome>")
@login_required
def contentor(nome):
    c = core.q("SELECT * FROM containers WHERE name=?", (nome,), one=True)
    if not c:
        abort(404)
    hist = core.q("SELECT * FROM updates WHERE container=? ORDER BY ts DESC, id DESC", (nome,))
    return render_template("contentor.html", c=c, hist=hist)


def _image_base(ref):
    """nginx:alpine -> nginx ; registry:5000/a/b:1 -> registry:5000/a/b (filtro de imagem do Watchtower)."""
    last = ref.rsplit("/", 1)
    if ":" in last[-1]:
        last[-1] = last[-1].split(":", 1)[0]
    return "/".join(last).split("@", 1)[0]


@app.route("/acoes/atualizar", methods=["POST"])
@admin_required
def acao_atualizar():
    alvo = request.form.get("imagens", "").strip()
    imagens = ",".join(_image_base(s.strip()) for s in alvo.split(",") if s.strip())
    if core.wt_update_async(imagens, g.user["username"], client_ip()):
        core.audit(g.user["username"], client_ip(), "Update manual pedido", imagens or "todos")
        flash(f"Update pedido ({imagens or 'todos os contentores'}). O resultado aparece no histórico.", "ok")
    else:
        flash("Já está um update a decorrer.", "erro")
    return redirect(request.form.get("voltar") or url_for("painel"))


@app.route("/acoes/sincronizar", methods=["POST"])
@login_required
def acao_sincronizar():
    try:
        n = core.sync_containers()
        flash(f"Lista sincronizada: {n} contentor(es).", "ok")
    except Exception as e:  # noqa: BLE001
        flash(f"Não foi possível ler o Docker: {e}", "erro")
    return redirect(request.form.get("voltar") or url_for("contentores"))


# ---------------------------------------------------------------- configuração
@app.route("/config/utilizadores")
@admin_required
def utilizadores():
    return render_template("utilizadores.html", lista=core.q("SELECT * FROM users ORDER BY username"))


def _active_admins(exclude_id=None):
    r = core.q("SELECT COUNT(*) n FROM users WHERE role='admin' AND active=1 AND id != ?",
               (exclude_id or 0,), one=True)
    return r["n"]


@app.route("/config/utilizadores/novo", methods=["POST"])
@admin_required
def utilizador_novo():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "leitura")
    if not USER_RE.match(username):
        flash("Nome inválido: 3 a 32 caracteres (letras, números, . _ -).", "erro")
    elif len(password) < 8:
        flash("A password tem de ter pelo menos 8 caracteres.", "erro")
    elif role not in ROLES:
        flash("Perfil inválido.", "erro")
    elif core.q("SELECT 1 FROM users WHERE username=?", (username,), one=True):
        flash("Esse utilizador já existe.", "erro")
    else:
        core.ex("INSERT INTO users(username,password_hash,role,active,created_at) VALUES(?,?,?,?,?)",
                (username, generate_password_hash(password), role, 1, core.now()))
        core.audit(g.user["username"], client_ip(), "Utilizador criado", f"{username} ({ROLES[role]})")
        flash(f"Utilizador {username} criado.", "ok")
    return redirect(url_for("utilizadores"))


@app.route("/config/utilizadores/<int:uid>/editar", methods=["POST"])
@admin_required
def utilizador_editar(uid):
    u = core.q("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not u:
        abort(404)
    role = request.form.get("role", u["role"])
    active = 1 if request.form.get("active") == "1" else 0
    if role not in ROLES:
        flash("Perfil inválido.", "erro")
    elif (role != "admin" or not active) and u["role"] == "admin" and _active_admins(uid) == 0:
        flash("Tem de existir pelo menos um administrador ativo.", "erro")
    else:
        core.ex("UPDATE users SET role=?, active=? WHERE id=?", (role, active, uid))
        core.audit(g.user["username"], client_ip(), "Utilizador alterado",
                   f"{u['username']}: {ROLES[role]}, {'ativo' if active else 'desativado'}")
        flash(f"Utilizador {u['username']} alterado.", "ok")
    return redirect(url_for("utilizadores"))


@app.route("/config/utilizadores/<int:uid>/password", methods=["POST"])
@admin_required
def utilizador_password(uid):
    u = core.q("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not u:
        abort(404)
    password = request.form.get("password", "")
    if len(password) < 8:
        flash("A password tem de ter pelo menos 8 caracteres.", "erro")
    else:
        core.ex("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(password), uid))
        core.audit(g.user["username"], client_ip(), "Password reposta", u["username"])
        flash(f"Password de {u['username']} alterada.", "ok")
    return redirect(url_for("utilizadores"))


@app.route("/config/utilizadores/<int:uid>/apagar", methods=["POST"])
@admin_required
def utilizador_apagar(uid):
    u = core.q("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not u:
        abort(404)
    if u["id"] == g.user["id"]:
        flash("Não pode apagar o seu próprio utilizador.", "erro")
    elif u["role"] == "admin" and _active_admins(uid) == 0:
        flash("Tem de existir pelo menos um administrador ativo.", "erro")
    else:
        core.ex("DELETE FROM users WHERE id=?", (uid,))
        core.audit(g.user["username"], client_ip(), "Utilizador apagado", u["username"])
        flash(f"Utilizador {u['username']} apagado.", "ok")
    return redirect(url_for("utilizadores"))


@app.route("/config/auditoria")
@admin_required
def auditoria():
    filtro = request.args.get("q", "").strip()
    sql, args = "SELECT * FROM audit", ()
    if filtro:
        sql += " WHERE username LIKE ? OR action LIKE ? OR detail LIKE ? OR ip LIKE ?"
        args = tuple(f"%{filtro}%" for _ in range(4))
    sql += " ORDER BY id DESC LIMIT 500"
    return render_template("auditoria.html", lista=core.q(sql, args), filtro=filtro,
                           retention=core.RETENTION_DAYS)


def _tail(path, n):
    if not os.path.exists(path):
        return [f"(ficheiro {os.path.basename(path)} ainda não existe)"]
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 400 * n))
        lines = f.read().decode(errors="replace").splitlines()
    return lines[-n:]


@app.route("/config/logs")
@admin_required
def logs():
    fonte = request.args.get("fonte", "portal")
    if fonte not in LOG_SOURCES:
        fonte = "portal"
    try:
        linhas = max(50, min(2000, int(request.args.get("linhas", 300))))
    except ValueError:
        linhas = 300
    filtro = request.args.get("q", "").strip()
    try:
        if fonte == "watchtower":
            texto = core.docker_logs(core.WT_CONTAINER, linhas) if core.docker_ok() else ["(sem acesso ao socket do Docker)"]
        elif fonte == "portal":
            texto = _tail(os.path.join(core.LOG_DIR, "portal.log"), linhas)
        else:
            texto = _tail(os.path.join(core.LOG_DIR, f"{fonte}.log"), linhas)
    except Exception as e:  # noqa: BLE001
        texto = [f"Erro a ler os logs: {e}"]
    if filtro:
        texto = [ln for ln in texto if filtro.lower() in ln.lower()]
    return render_template("logs.html", fontes=LOG_SOURCES, fonte=fonte, linhas=linhas,
                           filtro=filtro, texto=list(reversed(texto)))


@app.route("/conta/password", methods=["GET", "POST"])
@login_required
def minha_password():
    if request.method == "POST":
        atual = request.form.get("atual", "")
        nova = request.form.get("nova", "")
        if not check_password_hash(g.user["password_hash"], atual):
            flash("A password atual não está correta.", "erro")
        elif len(nova) < 8:
            flash("A nova password tem de ter pelo menos 8 caracteres.", "erro")
        elif nova != request.form.get("confirmar", ""):
            flash("As passwords não coincidem.", "erro")
        else:
            core.ex("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(nova), g.user["id"]))
            core.audit(g.user["username"], client_ip(), "Password alterada", "pelo próprio")
            flash("Password alterada.", "ok")
            return redirect(url_for("painel"))
    return render_template("password.html")


# ---------------------------------------------------------------- webhook e saúde
@app.route("/api/webhook", methods=["POST"])
@app.route("/api/webhook/<token>", methods=["POST"])
def webhook(token=None):
    token = token or request.args.get("token") or request.headers.get("X-Token", "")
    if not core.WEBHOOK_TOKEN or not secrets.compare_digest(token, core.WEBHOOK_TOKEN):
        core.log.warning("webhook recusado (token inválido) de %s", client_ip())
        abort(401)
    core.handle_report(request.get_data(cache=False, as_text=False)[:200_000])
    return "ok"


@app.route("/saude")
def saude():
    return "ok"


@app.errorhandler(403)
def e403(_):
    return render_template("erro.html", codigo=403, msg="Sem permissão para esta página."), 403


@app.errorhandler(404)
def e404(_):
    return render_template("erro.html", codigo=404, msg="Página não encontrada."), 404
