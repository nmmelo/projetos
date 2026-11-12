"""
tesla-key — portal admin (porta 8001, só LAN).

Login definido com `tesla-setup admin`. Permite ver o estado, gerir clientes, ver logs
e configurar a Status API e as definições.
"""
import secrets
from datetime import timedelta
from functools import wraps

from flask import (Flask, abort, flash, redirect, render_template_string, request,
                   session, url_for)
from markupsafe import Markup

import common as c

c.init_db()
app = Flask(__name__)
app.secret_key = c.get_def("secret_key")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                  PERMANENT_SESSION_LIFETIME=timedelta(hours=8))

BASE = """<!doctype html>
<html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>tesla-key · {{ titulo }}</title>
<style>
:root{--bg:#f5f6f8;--card:#fff;--txt:#1d2433;--mut:#6b7385;--pri:#3157d5;--ok:#1f8a4c;--warn:#b7791f;--err:#c53030;--bd:#e3e6ec}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--txt)}
header{background:#1d2433;color:#fff;padding:0 24px;display:flex;align-items:center;gap:24px;height:52px}
header b{font-size:16px}header a{color:#c8cfe0;text-decoration:none}header a.on,header a:hover{color:#fff}
header .sp{flex:1}main{max-width:1100px;margin:24px auto;padding:0 16px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:16px 20px;margin-bottom:16px}
h2{font-size:15px;margin:0 0 12px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.kpi{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:14px}.kpi small{color:var(--mut)}.kpi div{font-size:22px;font-weight:600}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--bd);vertical-align:top}
th{color:var(--mut);font-weight:500;font-size:12px;text-transform:uppercase}
.b{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600}
.ok{background:#e3f4ea;color:var(--ok)}.warn{background:#fbf0dc;color:var(--warn)}.err{background:#fde8e8;color:var(--err)}.mut{background:#eceef2;color:var(--mut)}
input,select{padding:7px 9px;border:1px solid var(--bd);border-radius:6px;font:inherit}
button{padding:7px 12px;border:0;border-radius:6px;background:var(--pri);color:#fff;font:inherit;cursor:pointer}
button.sec{background:#eceef2;color:var(--txt)}button.del{background:var(--err)}
form.in{display:inline}.fl{padding:10px 14px;border-radius:6px;margin-bottom:12px}
.fl.ok{border:1px solid #b9e2c8}.fl.err{border:1px solid #f3b9b9}.fl.warn{border:1px solid #f0d49b}
code{background:#eceef2;padding:2px 6px;border-radius:4px;word-break:break-all}.mutt{color:var(--mut)}
.key{font-family:ui-monospace,monospace;background:#1d2433;color:#7ee2a8;padding:10px;border-radius:6px;word-break:break-all;margin:6px 0}
</style></head><body>
{% if session.get('user') %}
<header><b>tesla-key</b>
<a href="{{ url_for('painel') }}" class="{{ 'on' if pag=='painel' }}">Estado</a>
<a href="{{ url_for('clientes') }}" class="{{ 'on' if pag=='clientes' }}">Clientes</a>
<a href="{{ url_for('logs') }}" class="{{ 'on' if pag=='logs' }}">Logs</a>
<a href="{{ url_for('definicoes') }}" class="{{ 'on' if pag=='definicoes' }}">Definições</a>
<span class="sp"></span><span class="mutt">{{ session['user'] }}</span>
<form class="in" method="post" action="{{ url_for('logout') }}"><input type="hidden" name="csrf" value="{{ csrf() }}"><button class="sec">Sair</button></form>
</header>{% endif %}
<main>
{% for cat, msg in get_flashed_messages(with_categories=true) %}<div class="fl {{ cat }} card">{{ msg }}</div>{% endfor %}
{% if nova_chave %}<div class="card fl warn"><b>{{ nova_chave.titulo }}</b> — copie agora, não volta a ser mostrada:
<div class="key">{{ nova_chave.valor }}</div></div>{% endif %}
{{ corpo|safe }}
</main></body></html>"""


def csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def render(titulo, pag, corpo, **ctx):
    nova = session.pop("nova_chave", None)
    corpo_html = render_template_string(corpo, csrf=csrf, **ctx)
    return render_template_string(BASE, titulo=titulo, pag=pag, corpo=corpo_html, csrf=csrf, nova_chave=nova)


def login_obrigatorio(f):
    @wraps(f)
    def w(*a, **kw):
        if not session.get("user"):
            return redirect(url_for("login", proximo=request.path))
        if request.method == "POST" and request.form.get("csrf") != session.get("csrf"):
            abort(400, "CSRF inválido")
        return f(*a, **kw)
    return w


