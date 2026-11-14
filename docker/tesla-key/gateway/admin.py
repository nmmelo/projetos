"""
tesla-key — portal admin (porta 8001, só LAN).

Login definido com `tesla-setup admin`. Permite ver o estado, gerir clientes, ver logs
e configurar a Status API e as definições.
"""
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, Response, abort, flash, jsonify, redirect, render_template_string, request,
                   send_file, session, url_for)
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
nav{display:flex;gap:18px;align-items:center}
.row{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:flex-end;margin-bottom:8px}.row label{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--mut)}
.chk{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:6px 16px;margin:6px 0 12px}.chk label{display:flex;gap:8px;align-items:flex-start}
.barra{height:10px;background:#eceef2;border-radius:6px;overflow:hidden}.barra div{height:100%;background:var(--pri)}
.barra div.warn{background:var(--warn)}.barra div.err{background:var(--err)}
.tw{overflow-x:auto;-webkit-overflow-scrolling:touch}
.mapa{height:480px;border-radius:8px;border:1px solid var(--bd)}
.m{font-family:ui-monospace,monospace;font-size:12px;font-weight:700;padding:2px 7px;border-radius:4px;color:#fff}
.m.GET{background:#1f8a4c}.m.POST{background:#b7791f}
pre{background:#1d2433;color:#e6e9f0;padding:10px;border-radius:6px;overflow-x:auto;font-size:12px;white-space:pre-wrap;word-break:break-all}
@media (max-width:760px){
 header{height:auto;flex-wrap:wrap;padding:10px 12px;gap:8px 14px}
 header nav{order:3;width:100%;overflow-x:auto;gap:16px;padding-bottom:4px;white-space:nowrap}
 main{margin:12px auto;padding:0 8px}.card{padding:12px}
 .grid{grid-template-columns:repeat(2,1fr);gap:8px}.kpi div{font-size:18px}
 table{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch}th,td{white-space:nowrap;padding:6px}
 input:not([type=checkbox]):not([type=radio]),select{width:100%}.row label{width:100%}
 button{padding:9px 12px}form.in{display:inline-block;margin:2px 0}
 table.ft,table.ft tbody,table.ft tr,table.ft td{display:block;width:100%;white-space:normal}
 table.ft td{border-bottom:0;padding:4px 0}table.ft tr{border-bottom:1px solid var(--bd);padding:8px 0}
 .mapa{height:360px}
}
</style></head><body>
{% if session.get('user') %}
<header><b>tesla-key</b>
<nav>
<a href="{{ url_for('painel') }}" class="{{ 'on' if pag=='painel' }}">Estado</a>
<a href="{{ url_for('consumo') }}" class="{{ 'on' if pag=='consumo' }}">Consumo</a>
<a href="{{ url_for('viagens') }}" class="{{ 'on' if pag=='viagens' }}">Viagens</a>
<a href="{{ url_for('mapa') }}" class="{{ 'on' if pag=='mapa' }}">Mapa</a>
<a href="{{ url_for('clientes') }}" class="{{ 'on' if pag=='clientes' }}">Clientes</a>
<a href="{{ url_for('tesla_conf') }}" class="{{ 'on' if pag=='tesla' }}">Tesla</a>
<a href="{{ url_for('dados') }}" class="{{ 'on' if pag=='dados' }}">Dados</a>
<a href="{{ url_for('api_ajuda') }}" class="{{ 'on' if pag=='api' }}">API</a>
<a href="{{ url_for('logs') }}" class="{{ 'on' if pag=='logs' }}">Logs</a>
<a href="{{ url_for('definicoes') }}" class="{{ 'on' if pag=='definicoes' }}">Definições</a>
</nav>
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
 <div class="kpi"><small>Consumo estimado ({{ r.consumo_mes.mes }})</small><div>
  <a href="{{ url_for('consumo') }}" style="color:inherit;text-decoration:none">{{ '%.2f'|format(r.consumo_mes.total_usd) }} $</a>
  <small>/ {{ '%.0f'|format(r.consumo_mes.credito_usd) }} $</small></div></div>
 <div class="kpi"><small>Pedidos API (24h)</small><div>{{ r.ultimas_24h.pedidos_api }}</div></div>
 <div class="kpi"><small>Warnings (24h)</small><div style="color:var(--warn)">{{ r.ultimas_24h.WARNING }}</div></div>
 <div class="kpi"><small>Erros (24h)</small><div style="color:var(--err)">{{ r.ultimas_24h.ERROR }}</div></div>
</div>
<div class="card"><h2>Configuração</h2><table>
<tr><th>Domínio</th><td>{{ r.configuracao.dominio or '—' }}
 {% if not r.configuracao.ok %}<span class="b err">falta tesla.env</span>{% endif %}</td></tr>
<tr><th>Região</th><td>{{ r.configuracao.regiao or '—' }}</td></tr>
<tr><th>Token</th><td>{% if r.token.ok %}obtido {{ r.token.obtido_em or '—' }} · expira {{ r.token.expira_em }}
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
            mostrar_chave(f"Chave do cliente '{nome}'",
                          c.criar_cliente(nome, request.form.get("descricao", ""), request.form.get("preset")))
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
<input name="descricao" placeholder="descrição" style="width:280px">
<select name="preset">{% for n, lst in presets.items() %}<option value="{{ n }}" {{ 'selected' if n==omissao }}>{{ n }} ({{ lst|length }})</option>{% endfor %}</select>
<button>Criar e gerar chave</button></form>
<p class="mutt">O cliente usa a chave no header <code>X-API-Key</code> em <code>http://tesla-key:8000/api/...</code></p></div>
<div class="card"><h2>Clientes</h2><table>
<tr><th>Nome</th><th>Descrição</th><th>Chave</th><th>Estado</th><th>Permissões</th><th>Último uso</th><th>Pedidos</th><th></th></tr>
{% for x in lista %}<tr>
<td><b>{{ x.nome }}</b></td><td>{{ x.descricao }}</td><td><code>{{ x.prefixo }}…</code></td>
<td>{% if x.ativo %}<span class="b ok">ativo</span>{% else %}<span class="b mut">inativo</span>{% endif %}</td>
<td style="max-width:260px">{% for p in (x.permissoes or '').split(',') if p %}<span class="b mut" style="margin:1px">{{ p }}</span>{% else %}<span class="mutt">nenhuma</span>{% endfor %}</td>
<td>{{ x.ultimo_uso or '—' }}</td><td>{{ x.pedidos }}</td>
<td style="white-space:nowrap"><a href="{{ url_for('cliente_perm', nome=x.nome) }}"><button type="button">Permissões</button></a>{% for a,l,cl in [('desativar' if x.ativo else 'ativar', 'Desativar' if x.ativo else 'Ativar','sec'),('regenerar','Nova chave','sec'),('apagar','Apagar','del')] %}
<form class="in" method="post" {% if a=='apagar' or a=='regenerar' %}onsubmit="return confirm('{{ l }} {{ x.nome }}?')"{% endif %}>
<input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="{{ a }}"><input type="hidden" name="nome" value="{{ x.nome }}">
<button class="{{ cl }}">{{ l }}</button></form>{% endfor %}</td></tr>
{% else %}<tr><td colspan="8" class="mutt">Sem clientes.</td></tr>{% endfor %}
</table></div>""", lista=c.listar_clientes(), presets=c.PRESETS, omissao=c.PERMISSAO_OMISSAO)


@app.route("/clientes/<nome>", methods=["GET", "POST"])
@login_obrigatorio
def cliente_perm(nome):
    if request.method == "POST":
        if request.form.get("preset"):
            c.definir_permissoes(nome, request.form["preset"])
        else:
            c.definir_permissoes(nome, request.form.getlist("perm"))
        flash(f"Permissões de '{nome}' guardadas", "ok")
        return redirect(url_for("cliente_perm", nome=nome))
    atuais = c.permissoes_cliente(nome)
    return render(f"Permissões · {nome}", "clientes", """
<div class="card"><h2>Permissões do cliente <code>{{ nome }}</code></h2>
<form method="post"><input type="hidden" name="csrf" value="{{ csrf() }}">
<table><tr><th></th><th>Permissão</th><th>O que dá acesso</th><th>Endpoints</th></tr>
{% for p, (desc, eps) in perms.items() %}<tr>
<td><input type="checkbox" name="perm" value="{{ p }}" id="p_{{ p }}" {{ 'checked' if p in atuais }}></td>
<td><label for="p_{{ p }}"><b>{{ p }}</b></label>{% if p in ('acordar','fleet') %} <span class="b warn">sensível</span>{% endif %}</td>
<td>{{ desc }}</td><td><code>{{ eps }}</code></td></tr>{% endfor %}
</table><p><button>Guardar</button> <a href="{{ url_for('clientes') }}"><button type="button" class="sec">Voltar</button></a></p></form>
<form method="post"><input type="hidden" name="csrf" value="{{ csrf() }}">
<span class="mutt">Aplicar preset:</span>
{% for n, lst in presets.items() %}<button class="sec" name="preset" value="{{ n }}" title="{{ lst|join(', ') }}">{{ n }}</button> {% endfor %}
</form>
<p class="mutt">A alteração aplica-se de imediato. O cliente pode consultar as suas permissões em <code>GET /api/me</code>.</p></div>""",
                  nome=nome, atuais=atuais, perms=c.PERMISSOES, presets=c.PRESETS)


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
            c.set_def("retencao_logs_dias", max(1, int(request.form.get("retencao_logs_dias", 30))))
            c.log("INFO", "admin", "definições gerais alteradas")
            flash("Definições guardadas", "ok")
        elif acao == "tempos":
            for k in ("cache_segundos", "cache_localizacao_segundos", "cache_veiculos_segundos"):
                c.set_def(k, max(0, int(request.form.get(k) or 0)))
            c.set_def("imagem_cache_dias", max(1, int(request.form.get("imagem_cache_dias") or 30)))
            c.log("INFO", "admin", "tempos de atualização alterados")
            flash("Tempos de atualização guardados", "ok")
        elif acao == "recolha":
            c.set_def("recolha_ativa", "1" if request.form.get("recolha_ativa") else "0")
            for k in ("recolha_intervalo_conduzir", "recolha_intervalo_parado", "recolha_minutos_parado",
                      "recolha_intervalo_dormir", "recolha_limite_diario"):
                c.set_def(k, max(1, int(request.form.get(k) or 1)))
            c.log("WARNING", "admin", f"recolha de viagens {'ativa' if c.get_def('recolha_ativa') == '1' else 'desativada'}")
            flash("Recolha de viagens guardada", "ok")
        elif acao == "seccoes":
            sel = [x for x in request.form.getlist("seccao") if x in c.SECCOES]
            if not sel:
                flash("Escolha pelo menos uma secção", "err")
            else:
                c.set_def("data_endpoints_omissao", ";".join(sel))
                c.log("INFO", "admin", "secções por omissão do /data: " + ", ".join(sel))
                flash("Secções por omissão guardadas", "ok")
        elif acao == "funcionalidades":
            c.definir_funcionalidades(request.form.getlist("func"))
            flash("Funcionalidades do gateway guardadas", "ok")
        elif acao == "precos":
            for k in ("preco_dados", "preco_wake", "preco_comando", "credito_mensal"):
                c.set_def(k, max(0.0, float((request.form.get(k) or "0").replace(",", "."))))
            c.log("INFO", "admin", "preços da estimativa alterados")
            flash("Preços guardados", "ok")
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
<div class="card"><h2>Recolha de viagens</h2>
<p class="mutt">O gateway lê o carro periodicamente e regista viagens e posições (Viagens, Mapa). Cada leitura é um pedido
de dados pago (cliente <code>recolha</code> em Consumo). Requer o scope <code>vehicle_location</code>.</p>
<form method="post"><input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="recolha">
<p><label><input type="checkbox" name="recolha_ativa" value="1" {{ 'checked' if rec.recolha_ativa == '1' }}> <b>Recolha ativa</b></label></p>
<div class="row">
<label>A conduzir (s)<input name="recolha_intervalo_conduzir" type="number" min="30" value="{{ rec.recolha_intervalo_conduzir }}"></label>
<label>Parado e acordado (s)<input name="recolha_intervalo_parado" type="number" min="60" value="{{ rec.recolha_intervalo_parado }}"></label>
<label>Minutos parado até "a dormir"<input name="recolha_minutos_parado" type="number" min="1" value="{{ rec.recolha_minutos_parado }}"></label>
<label>A dormir (s)<input name="recolha_intervalo_dormir" type="number" min="300" value="{{ rec.recolha_intervalo_dormir }}"></label>
<label>Máx. leituras/dia/veículo<input name="recolha_limite_diario" type="number" min="1" max="200" value="{{ rec.recolha_limite_diario }}"></label>
</div>
<p class="mutt">Com 2 min a conduzir, 10 min parado e 30 min a dormir: ~30 leituras por hora de condução + ~48/dia em repouso
(≈ 0,15–0,25 $/dia). A Tesla limita a 200 pedidos de dados por veículo e por dia.</p>
<button>Guardar</button></form></div>

<div class="card"><h2>Tempos de atualização</h2>
<p class="mutt">Durante este tempo o gateway responde com o último valor, sem pedir à Tesla (poupa créditos).
Um cliente pode forçar com <code>?fresh=1</code>.</p>
<form method="post"><input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="tempos">
<div class="row">
<label>Dados do carro, /data (s)<input name="cache_segundos" type="number" min="0" value="{{ d.cache_segundos }}"></label>
<label>Localização, /location (s)<input name="cache_localizacao_segundos" type="number" min="0" value="{{ d.cache_localizacao_segundos }}"></label>
<label>Lista e estado dos veículos (s)<input name="cache_veiculos_segundos" type="number" min="0" value="{{ d.cache_veiculos_segundos }}"></label>
<label>Imagem do carro (dias)<input name="imagem_cache_dias" type="number" min="1" value="{{ d.imagem_cache_dias }}"></label>
</div><button>Guardar</button></form></div>

<div class="card"><h2>Dados a ir buscar (/data por omissão)</h2>
<p class="mutt">Secções pedidas à Tesla quando o cliente não indica <code>?endpoints=</code>. Menos secções = respostas mais pequenas
(o custo por pedido é o mesmo).</p>
<form method="post"><input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="seccoes">
<div class="chk">{% for sec, perm in seccoes.items() %}<label><input type="checkbox" name="seccao" value="{{ sec }}" {{ 'checked' if sec in omissao }}>
<span><code>{{ sec }}</code> <span class="mutt">({{ perm }})</span></span></label>{% endfor %}</div>
<button>Guardar</button></form></div>

<div class="card"><h2>Funcionalidades ativas no gateway</h2>
<p class="mutt">O que o gateway disponibiliza a qualquer cliente. Uma funcionalidade desligada aqui fica bloqueada (403)
para todos, mesmo que o cliente tenha a permissão.</p>
<form method="post"><input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="funcionalidades">
<div class="chk">{% for p, (desc, eps) in perms.items() %}<label><input type="checkbox" name="func" value="{{ p }}" {{ 'checked' if p in func }}>
<span><b>{{ p }}</b> <span class="mutt">{{ desc }}</span></span></label>{% endfor %}</div>
<button>Guardar</button></form></div>

<div class="card"><h2>Preços e crédito (estimativa de consumo)</h2>
<p class="mutt">Valores em USD por pedido. Confirme os preços atuais em developer.tesla.com.</p>
<form method="post"><input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="precos">
<div class="row">
<label>Pedido de dados<input name="preco_dados" value="{{ d.preco_dados }}" inputmode="decimal"></label>
<label>Wake<input name="preco_wake" value="{{ d.preco_wake }}" inputmode="decimal"></label>
<label>Comando<input name="preco_comando" value="{{ d.preco_comando }}" inputmode="decimal"></label>
<label>Crédito mensal<input name="credito_mensal" value="{{ d.credito_mensal }}" inputmode="decimal"></label>
</div><button>Guardar</button></form></div>

<div class="card"><h2>Logs</h2><form method="post"><input type="hidden" name="csrf" value="{{ csrf() }}">
<input type="hidden" name="acao" value="gerais">
<div class="row"><label>Retenção de logs (dias)<input name="retencao_logs_dias" type="number" min="1" value="{{ ret }}"></label>
<button>Guardar</button></div></form>
<form method="post"><input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="limpar_logs">
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
                  d={k: c.get_def(k) for k in ("cache_segundos", "cache_localizacao_segundos",
                                               "cache_veiculos_segundos", "preco_dados", "preco_wake",
                                               "preco_comando", "credito_mensal")}
                    | {"imagem_cache_dias": c.get_def("imagem_cache_dias")
                       or c.ler_conf_seguro().get("TESLA_IMAGE_CACHE_DIAS") or 30},
                  seccoes={k: v for k, v in c.SECCOES.items()},
                  omissao=(c.get_def("data_endpoints_omissao") or "").split(";"),
                  perms=c.PERMISSOES, func=c.funcionalidades_ativas(),
                  rec={k: c.get_def(k) for k in ("recolha_ativa", "recolha_intervalo_conduzir", "recolha_intervalo_parado",
                                                  "recolha_minutos_parado", "recolha_intervalo_dormir",
                                                  "recolha_limite_diario")},
                  ativa=c.get_def("status_api_ativa") == "1", pref=c.get_def("status_api_token_prefixo"))


@app.get("/consumo")
@login_obrigatorio
def consumo():
    m = c.consumo_mes(request.args.get("mes"))
    nomes = {"dados": "Pedidos de dados", "wake": "Wakes", "comando": "Comandos"}
    return render("Consumo", "consumo", """
<div class="card"><h2>Consumo estimado — {{ m.mes }}</h2>
{% set pct = m.credito_usado_pct or 0 %}
<div class="grid" style="margin-bottom:12px">
 <div class="kpi"><small>Total estimado</small><div>{{ '%.2f'|format(m.total_usd) }} $</div></div>
 <div class="kpi"><small>Crédito mensal</small><div>{{ '%.2f'|format(m.credito_usd) }} $</div></div>
 <div class="kpi"><small>Crédito usado</small><div>{{ pct }} %</div></div>
 <div class="kpi"><small>Restante</small><div>{{ '%.2f'|format([m.credito_usd - m.total_usd, 0]|max) }} $</div></div>
</div>
<div class="barra"><div class="{{ 'err' if pct >= 90 else 'warn' if pct >= 70 else '' }}" style="width:{{ [pct,100]|min }}%"></div></div>
<p class="mutt">{{ m.nota }}. Só conta os pedidos feitos através deste gateway.</p></div>

<div class="card"><h2>Por tipo</h2><table>
<tr><th>Tipo</th><th>Pedidos (mês)</th><th>Preço</th><th>Custo</th><th>Hoje</th><th>Limite/dia/veículo</th></tr>
{% for k, v in m.categorias.items() %}<tr><td>{{ nomes[k] }}</td><td>{{ v.pedidos }}</td><td>{{ v.preco }} $</td>
<td>{{ '%.3f'|format(v.custo) }} $</td>
<td>{% if v.hoje >= v.limite_dia * 0.8 %}<span class="b warn">{{ v.hoje }}</span>{% else %}{{ v.hoje }}{% endif %}</td>
<td>{{ v.limite_dia }}</td></tr>{% endfor %}</table></div>

<div class="card"><h2>Por cliente</h2><table><tr><th>Cliente</th><th>Tipo</th><th>Pedidos</th><th>Custo</th></tr>
{% for x in m.por_cliente %}<tr><td>{{ x.cliente }}</td><td>{{ nomes[x.categoria] }}</td><td>{{ x.n }}</td><td>{{ '%.3f'|format(x.custo) }} $</td></tr>
{% else %}<tr><td colspan="4" class="mutt">Sem pedidos este mês.</td></tr>{% endfor %}</table></div>

<div class="card"><h2>O que está a ser pedido à Tesla</h2><table><tr><th>Tipo</th><th>Endpoint</th><th>Pedidos</th></tr>
{% for x in m.por_caminho %}<tr><td>{{ nomes[x.categoria] }}</td><td><code>{{ x.caminho }}</code></td><td>{{ x.n }}</td></tr>
{% else %}<tr><td colspan="3" class="mutt">Sem pedidos este mês.</td></tr>{% endfor %}</table></div>

<div class="card"><h2>Por dia</h2>
{% set maxd = (m.por_dia|map(attribute='n')|max) if m.por_dia else 1 %}
{% for x in m.por_dia %}<div style="display:flex;gap:10px;align-items:center;margin:3px 0">
<span style="width:90px" class="mutt">{{ x.dia[5:] }}</span><div class="barra" style="flex:1"><div style="width:{{ 100 * x.n // maxd }}%"></div></div>
<span style="width:40px;text-align:right">{{ x.n }}</span></div>{% else %}<p class="mutt">Sem pedidos este mês.</p>{% endfor %}
</div>""", m=m, nomes=nomes)


@app.route("/tesla", methods=["GET", "POST"])
@login_obrigatorio
def tesla_conf():
    if request.method == "POST":
        alt = {}
        for k, _, tipo in c.CAMPOS_ENV:
            if tipo == "scopes":
                sel = request.form.getlist("scope")
                if sel:
                    alt[k] = " ".join(sel)
            elif tipo == "segredo":
                if request.form.get(k):
                    alt[k] = request.form[k].strip()
            elif k in request.form:
                v = request.form[k].strip().replace("$", "")
                if v != dict(c.ler_env_bruto()).get(k, ""):
                    alt[k] = v
        if alt:
            c.escrever_env(alt)
            msg = "tesla.env guardado (" + ", ".join(sorted(alt)) + ")."
            if "TESLA_SCOPES" in alt:
                msg += " Scopes alterados: refazer o login com tesla-setup url / token."
            flash(msg, "ok")
        else:
            flash("Sem alterações", "warn")
        return redirect(url_for("tesla_conf"))
    env = dict(c.ler_env_bruto())
    conhecidos = {k for k, _, _ in c.CAMPOS_ENV}
    pedidos = (env.get("TESLA_SCOPES") or "").split()
    no_token = c.scopes_token()
    return render("Tesla", "tesla", """
<div class="card"><h2>Scopes</h2><table>
<tr><th>Scope</th><th>Descrição</th><th>Pedido (tesla.env)</th><th>No token atual</th></tr>
{% for sc, desc in scopes %}<tr><td><code>{{ sc }}</code></td><td>{{ desc }}</td>
<td>{% if sc in pedidos %}<span class="b ok">sim</span>{% else %}<span class="b mut">não</span>{% endif %}</td>
<td>{% if sc in no_token %}<span class="b ok">sim</span>{% elif sc in pedidos %}<span class="b warn">falta</span>{% else %}<span class="b mut">não</span>{% endif %}</td></tr>{% endfor %}
</table>
<p class="mutt">"falta" = pedido mas não concedido: ativar o scope na aplicação em developer.tesla.com e refazer o login
(<code>tesla-setup url</code> + <code>tesla-setup token</code>).</p></div>

<div class="card"><h2>Configuração (tesla.env)</h2>
<form method="post"><input type="hidden" name="csrf" value="{{ csrf() }}">
<table class="ft">
{% for k, desc, tipo in campos %}<tr><td><code>{{ k }}</code><br><span class="mutt">{{ desc }}</span></td><td style="min-width:260px">
{% if tipo == 'segredo' %}<input name="{{ k }}" type="password" placeholder="{{ mascarar(k, env.get(k,'')) or '(vazio)' }} — deixar vazio para manter" autocomplete="new-password" style="width:100%">
{% elif tipo == 'regiao' %}<select name="{{ k }}">{% for r in ['eu','na'] %}<option {{ 'selected' if env.get(k,'eu')==r }}>{{ r }}</option>{% endfor %}</select>
{% elif tipo == 'scopes' %}<div class="chk" style="margin:0">{% for sc, d in scopes %}<label>{% if sc in ['openid','offline_access'] %}<input type="hidden" name="scope" value="{{ sc }}"><input type="checkbox" checked disabled>{% else %}<input type="checkbox" name="scope" value="{{ sc }}" {{ 'checked' if sc in pedidos }}>{% endif %}<code>{{ sc }}</code></label>{% endfor %}</div>
{% elif tipo == 'numero' %}<input name="{{ k }}" type="number" min="1" value="{{ env.get(k,'') }}">
{% else %}<input name="{{ k }}" value="{{ env.get(k,'') }}" style="width:100%">{% endif %}
</td></tr>{% endfor %}
</table>
<p><button>Guardar tesla.env</button></p></form>
{% if outros %}<p class="mutt">Outras variáveis no ficheiro: {% for k in outros %}<code>{{ k }}</code> {% endfor %}</p>{% endif %}
<p class="mutt">Alterações aplicam-se no pedido seguinte, sem restart. Os <code>$</code> nos códigos da imagem são retirados
automaticamente. Mudar o Client ID/Secret ou a região exige refazer o login.</p></div>""",
                  campos=c.CAMPOS_ENV, env=env, scopes=c.SCOPES_TESLA, pedidos=pedidos, no_token=no_token,
                  mascarar=c.mascarar, outros=[k for k in env if k not in conhecidos])

# ---------------------------------------------------------------------------
# Viagens, mapa, dados (backups/exportação) e ajuda da API
# ---------------------------------------------------------------------------

LEAFLET = """<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>"""

FILTRO = """<form method="get" class="row">
<label>Período<select name="periodo" onchange="this.form.submit()">
{% for v, n in [('semana','Semana'),('mes','Mês'),('ano','Ano'),('dias','Últimos 30 dias'),('tudo','Tudo')] %}
<option value="{{ v }}" {{ 'selected' if f.periodo==v }}>{{ n }}</option>{% endfor %}</select></label>
{% if f.periodo=='semana' %}<label>Semana<input type="week" name="valor" value="{{ f.valor }}"></label>
{% elif f.periodo=='mes' %}<label>Mês<input type="month" name="valor" value="{{ f.valor }}"></label>
{% elif f.periodo=='ano' %}<label>Ano<select name="valor">{% for a in f.anos %}<option {{ 'selected' if a==f.valor }}>{{ a }}</option>{% endfor %}</select></label>
{% elif f.periodo=='dias' %}<input type="hidden" name="valor" value="30">{% endif %}
{% if f.vins|length > 1 %}<label>Veículo<select name="vin"><option value="">todos</option>
{% for v in f.vins %}<option value="{{ v }}" {{ 'selected' if v==f.vin }}>…{{ v[-6:] }}</option>{% endfor %}</select></label>{% endif %}
<button>Filtrar</button></form>"""


def ler_filtro():
    agora = datetime.now(timezone.utc)
    periodo = request.args.get("periodo", "mes")
    omissao = {"semana": agora.strftime("%G-W%V"), "mes": agora.strftime("%Y-%m"), "ano": str(agora.year),
               "dias": "30", "tudo": ""}
    valor = request.args.get("valor") or omissao.get(periodo, "")
    de, ate = c.intervalo_periodo(periodo, valor)
    with c.db() as con:
        vins = [r[0] for r in con.execute("SELECT DISTINCT vin FROM viagens UNION SELECT vin FROM estado_veiculo")]
    anos = c.anos_com_dados() or [str(agora.year)]
    return {"periodo": periodo, "valor": valor, "de": de, "ate": ate, "vin": request.args.get("vin") or None,
            "vins": vins, "anos": anos}


@app.get("/viagens")
@login_obrigatorio
def viagens():
    f = ler_filtro()
    lista = c.listar_viagens(f["vin"], f["de"], f["ate"], limite=1000)
    res = c.resumo_viagens(f["vin"], f["de"], f["ate"])
    hoje = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with c.db() as con:
        estados = [dict(r) for r in con.execute("SELECT * FROM estado_veiculo")]
        for e in estados:
            e["hoje"] = con.execute("SELECT COUNT(*) FROM consumo WHERE dia=? AND cliente='recolha' AND caminho LIKE ?",
                                    (hoje, f"vehicles/{e['vin']}/%")).fetchone()[0]
            e["proxima"] = (datetime.fromtimestamp(e["proxima_leitura"], timezone.utc).isoformat(timespec="seconds")
                            if e.get("proxima_leitura") else None)
    for v in lista:
        try:
            v["duracao_min"] = round((datetime.fromisoformat(v["fim"]) - datetime.fromisoformat(v["inicio"])).total_seconds() / 60) if v["fim"] else None
        except (TypeError, ValueError):
            v["duracao_min"] = None
    return render("Viagens", "viagens", """
<div class="card"><h2>Recolha</h2>
<p>{% if ativa %}<span class="b ok">ativa</span>{% else %}<span class="b mut">desativada</span>
— ative em <a href="{{ url_for('definicoes') }}">Definições</a>{% endif %}</p>
{% if estados %}<table><tr><th>Veículo</th><th>Estado</th><th>Última leitura</th><th>Próxima</th><th>Leituras hoje</th><th>Bateria</th></tr>
{% for e in estados %}<tr><td>…{{ e.vin[-6:] }}</td><td>{{ e.estado or '—' }}</td><td>{{ (e.ts or '—')[:19].replace('T',' ') }}</td>
<td>{{ (e.proxima or '—')[:19].replace('T',' ') }}</td><td>{{ e.hoje }} / {{ limite }}</td><td>{{ e.bateria if e.bateria is not none else '—' }}{{ ' %' if e.bateria is not none }}</td></tr>{% endfor %}
</table>{% endif %}</div>
<div class="card">""" + FILTRO + """
<div class="grid" style="margin:12px 0">
 <div class="kpi"><small>Viagens</small><div>{{ res.n }}</div></div>
 <div class="kpi"><small>Distância</small><div>{{ res.km }} km</div></div>
 <div class="kpi"><small>Bateria usada</small><div>{{ res.bateria }} %</div></div>
 <div class="kpi"><small>Velocidade máx.</small><div>{{ res.vmax or '—' }}{{ ' km/h' if res.vmax }}</div></div>
</div>
<table><tr><th>Início</th><th>Duração</th><th>Distância</th><th>Bateria</th><th>V. máx.</th><th>Pontos</th><th></th></tr>
{% for v in lista %}<tr><td>{{ v.inicio[:16].replace('T',' ') }}</td>
<td>{{ v.duracao_min ~ ' min' if v.duracao_min is not none else ('a decorrer' if v.estado=='aberta' else '—') }}</td>
<td>{{ v.km ~ ' km' if v.km is not none else '—' }}</td>
<td>{{ v.bateria_inicio if v.bateria_inicio is not none else '?' }} → {{ v.bateria_fim if v.bateria_fim is not none else '?' }} %</td>
<td>{{ v.velocidade_max_kmh or '—' }}</td><td>{{ v.pontos }}</td>
<td><a href="{{ url_for('viagem', viagem_id=v.id) }}"><button type="button" class="sec">Ver</button></a></td></tr>
{% else %}<tr><td colspan="7" class="mutt">Sem viagens neste período.</td></tr>{% endfor %}</table></div>""",
                  f=f, lista=lista, res=res, estados=estados, ativa=c.get_def("recolha_ativa") == "1",
                  limite=c.get_def("recolha_limite_diario"))


@app.get("/viagens/<int:viagem_id>")
@login_obrigatorio
def viagem(viagem_id):
    v = c.obter_viagem(viagem_id)
    return render(f"Viagem {viagem_id}", "viagens", LEAFLET + """
<div class="card"><h2>Viagem {{ v.id }} — {{ v.inicio[:16].replace('T',' ') }}</h2>
<div class="grid" style="margin-bottom:12px">
 <div class="kpi"><small>Distância</small><div>{{ v.km ~ ' km' if v.km is not none else '—' }}</div></div>
 <div class="kpi"><small>Fim</small><div style="font-size:15px">{{ (v.fim or 'a decorrer')[:16].replace('T',' ') }}</div></div>
 <div class="kpi"><small>Bateria</small><div>{{ v.bateria_inicio }} → {{ v.bateria_fim if v.bateria_fim is not none else '?' }} %</div></div>
 <div class="kpi"><small>V. máx.</small><div>{{ v.velocidade_max_kmh or '—' }}</div></div>
</div>
<div id="mapa" class="mapa"></div>
<p><a href="{{ url_for('exportar_viagem', viagem_id=v.id, formato='gpx') }}"><button type="button" class="sec">GPX</button></a>
<a href="{{ url_for('exportar_viagem', viagem_id=v.id, formato='json') }}"><button type="button" class="sec">JSON</button></a>
<a href="{{ url_for('viagens') }}"><button type="button" class="sec">Voltar</button></a></p>
<p class="mutt">Percurso aproximado: um ponto por leitura (intervalo "a conduzir" em Definições).</p></div>
<script>
const pts = {{ v.percurso|map(attribute='lat')|list|tojson }}.map((la, i) => [la, {{ v.percurso|map(attribute='lon')|list|tojson }}[i]]);
const m = L.map('mapa'); L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
 {maxZoom: 19, attribution: '&copy; OpenStreetMap'}).addTo(m);
if (pts.length) { const l = L.polyline(pts, {color: '#3157d5', weight: 4}).addTo(m); m.fitBounds(l.getBounds(), {padding: [20, 20]});
 L.circleMarker(pts[0], {radius: 7, color: '#1f8a4c'}).addTo(m).bindPopup('Início');
 L.circleMarker(pts[pts.length-1], {radius: 7, color: '#c53030'}).addTo(m).bindPopup('Fim'); }
else { m.setView([39.5, -8], 6); }
</script>""", v=v)


@app.get("/viagens/<int:viagem_id>.<formato>")
@login_obrigatorio
def exportar_viagem(viagem_id, formato):
    v = c.obter_viagem(viagem_id)
    if formato == "json":
        return Response(json.dumps(v, ensure_ascii=False, indent=1), mimetype="application/json",
                        headers={"Content-Disposition": f"attachment; filename=viagem-{viagem_id}.json"})
    if formato == "gpx":
        corpo = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<gpx version="1.1" creator="tesla-key" xmlns="http://www.topografix.com/GPX/1/1">',
                 f'<trk><name>Viagem {viagem_id}</name><trkseg>']
        corpo += [f'<trkpt lat="{p["lat"]}" lon="{p["lon"]}"><time>{p["ts"]}</time></trkpt>' for p in v["percurso"]]
        corpo += ["</trkseg></trk>", "</gpx>"]
        return Response("\n".join(corpo), mimetype="application/gpx+xml",
                        headers={"Content-Disposition": f"attachment; filename=viagem-{viagem_id}.gpx"})
    abort(404)


@app.get("/mapa")
@login_obrigatorio
def mapa():
    f = ler_filtro()
    return render("Mapa", "mapa", LEAFLET + """
<script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>
<div class="card"><h2>Por onde andou o carro</h2>""" + FILTRO + """
<p><label><input type="checkbox" id="so_viagens"> só pontos de viagens (sem estacionamentos)</label>
<span id="info" class="mutt" style="margin-left:10px"></span></p>
<div id="mapa" class="mapa"></div>
<p class="mutt">Mapa de calor das posições registadas (grelha de ~100 m): quanto mais quente, mais vezes o carro passou ou esteve lá.</p></div>
<script>
const m = L.map('mapa').setView([39.5, -8], 6);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '&copy; OpenStreetMap'}).addTo(m);
let camada = null;
function carregar() {
  const q = new URLSearchParams({periodo: {{ f.periodo|tojson }}, valor: {{ f.valor|tojson }}, vin: {{ (f.vin or '')|tojson }},
    so_viagens: document.getElementById('so_viagens').checked ? '1' : '0'});
  fetch('{{ url_for("mapa_dados") }}?' + q).then(r => r.json()).then(d => {
    if (camada) m.removeLayer(camada);
    const max = Math.max(1, ...d.pontos.map(p => p.n));
    camada = L.heatLayer(d.pontos.map(p => [p.lat, p.lon, Math.max(0.15, Math.log(1 + p.n) / Math.log(1 + max))]),
      {radius: 18, blur: 15, maxZoom: 15, minOpacity: 0.35}).addTo(m);
    document.getElementById('info').textContent = d.pontos.length + ' zonas · ' + d.total + ' pontos';
    if (d.pontos.length) m.fitBounds(d.pontos.map(p => [p.lat, p.lon]), {padding: [20, 20], maxZoom: 14});
  });
}
document.getElementById('so_viagens').addEventListener('change', carregar);
carregar();
</script>""", f=f)


@app.get("/mapa/dados.json")
@login_obrigatorio
def mapa_dados():
    f = ler_filtro()
    pts = c.posicoes(f["vin"], f["de"], f["ate"], agregado=True, so_viagens=request.args.get("so_viagens") == "1")
    return jsonify({"pontos": pts, "total": sum(p["n"] for p in pts)})


@app.route("/dados", methods=["GET", "POST"])
@login_obrigatorio
def dados():
    if request.method == "POST":
        acao = request.form.get("acao")
        if acao == "backup":
            flash(f"Backup criado: {c.criar_backup('manual')}", "ok")
        elif acao == "apagar":
            c.apagar_backup(request.form.get("nome"))
            flash("Backup apagado", "ok")
        elif acao == "restaurar":
            c.restaurar_backup(request.form.get("nome"))
            flash("Base de dados restaurada (foi feito antes um backup 'antes-restauro')", "ok")
        elif acao == "config":
            c.set_def("backup_automatico", "1" if request.form.get("backup_automatico") else "0")
            c.set_def("backup_retencao", max(1, int(request.form.get("backup_retencao") or 14)))
            flash("Definições de backup guardadas", "ok")
        return redirect(url_for("dados"))
    with c.db() as con:
        cont = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("viagens", "pontos", "consumo", "logs", "clientes")}
    tam = round(os.path.getsize(c.DB_FILE) / 1024, 1) if os.path.exists(c.DB_FILE) else 0
    return render("Dados", "dados", """
<div class="card"><h2>Base de dados</h2>
<div class="grid">{% for k, n in cont.items() %}<div class="kpi"><small>{{ k }}</small><div>{{ n }}</div></div>{% endfor %}
<div class="kpi"><small>Tamanho</small><div>{{ tam }} KB</div></div></div></div>

<div class="card"><h2>Exportar</h2>
<form method="get" action="{{ url_for('exportar') }}" class="row">
<label>Dados<select name="tipo"><option value="viagens">Viagens</option><option value="pontos">Posições</option>
<option value="consumo">Consumo</option><option value="logs">Logs</option></select></label>
<label>Formato<select name="formato"><option value="csv">CSV (Excel)</option><option value="json">JSON</option>
<option value="gpx">GPX (viagens)</option><option value="geojson">GeoJSON (posições)</option></select></label>
<label>Período<select name="periodo"><option value="tudo">Tudo</option><option value="ano">Este ano</option>
<option value="mes">Este mês</option><option value="semana">Esta semana</option></select></label>
<button>Exportar</button></form>
<p class="mutt">CSV com separador <code>;</code>. GPX abre no Google Earth, Strava, etc.; GeoJSON em QGIS/geojson.io.</p></div>

<div class="card"><h2>Backups</h2>
<form method="post" class="row"><input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="config">
<label style="flex-direction:row;align-items:center"><input type="checkbox" name="backup_automatico" value="1" {{ 'checked' if auto == '1' }}> Backup automático diário</label>
<label>Guardar (nº de backups automáticos)<input name="backup_retencao" type="number" min="1" value="{{ ret }}"></label>
<button>Guardar</button></form>
<form method="post"><input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="backup"><button>Criar backup agora</button></form>
<table style="margin-top:10px"><tr><th>Ficheiro</th><th>Data (UTC)</th><th>Tamanho</th><th></th></tr>
{% for b in backups %}<tr><td><code>{{ b.nome }}</code></td><td>{{ b.data[:19].replace('T',' ') }}</td><td>{{ b.tamanho_kb }} KB</td>
<td style="white-space:nowrap"><a href="{{ url_for('descarregar_backup', nome=b.nome) }}"><button type="button" class="sec">Descarregar</button></a>
<form class="in" method="post" onsubmit="return confirm('Restaurar {{ b.nome }}? Os dados atuais são substituídos (é feito um backup antes).')">
<input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="restaurar"><input type="hidden" name="nome" value="{{ b.nome }}"><button class="sec">Restaurar</button></form>
<form class="in" method="post" onsubmit="return confirm('Apagar {{ b.nome }}?')"><input type="hidden" name="csrf" value="{{ csrf() }}">
<input type="hidden" name="acao" value="apagar"><input type="hidden" name="nome" value="{{ b.nome }}"><button class="del">Apagar</button></form></td></tr>
{% else %}<tr><td colspan="4" class="mutt">Sem backups.</td></tr>{% endfor %}</table>
<p class="mutt">Ficam em <code>tesla/backups/</code> no NAS (volume). Incluem clientes, chaves (em hash), login, viagens, consumo e logs;
o <code>tesla.env</code>, as chaves Tesla e os tokens estão em <code>tesla/</code> e não fazem parte do backup.</p></div>""",
                  cont=cont, tam=tam, backups=c.listar_backups(), auto=c.get_def("backup_automatico"),
                  ret=c.get_def("backup_retencao"))


@app.get("/dados/exportar")
@login_obrigatorio
def exportar():
    periodo = request.args.get("periodo", "tudo")
    de, ate = c.intervalo_periodo(periodo, None)
    corpo, mime, nome = c.exportar(request.args.get("tipo", "viagens"), request.args.get("formato", "csv"), None, de, ate)
    c.log("INFO", "admin", f"exportação: {nome} ({periodo})")
    return Response(corpo, mimetype=mime, headers={"Content-Disposition": f"attachment; filename={nome}"})


@app.get("/dados/backup/<nome>")
@login_obrigatorio
def descarregar_backup(nome):
    return send_file(c.caminho_backup(nome), as_attachment=True, download_name=nome)


@app.get("/api-ajuda")
@login_obrigatorio
def api_ajuda():
    return render("API", "api", """
<div class="card"><h2>Como usar a API</h2>
<table class="ft"><tr><th>URL base (mesma rede Docker)</th><td><code>http://tesla-key:8000</code></td></tr>
<tr><th>Autenticação</th><td>Header <code>X-API-Key: tk_...</code> (chave do cliente, em Clientes)</td></tr>
<tr><th>Formato</th><td>JSON; erros em <code>{"erro": "..."}</code></td></tr>
<tr><th>Referência em JSON</th><td><code>GET /api/endpoints</code> (sem chave)</td></tr></table>
<pre>curl -H "X-API-Key: tk_..." http://tesla-key:8000/api/vehicles</pre></div>

{% for e in eps %}<div class="card">
<h2><span class="m {{ e.metodo }}">{{ e.metodo }}</span> <code>{{ e.caminho }}</code></h2>
<p>{{ e.descricao }}</p>
<table class="ft"><tr><th>Permissão</th><td>{% if e.permissao %}{% for p in e.permissao.split(', ') %}<span class="b mut">{{ p }}</span> {% endfor %}{% else %}<span class="mutt">nenhuma</span>{% endif %}</td></tr>
{% if e.parametros %}<tr><th>Parâmetros</th><td><code>{{ e.parametros }}</code></td></tr>{% endif %}</table>
<pre>curl {{ '-X POST ' if e.metodo == 'POST' }}{{ '-H "X-API-Key: tk_..." ' if e.permissao or e.caminho == '/api/me' }}"http://tesla-key:8000{{ e.caminho|replace('{vin}', '<VIN>')|replace('{id}', '1')|replace('{caminho}', 'dx/charging/history') }}"</pre>
<small class="mutt">Exemplo de resposta</small><pre>{{ e.exemplo }}</pre></div>{% endfor %}

<div class="card"><h2>Códigos de resposta</h2><table>
<tr><th>Código</th><th>Significado</th></tr>
<tr><td>200</td><td>OK</td></tr><tr><td>400</td><td>Parâmetro inválido</td></tr>
<tr><td>401</td><td>Chave em falta, errada ou cliente desativado</td></tr>
<tr><td>403</td><td>Sem permissão, funcionalidade desativada, ou scope em falta na Tesla</td></tr>
<tr><td>404</td><td>Caminho ou recurso inexistente</td></tr><tr><td>408</td><td>Carro a dormir ou offline</td></tr>
<tr><td>429</td><td>Limite de pedidos da Tesla</td></tr><tr><td>502 / 503</td><td>Falha na Tesla / gateway sem tokens</td></tr>
</table></div>""", eps=c.endpoints_json())



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001)