def mostrar_chave(titulo, valor):
    session["nova_chave"] = {"titulo": titulo, "valor": valor}


@app.errorhandler(c.ErroGateway)
def erro_gw(e):
    flash(e.mensagem, "err")
    return redirect(request.referrer or url_for("painel"))


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if not c.get_def("admin_pass_hash"):
        return render("Login", "", """<div class="card" style="max-width:420px;margin:60px auto">
<h2>Portal não configurado</h2><p>Defina o utilizador e a password dentro do contentor:</p>
<code>tesla-setup admin</code></div>""")
    if request.method == "POST":
        u, p = request.form.get("user", ""), request.form.get("password", "")
        if c.validar_admin(u, p):
            session.clear()
            session.permanent = True
            session["user"] = u
            c.log("INFO", "admin", f"login: {u}", cliente=request.remote_addr)
            return redirect(request.args.get("proximo") or url_for("painel"))
        c.log("WARNING", "admin", f"login falhado: {u}", cliente=request.remote_addr)
        flash("Utilizador ou password inválidos", "err")
    return render("Login", "", """<div class="card" style="max-width:360px;margin:60px auto">
<h2>tesla-key · login</h2><form method="post">
<p><input name="user" placeholder="utilizador" style="width:100%" autofocus required></p>
<p><input name="password" type="password" placeholder="password" style="width:100%" required></p>
<button style="width:100%">Entrar</button></form></div>""")


@app.post("/logout")
@login_obrigatorio
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Páginas
# ---------------------------------------------------------------------------

@app.get("/")
@login_obrigatorio
def painel():
    r = c.resumo()
    return render("Estado", "painel", """
<div class="grid" style="margin-bottom:16px">
 <div class="kpi"><small>Gateway</small><div><span class="b ok">online</span></div></div>
 <div class="kpi"><small>Token Tesla</small><div>
  {% if r.token.ok %}<span class="b {{ 'ok' if (r.token.segundos_restantes or 0) > 0 else 'warn' }}">
   {{ ((r.token.segundos_restantes or 0) // 60) }} min</span>{% else %}<span class="b err">sem token</span>{% endif %}</div></div>
 <div class="kpi"><small>Clientes ativos</small><div>{{ r.clientes.ativos }} / {{ r.clientes.total }}</div></div>
 <div class="kpi"><small>Pedidos API (24h)</small><div>{{ r.ultimas_24h.pedidos_api }}</div></div>
 <div class="kpi"><small>Warnings (24h)</small><div style="color:var(--warn)">{{ r.ultimas_24h.WARNING }}</div></div>
 <div class="kpi"><small>Erros (24h)</small><div style="color:var(--err)">{{ r.ultimas_24h.ERROR }}</div></div>
</div>
<div class="card"><h2>Configuração</h2><table>
<tr><th>Domínio</th><td>{{ r.configuracao.dominio or '—' }}
 {% if not r.configuracao.ok %}<span class="b err">falta tesla.env</span>{% endif %}</td></tr>
<tr><th>Região</th><td>{{ r.configuracao.regiao or '—' }}</td></tr>
<tr><th>Token</th><td>{% if r.token.ok %}obtido {{ r.token.obtido_em }} · expira {{ r.token.expira_em }}
 {% else %}<span class="b err">{{ r.token.erro or 'sem refresh token' }}</span>{% endif %}</td></tr>
<tr><th>Cache vehicle_data</th><td>{{ r.cache_segundos }} s</td></tr>
<tr><th>Status API</th><td>{% if r.status_api_ativa %}<span class="b ok">ativa</span>{% else %}<span class="b mut">desativada</span>{% endif %}</td></tr>
</table>
<p><form class="in" method="post" action="{{ url_for('testar') }}"><input type="hidden" name="csrf" value="{{ csrf() }}"><button>Testar ligação à Tesla</button></form>
<form class="in" method="post" action="{{ url_for('renovar') }}"><input type="hidden" name="csrf" value="{{ csrf() }}"><button class="sec">Renovar token</button></form></p>
</div>
<div class="card"><h2>Últimos erros e warnings</h2>{{ tabela_logs(erros) }}</div>
""", r=r, erros=[l for l in c.ler_logs(limite=200) if l["nivel"] != "INFO"][:10], tabela_logs=tabela_logs)


@app.post("/testar")
@login_obrigatorio
def testar():
    r = c.tesla("GET", "vehicles")
    if r.status_code == 200:
        v = r.json().get("response", [])
        flash(f"Ligação OK — {len(v)} veículo(s): " + ", ".join(f"{x.get('display_name')} ({x.get('state')})" for x in v), "ok")
        c.log("INFO", "admin", "teste de ligação OK")
    else:
        flash(f"Falhou: {r.status_code} {r.text[:200]}", "err")
    return redirect(url_for("painel"))


@app.post("/renovar")
@login_obrigatorio
def renovar():
    c.access_token(forcar=True)
    flash("Token renovado", "ok")
    return redirect(url_for("painel"))


@app.route("/clientes", methods=["GET", "POST"])
@login_obrigatorio
def clientes():
    if request.method == "POST":
        acao, nome = request.form.get("acao"), request.form.get("nome", "")
        if acao == "criar":
            mostrar_chave(f"Chave do cliente '{nome}'", c.criar_cliente(nome, request.form.get("descricao", "")))
        elif acao == "regenerar":
            mostrar_chave(f"Nova chave do cliente '{nome}'", c.regenerar_cliente(nome))
        elif acao in ("ativar", "desativar"):
            c.ativar_cliente(nome, acao == "ativar")
            flash(f"Cliente '{nome}' {acao}do", "ok")
        elif acao == "apagar":
            c.apagar_cliente(nome)
            flash(f"Cliente '{nome}' apagado", "ok")
        return redirect(url_for("clientes"))
    return render("Clientes", "clientes", """
<div class="card"><h2>Novo cliente</h2><form method="post">
<input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="criar">
<input name="nome" placeholder="nome (ex.: portal)" required pattern="[A-Za-z0-9._-]+">
<input name="descricao" placeholder="descrição" style="width:340px"> <button>Criar e gerar chave</button></form>
<p class="mutt">O cliente usa a chave no header <code>X-API-Key</code> em <code>http://tesla-key:8000/api/...</code></p></div>
<div class="card"><h2>Clientes</h2><table>
<tr><th>Nome</th><th>Descrição</th><th>Chave</th><th>Estado</th><th>Último uso</th><th>Pedidos</th><th></th></tr>
{% for x in lista %}<tr>
<td><b>{{ x.nome }}</b></td><td>{{ x.descricao }}</td><td><code>{{ x.prefixo }}…</code></td>
<td>{% if x.ativo %}<span class="b ok">ativo</span>{% else %}<span class="b mut">inativo</span>{% endif %}</td>
<td>{{ x.ultimo_uso or '—' }}</td><td>{{ x.pedidos }}</td>
<td style="white-space:nowrap">{% for a,l,cl in [('desativar' if x.ativo else 'ativar', 'Desativar' if x.ativo else 'Ativar','sec'),('regenerar','Nova chave','sec'),('apagar','Apagar','del')] %}
<form class="in" method="post" {% if a=='apagar' or a=='regenerar' %}onsubmit="return confirm('{{ l }} {{ x.nome }}?')"{% endif %}>
<input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="{{ a }}"><input type="hidden" name="nome" value="{{ x.nome }}">
<button class="{{ cl }}">{{ l }}</button></form>{% endfor %}</td></tr>
{% else %}<tr><td colspan="7" class="mutt">Sem clientes.</td></tr>{% endfor %}
</table></div>""", lista=c.listar_clientes())


def tabela_logs(linhas):
    return Markup(render_template_string("""<table>
<tr><th>Hora (UTC)</th><th>Nível</th><th>Origem</th><th>Cliente</th><th>Pedido</th><th>Status</th><th>ms</th><th>Mensagem</th></tr>
{% for l in linhas %}<tr><td style="white-space:nowrap">{{ l.ts[:19].replace('T',' ') }}</td>
<td><span class="b {{ {'INFO':'ok','WARNING':'warn','ERROR':'err'}[l.nivel] }}">{{ l.nivel }}</span></td>
<td>{{ l.origem }}</td><td>{{ l.cliente or '' }}</td><td>{{ (l.metodo or '') ~ ' ' ~ (l.caminho or '') }}</td>
<td>{{ l.status or '' }}</td><td>{{ l.ms if l.ms is not none else '' }}</td><td>{{ l.mensagem or '' }}</td></tr>
{% else %}<tr><td colspan="8" class="mutt">Sem registos.</td></tr>{% endfor %}</table>""", linhas=linhas))


@app.get("/logs")
@login_obrigatorio
def logs():
    nivel = request.args.get("nivel") or None
    cliente = request.args.get("cliente") or None
    limite = min(int(request.args.get("limite", 200)), 2000)
    return render("Logs", "logs", """
<div class="card"><form method="get">
<select name="nivel"><option value="">todos os níveis</option>
{% for n in ['INFO','WARNING','ERROR'] %}<option {{ 'selected' if n==nivel }}>{{ n }}</option>{% endfor %}</select>
<select name="cliente"><option value="">todos os clientes</option>
{% for x in clientes %}<option {{ 'selected' if x.nome==cliente }}>{{ x.nome }}</option>{% endfor %}</select>
<select name="limite">{% for n in [100,200,500,1000] %}<option {{ 'selected' if n==limite }}>{{ n }}</option>{% endfor %}</select>
<button>Filtrar</button></form></div>
<div class="card">{{ tabela(linhas) }}</div>""",
                  linhas=c.ler_logs(nivel, cliente, limite), nivel=nivel, cliente=cliente, limite=limite,
                  clientes=c.listar_clientes(), tabela=tabela_logs)


@app.route("/definicoes", methods=["GET", "POST"])
@login_obrigatorio
def definicoes():
    if request.method == "POST":
        acao = request.form.get("acao")
        if acao == "gerais":
            c.set_def("cache_segundos", max(0, int(request.form.get("cache_segundos", 60))))
            c.set_def("retencao_logs_dias", max(1, int(request.form.get("retencao_logs_dias", 30))))
            c.log("INFO", "admin", "definições gerais alteradas")
            flash("Definições guardadas", "ok")
        elif acao == "status_on":
            if not c.get_def("status_api_token_hash"):
                mostrar_chave("Token da Status API", c.gerar_token_status())
            c.set_def("status_api_ativa", "1")
            c.log("WARNING", "admin", "Status API ativada")
            flash("Status API ativada", "ok")
        elif acao == "status_off":
            c.set_def("status_api_ativa", "0")
            c.log("WARNING", "admin", "Status API desativada")
            flash("Status API desativada", "ok")
        elif acao == "status_token":
            mostrar_chave("Novo token da Status API", c.gerar_token_status())
        elif acao == "password":
            if not c.validar_admin(session["user"], request.form.get("atual", "")):
                flash("Password atual errada", "err")
            elif request.form.get("nova") != request.form.get("confirmar"):
                flash("As passwords não coincidem", "err")
            else:
                c.definir_admin(session["user"], request.form.get("nova", ""))
                flash("Password alterada", "ok")
        elif acao == "limpar_logs":
            flash(f"{c.limpar_logs()} registos antigos apagados", "ok")
        return redirect(url_for("definicoes"))
    return render("Definições", "definicoes", """
<div class="card"><h2>Gerais</h2><form method="post"><input type="hidden" name="csrf" value="{{ csrf() }}">
<input type="hidden" name="acao" value="gerais">
Cache do vehicle_data (s) <input name="cache_segundos" type="number" min="0" value="{{ cache }}" style="width:90px">
&nbsp; Retenção de logs (dias) <input name="retencao_logs_dias" type="number" min="1" value="{{ ret }}" style="width:90px">
<button>Guardar</button></form>
<form method="post" style="margin-top:10px"><input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="limpar_logs">
<button class="sec">Apagar logs com mais de {{ ret }} dias</button></form></div>

<div class="card"><h2>Status API (para outro portal)</h2>
<p>Estado: {% if ativa %}<span class="b ok">ativa</span>{% else %}<span class="b mut">desativada</span>{% endif %}
{% if pref %} · token <code>{{ pref }}…</code>{% endif %}</p>
<p class="mutt">Endpoints em <code>http://tesla-key:8000/status/v1/</code>: <code>health</code>, <code>resumo</code>,
<code>clientes</code>, <code>logs?nivel=ERROR&amp;limite=50</code> — header <code>Authorization: Bearer &lt;token&gt;</code>.
Só leitura: não dá acesso aos dados do carro.</p>
{% for a,l,cl in ([('status_off','Desativar','sec')] if ativa else [('status_on','Ativar','')]) + [('status_token','Gerar novo token','sec')] %}
<form class="in" method="post" {% if a=='status_token' %}onsubmit="return confirm('O token atual deixa de funcionar. Continuar?')"{% endif %}>
<input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="{{ a }}"><button class="{{ cl }}">{{ l }}</button></form>{% endfor %}
</div>

<div class="card"><h2>Password do portal</h2><form method="post"><input type="hidden" name="csrf" value="{{ csrf() }}">
<input type="hidden" name="acao" value="password">
<input name="atual" type="password" placeholder="atual" required>
<input name="nova" type="password" placeholder="nova (mín. 8)" minlength="8" required>
<input name="confirmar" type="password" placeholder="confirmar" minlength="8" required>
<button>Alterar</button></form></div>""",
                  cache=c.get_def("cache_segundos"), ret=c.get_def("retencao_logs_dias"),
                  ativa=c.get_def("status_api_ativa") == "1", pref=c.get_def("status_api_token_prefixo"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001)
