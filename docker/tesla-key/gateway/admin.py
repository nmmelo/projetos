"""
tesla-key — portal admin (porta 8001, só LAN).

Login definido com `tesla-setup admin`. Permite ver o estado, gerir clientes, ver logs
e configurar a Status API e as definições.
"""
import json
import urllib.parse
import re
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, Response, abort, flash, jsonify, redirect, render_template_string, request,
                   send_file, session, url_for)
from markupsafe import Markup, escape

import common as c

c.init_db()
app = Flask(__name__)
app.secret_key = c.get_def("secret_key")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                  PERMANENT_SESSION_LIFETIME=timedelta(hours=8))

GRAF_CLARO = "--c1:#2a78d6;--c2:#eb6834;--c3:#1baf7a;--c4:#eda100;--c5:#e87ba4;--c6:#008300;"
GRAF_ESCURO = "--c1:#3987e5;--c2:#d95926;--c3:#199e70;--c4:#c98500;--c5:#d55181;--c6:#0ca30c;"
FONTE_SIS = "system-ui,-apple-system,Segoe UI,Roboto,sans-serif"
FONTE_TESLA = "'Gotham SSm','Gotham','Montserrat','Helvetica Neue',Helvetica,Arial,sans-serif"


def _v(bg, card, txt, mut, pri, ok, warn, err, bd, sec, hdr, hdr_txt, hdr_mut, okbg, warnbg, errbg, rad, font, extra=""):
    return (f"--bg:{bg};--card:{card};--txt:{txt};--mut:{mut};--pri:{pri};--pri-txt:#fff;--ok:{ok};--warn:{warn};"
            f"--err:{err};--bd:{bd};--sec:{sec};--code:{sec};--hdr:{hdr};--hdr-txt:{hdr_txt};--hdr-mut:{hdr_mut};"
            f"--okbg:{okbg};--warnbg:{warnbg};--errbg:{errbg};--rad:{rad};--font:{font};{extra}")


PALETAS = {
    "padrao": {
        "nome": "Padrão",
        "claro": _v("#f5f6f8", "#fff", "#1d2433", "#6b7385", "#3157d5", "#1f8a4c", "#b7791f", "#c53030", "#e3e6ec",
                    "#eceef2", "#1d2433", "#fff", "#c8cfe0", "#e3f4ea", "#fbf0dc", "#fde8e8", "8px", FONTE_SIS),
        "escuro": _v("#0f1218", "#171b23", "#e6e9f0", "#98a1b3", "#5b7cfa", "#4cc38a", "#e0a84b", "#f07171", "#2a303c",
                     "#262c38", "#0b0e13", "#fff", "#98a1b3", "rgba(76,195,138,.15)", "rgba(224,168,75,.15)",
                     "rgba(240,113,113,.15)", "8px", FONTE_SIS),
        "extra": "",
        "amostra": ["#1d2433", "#f5f6f8", "#3157d5", "#171b23"],
    },
    "tesla": {
        "nome": "Tesla (minimalista)",
        "claro": _v("#f4f4f4", "#fff", "#171a20", "#5c5e62", "#3e6ae1", "#2d7d46", "#9a6b00", "#c4161c", "#e2e3e3",
                    "#f4f4f4", "#fff", "#171a20", "#5c5e62", "#e8f3eb", "#f8f0dc", "#fbe7e8", "4px", FONTE_TESLA),
        "escuro": _v("#0d0d0d", "#171717", "#eeeeee", "#a2a3a5", "#3e6ae1", "#4caf6e", "#d9a441", "#e5484d", "#2b2b2b",
                     "#242424", "#0d0d0d", "#fff", "#a2a3a5", "rgba(76,175,110,.15)", "rgba(217,164,65,.15)",
                     "rgba(229,72,77,.15)", "4px", FONTE_TESLA),
        "extra": ("header{border-bottom:1px solid var(--bd)}header b{letter-spacing:.25em;text-transform:uppercase;font-weight:600;font-size:14px}"
                  "nav a{font-weight:500;font-size:13px;padding:4px 10px;border-radius:4px}nav a.on,nav a:hover{background:rgba(127,127,127,.12)}"
                  ".card,.kpi{border:0;box-shadow:0 1px 2px rgba(0,0,0,.06)}h2{font-weight:500;letter-spacing:.02em}"
                  "button{font-weight:500;letter-spacing:.02em;min-height:36px}button.sec{border:1px solid var(--bd)}"
                  ".entrada{background:linear-gradient(180deg,var(--card) 0%,var(--bg) 100%)}"
                  ".entrada h1{font-weight:500;letter-spacing:.08em;text-transform:uppercase}"),
        "amostra": ["#ffffff", "#171a20", "#3e6ae1", "#0d0d0d"],
    },
    "natal": {
        "nome": "Natal",
        "claro": _v("#f7f3ec", "#fffdf9", "#2a1a12", "#7a6656", "#b0141c", "#1f6b3a", "#a86b00", "#c0392b", "#eadfce",
                    "#f1e8da", "#8f1018", "#fff6e5", "#f3cfa0", "#e3f1e7", "#f8ecd2", "#f9e1df", "10px", FONTE_SIS,
                    "--ouro:#d4a72c;--neve:rgba(143,16,24,.09);"),
        "escuro": _v("#0e1a13", "#15261c", "#f4eee4", "#b9ad9c", "#e0474f", "#5cc489", "#e6b450", "#ff6b6b", "#24392c",
                     "#1d3125", "#0a130e", "#f4eee4", "#d8b36a", "rgba(92,196,137,.15)", "rgba(230,180,80,.15)",
                     "rgba(255,107,107,.15)", "10px", FONTE_SIS, "--ouro:#e3bc4f;--neve:rgba(255,255,255,.35);"),
        "extra": ("header{border-bottom:3px solid var(--ouro)}header b::after{content:'\\2726';color:var(--ouro);margin-left:2px}"
                  "nav a.on{color:var(--ouro)!important}.abas a.on{border-bottom-color:var(--ouro)}"
                  ".entrada{background-color:var(--bg);background-image:radial-gradient(circle,var(--neve) 1.5px,transparent 2px),"
                  "radial-gradient(circle,var(--neve) 1px,transparent 1.5px);background-size:46px 46px,23px 23px;background-position:0 0,11px 17px}"
                  ".entrada .caixa{border-top:4px solid var(--ouro)}.entrada .ic{color:var(--pri)}"
                  ".kpi{border-top:3px solid var(--ouro)}"),
        "amostra": ["#8f1018", "#fffdf9", "#1f6b3a", "#d4a72c"],
    },
}
MODOS = {"sistema": "Como o sistema (claro/escuro do dispositivo)", "hora": "Pela hora (claro das 8h às 20h, escuro à noite)",
         "claro": "Sempre claro", "escuro": "Sempre escuro"}


def tema_css():
    p = PALETAS.get(c.get_def("tema") or "padrao", PALETAS["padrao"])
    modo = c.get_def("tema_modo") or "sistema"
    claro, escuro = p["claro"] + GRAF_CLARO, p["escuro"] + GRAF_ESCURO
    if modo == "claro":
        css = f":root{{{claro}color-scheme:light}}"
    elif modo == "escuro":
        css = f":root{{{escuro}color-scheme:dark}}"
    elif modo == "hora":
        css = f":root{{{claro}color-scheme:light}}:root.noite{{{escuro}color-scheme:dark}}"
    else:
        css = (f":root{{{claro}color-scheme:light dark}}"
               f"@media (prefers-color-scheme: dark){{:root{{{escuro}}}}}")
    return css + p["extra"]


TEMA_JS = """<script>(function(){var h=new Date().getHours();if(h<8||h>=20)document.documentElement.classList.add('noite');})();</script>"""


ICONE = """<svg viewBox="0 0 64 64" width="{s}" height="{s}" aria-hidden="true"><circle cx="32" cy="32" r="30" fill="none" stroke="currentColor" stroke-width="3"/>
<path d="M35 10 19 36h11l-3 18 18-28H34l1-16z" fill="currentColor"/></svg>"""

BASE = """<!doctype html>
<html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#171a20"><title>Gateway Tesla API · {{ titulo }}</title>
{% if modo == 'hora' %}{{ tema_js|safe }}{% endif %}
<style>
{{ tema_css|safe }}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 var(--font);background:var(--bg);color:var(--txt)}
a{color:var(--pri)}
header{background:var(--hdr);color:var(--hdr-txt);padding:0 24px;display:flex;align-items:center;gap:20px;min-height:54px;position:sticky;top:0;z-index:1000}
header b{font-size:15px;display:flex;align-items:center;gap:8px;white-space:nowrap}header a{color:var(--hdr-mut);text-decoration:none}header a.on,header a:hover{color:var(--hdr-txt)}
header .sp{flex:1}.quem{color:var(--hdr-mut);font-size:13px;white-space:nowrap}main{max-width:1100px;margin:24px auto;padding:0 16px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:var(--rad);padding:16px 20px;margin-bottom:16px}
h2{font-size:15px;margin:0 0 12px}h3{font-size:14px;margin:14px 0 8px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.kpi{background:var(--card);border:1px solid var(--bd);border-radius:var(--rad);padding:14px}.kpi small{color:var(--mut)}.kpi div{font-size:22px;font-weight:600}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--bd);vertical-align:top;overflow-wrap:break-word}
th{white-space:nowrap}td code{word-break:break-all}.b{white-space:nowrap}
td.q{overflow-wrap:anywhere;word-break:break-word}td.ped{min-width:18ch;max-width:46ch}td.msg{min-width:22ch}
.card,.kpi{min-width:0;overflow-wrap:break-word}select,input{max-width:100%}pre{overflow-wrap:anywhere}
th{color:var(--mut);font-weight:500;font-size:12px;text-transform:uppercase}
.b{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600}
.ok{background:var(--okbg);color:var(--ok)}.warn{background:var(--warnbg);color:var(--warn)}.err{background:var(--errbg);color:var(--err)}.mut{background:var(--sec);color:var(--mut)}
input,select{padding:7px 9px;border:1px solid var(--bd);border-radius:var(--rad);font:inherit;background:var(--card);color:var(--txt)}
button{padding:7px 12px;border:0;border-radius:var(--rad);background:var(--pri);color:var(--pri-txt);font:inherit;cursor:pointer}
button.sec{background:var(--sec);color:var(--txt)}button.del{background:var(--err);color:#fff}button:disabled{opacity:.5;cursor:not-allowed}
form.in{display:inline}.fl{padding:10px 14px;border-radius:var(--rad);margin-bottom:12px}
.fl.ok{border:1px solid var(--ok)}.fl.err{border:1px solid var(--err)}.fl.warn{border:1px solid var(--warn)}
code{background:var(--code);padding:2px 6px;border-radius:4px;word-break:break-all}.mutt{color:var(--mut)}
.key{font-family:ui-monospace,monospace;background:#1d2433;color:#7ee2a8;padding:10px;border-radius:6px;word-break:break-all;margin:6px 0}
nav{display:flex;gap:16px;align-items:center}
.row{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:flex-end;margin-bottom:8px}.row label{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--mut)}
.chk{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:6px 16px;margin:6px 0 12px}.chk label{display:flex;gap:8px;align-items:flex-start}
.barra{height:10px;background:var(--sec);border-radius:6px;overflow:hidden}.barra div{height:100%;background:var(--pri)}
.barra div.warn{background:var(--warn)}.barra div.err{background:var(--err)}
.mapa{height:480px;border-radius:var(--rad);border:1px solid var(--bd)}
.m{font-family:ui-monospace,monospace;font-size:12px;font-weight:700;padding:2px 7px;border-radius:4px;color:#fff}
.m.GET{background:#1f8a4c}.m.POST{background:#b7791f}
pre{background:#1d2433;color:#e6e9f0;padding:10px;border-radius:6px;overflow-x:auto;font-size:12px;white-space:pre-wrap;word-break:break-all}
.abas{display:flex;gap:4px;border-bottom:1px solid var(--bd);margin:0 0 16px;overflow-x:auto;scrollbar-width:none}
.abas a{padding:9px 14px;color:var(--mut);text-decoration:none;white-space:nowrap;border-bottom:2px solid transparent;margin-bottom:-1px}
.abas a.on{color:var(--txt);border-bottom-color:var(--pri);font-weight:600}
.sub{display:flex;gap:6px;flex-wrap:nowrap;overflow-x:auto;margin:0 0 14px;padding-bottom:4px;scrollbar-width:thin}
.sub a{padding:5px 11px;border-radius:16px;background:var(--sec);color:var(--txt);text-decoration:none;white-space:nowrap;font-size:13px}
.sub a.on{background:var(--pri);color:var(--pri-txt)}
.donuts{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:20px}
.donut{display:flex;gap:16px;align-items:center;flex-wrap:wrap}.donut svg{flex:0 0 150px}
.donut ul{list-style:none;margin:0;padding:0;flex:1;min-width:150px}.donut li{display:flex;gap:8px;align-items:center;padding:3px 0;font-size:13px}
.donut li span.s{width:12px;height:12px;border-radius:3px;flex:0 0 12px}.donut li .n{flex:1;min-width:0}
.donut li b{font-weight:600;white-space:nowrap}.donut li .p{width:42px;text-align:right;white-space:nowrap}
.donut circle.f{transition:stroke-width .15s}.donut circle.f:hover{stroke-width:7}
.leitura{background:var(--warnbg);color:var(--warn);text-align:center;padding:6px;font-size:13px}
.entrada{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:16px}
.entrada .caixa{background:var(--card);border:1px solid var(--bd);border-radius:calc(var(--rad)*2);padding:36px 28px;width:100%;max-width:380px;text-align:center}
.entrada h1{font-size:22px;margin:14px 0 4px}.entrada .ic{color:var(--txt)}
.entrada form{text-align:left;margin-top:18px}.entrada input{width:100%;margin:6px 0}.entrada button{width:100%;margin-top:10px;padding:11px}
.hp{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}
@media (max-width:760px){
 header{flex-wrap:wrap;padding:8px 12px;gap:6px 12px}
 header nav{order:3;width:100%;overflow-x:auto;gap:14px;padding-bottom:4px;white-space:nowrap;scrollbar-width:none}
 main{margin:12px auto;padding:0 8px}.card{padding:12px}
 .grid{grid-template-columns:repeat(2,1fr);gap:8px}.kpi div{font-size:18px}
 table:not(.rt):not(.ft){display:block;overflow-x:auto;-webkit-overflow-scrolling:touch}
 table:not(.rt):not(.ft) th,table:not(.rt):not(.ft) td{white-space:nowrap;padding:6px}
 table.rt,table.rt tbody{display:block}table.rt tr.rh{display:none}
 table.rt tr{display:block;border:1px solid var(--bd);border-radius:var(--rad);margin:0 0 8px;padding:6px 12px;background:var(--card)}
 table.rt td{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;border:0;padding:5px 0;text-align:right;min-width:0;overflow-wrap:anywhere;word-break:break-word}
 table.rt td>*{min-width:0;max-width:100%}table.rt td code{white-space:normal}table.rt td select{width:auto;max-width:60%}
 table.rt td form.in{display:inline-flex}
 table.rt td::before{content:attr(data-l);color:var(--mut);font-size:11px;text-transform:uppercase;text-align:left;flex:0 0 34%;max-width:34%;padding-top:2px;overflow-wrap:anywhere}
 table.rt td[data-l=""]{justify-content:flex-start;flex-wrap:wrap;text-align:left}table.rt td[data-l=""]::before{display:none}
 table.rt td.sel{justify-content:flex-start}
 input:not([type=checkbox]):not([type=radio]),select{width:100%}.row label{width:100%}
 button{padding:10px 12px}form.in{display:inline-block;margin:2px 0}
 table.ft,table.ft tbody,table.ft tr,table.ft td,table.ft th{display:block;width:100%;white-space:normal}
 table.ft td,table.ft th{border-bottom:0;padding:4px 0}table.ft tr{border-bottom:1px solid var(--bd);padding:8px 0}
 .mapa{height:360px}
 .donut{flex-direction:column;align-items:stretch}.donut svg{flex:0 0 auto;width:160px;align-self:center}
}
</style></head><body>
{% if session.get('user') %}
<header><b>{{ icone(20)|safe }} Gateway Tesla</b>
<nav>
<a href="{{ url_for('painel') }}" class="{{ 'on' if pag=='painel' }}">Estado</a>
<a href="{{ url_for('consumo') }}" class="{{ 'on' if pag=='consumo' }}">Consumo</a>
<a href="{{ url_for('viagens') }}" class="{{ 'on' if pag=='viagens' }}">Viagens</a>
<a href="{{ url_for('carregamentos') }}" class="{{ 'on' if pag=='carregamentos' }}">Carregamentos</a>
<a href="{{ url_for('bateria') }}" class="{{ 'on' if pag=='bateria' }}">Bateria</a>
<a href="{{ url_for('mapa') }}" class="{{ 'on' if pag=='mapa' }}">Mapa</a>
<a href="{{ url_for('clientes') }}" class="{{ 'on' if pag=='clientes' }}">Clientes</a>
<a href="{{ url_for('tesla_conf') }}" class="{{ 'on' if pag=='tesla' }}">Tesla</a>
<a href="{{ url_for('definicoes') }}" class="{{ 'on' if pag=='definicoes' }}">Definições</a>
</nav>
<span class="sp"></span><span class="quem">{{ session['user'] }}{{ ' · leitura' if session.get('perfil') != 'admin' }}</span>
<form class="in" method="post" action="{{ url_for('logout') }}"><input type="hidden" name="csrf" value="{{ csrf() }}"><button class="sec">Sair</button></form>
</header>
{% if session.get('perfil') != 'admin' %}<div class="leitura">Perfil só de leitura: as alterações estão desativadas.</div>{% endif %}
{% endif %}
{% if pag == 'entrada' %}{{ corpo|safe }}{% else %}
<main>
{% for cat, msg in get_flashed_messages(with_categories=true) %}<div class="fl {{ cat }} card">{{ msg }}</div>{% endfor %}
{% if nova_chave %}<div class="card fl warn"><b>{{ nova_chave.titulo }}</b> — copie agora, não volta a ser mostrada:
<div class="key">{{ nova_chave.valor }}</div></div>{% endif %}
{% if abas %}{{ abas|safe }}{% endif %}
{{ corpo|safe }}
</main>{% endif %}
<script>
// Tabelas com cabeçalho passam a cartões no telemóvel (cada célula mostra o nome da coluna)
document.querySelectorAll('table:not(.ft)').forEach(function (t) {
  var cab = Array.prototype.find.call(t.rows, function (r) { return r.cells.length && Array.prototype.every.call(r.cells, function (c) { return c.tagName === 'TH'; }); });
  if (!cab) { t.classList.add('ft'); return; }   // tabela chave/valor: empilha no telemóvel
  var nomes = Array.prototype.map.call(cab.cells, function (c) { return c.textContent.trim(); });
  t.classList.add('rt'); cab.classList.add('rh');
  Array.prototype.forEach.call(t.rows, function (r) {
    if (r === cab) return;
    Array.prototype.forEach.call(r.cells, function (c, i) { if (!c.hasAttribute('data-l')) c.setAttribute('data-l', c.colSpan > 1 ? '' : (nomes[i] || '')); });
  });
});
{% if session.get('user') and session.get('perfil') != 'admin' %}
document.querySelectorAll('main form[method=post]:not(.livre) button, main form[method=post]:not(.livre) input, main form[method=post]:not(.livre) select').forEach(function (e) { if (e.type !== 'hidden') e.disabled = true; });
{% endif %}
</script>
</body></html>"""

ABAS_DEF = [("geral", "Geral", "definicoes"), ("acessos", "Acessos", "acessos"), ("logs", "Logs", "logs"),
            ("dados", "Dados", "dados"), ("api", "API", "api_ajuda"), ("temas", "Temas", "temas")]


def abas_definicoes(atual):
    return "<div class='abas'>" + "".join(
        f"<a href='{url_for(fn)}' class='{'on' if k == atual else ''}'>{n}</a>" for k, n, fn in ABAS_DEF) + "</div>"


def icone(s=28):
    return ICONE.replace("{s}", str(s))


def csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def render(titulo, pag, corpo, aba=None, **ctx):
    nova = session.pop("nova_chave", None)
    corpo_html = render_template_string(corpo, csrf=csrf, icone=icone, **ctx)
    return render_template_string(BASE, titulo=titulo, pag=pag, corpo=corpo_html, csrf=csrf, nova_chave=nova,
                                  icone=icone, tema_css=tema_css(), tema_js=TEMA_JS,
                                  modo=c.get_def("tema_modo") or "sistema",
                                  abas=abas_definicoes(aba) if aba else "")


def ip_cliente():
    return request.remote_addr or "?"


# Rotas que um perfil "leitura" pode usar com POST
POST_LEITURA = {"logout", "minha_password"}
# Rotas GET só para administradores
GET_ADMIN = {"descarregar_backup"}


def login_obrigatorio(f):
    @wraps(f)
    def w(*a, **kw):
        user = session.get("user")
        if not user:
            return redirect(url_for("login", proximo=request.path))
        perfil = c.perfil_utilizador(user)
        if not perfil:                              # apagado ou desativado entretanto
            session.clear()
            flash("Sessão terminada: utilizador inativo", "err")
            return redirect(url_for("login"))
        session["perfil"] = perfil
        if request.method == "POST":
            if request.form.get("csrf") != session.get("csrf"):
                abort(400, "CSRF inválido")
            if perfil != "admin" and request.endpoint not in POST_LEITURA:
                c.auditar("negado", ip_cliente(), user, f"{request.path} {request.form.get('acao', '')}".strip())
                flash("O seu perfil é só de leitura", "err")
                return redirect(request.referrer or url_for("painel"))
            if request.endpoint not in ("logout",):
                c.auditar("acao", ip_cliente(), user, f"{request.path} {request.form.get('acao', '')}".strip(),
                          request.user_agent.string)
        elif request.endpoint in GET_ADMIN and perfil != "admin":
            flash("Disponível só para administradores", "err")
            return redirect(request.referrer or url_for("painel"))
        return f(*a, **kw)
    return w


def mostrar_chave(titulo, valor):
    session["nova_chave"] = {"titulo": titulo, "valor": valor}


@app.errorhandler(c.ErroGateway)
def erro_gw(e):
    flash(e.mensagem, "err")
    return redirect(request.referrer or url_for("painel"))


# ---------------------------------------------------------------------------
# Entrada (login)
# ---------------------------------------------------------------------------

def novo_desafio():
    a, b = secrets.randbelow(9) + 1, secrets.randbelow(9) + 1
    session["desafio"] = {"r": a + b, "t": time.time()}
    return f"Quanto é {a} + {b}?"


ENTRADA = """<div class="entrada"><div class="caixa">
<div class="ic">{{ icone(64)|safe }}</div>
<h1>Gateway Tesla API</h1><div class="mutt">Portal de administração</div>
{% for cat, msg in get_flashed_messages(with_categories=true) %}<div class="fl {{ cat }}" style="margin-top:14px">{{ msg }}</div>{% endfor %}
{% if sem_utilizadores %}
<p style="margin-top:18px">Portal ainda não configurado. Crie o primeiro administrador dentro do contentor:</p><code>tesla-setup admin</code>
{% elif bloqueado %}
<p style="margin-top:18px" class="err fl">Demasiadas tentativas falhadas a partir deste endereço. Tente de novo dentro de {{ (bloqueado // 60) + 1 }} min.</p>
{% else %}
<button id="entrar" type="button" style="width:100%;margin-top:22px;padding:12px" onclick="document.getElementById('cred').style.display='block';this.style.display='none';document.getElementById('u').focus()">Entrar</button>
<form id="cred" method="post" style="display:{{ 'block' if mostrar else 'none' }}" autocomplete="on">
<input type="hidden" name="csrf" value="{{ csrf() }}">
<div class="hp"><label>Website <input name="website" tabindex="-1" autocomplete="off"></label></div>
<input id="u" name="user" placeholder="Utilizador" autocomplete="username" required>
<input name="password" type="password" placeholder="Password" autocomplete="current-password" required>
<label class="mutt" style="display:block;margin-top:10px;font-size:13px">Verificação: {{ pergunta }}</label>
<input name="humano" inputmode="numeric" pattern="[0-9]*" placeholder="Resposta" autocomplete="off" required>
<button>Entrar</button></form>
<noscript><style>#cred{display:block!important}#entrar{display:none}</style></noscript>
{% endif %}
</div></div>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    ip = ip_cliente()
    if not c.existem_utilizadores():
        return render("Entrar", "entrada", ENTRADA, sem_utilizadores=True)
    espera = c.ip_bloqueado(ip)
    if espera:
        if request.method == "POST":
            c.auditar("bloqueado", ip, request.form.get("user", "")[:40], "tentativa durante o bloqueio",
                      request.user_agent.string)
        return render("Entrar", "entrada", ENTRADA, bloqueado=espera)
    mostrar = False
    if request.method == "POST":
        mostrar = True
        u, p = request.form.get("user", "").strip(), request.form.get("password", "")
        d = session.get("desafio") or {}
        humano_ok = (request.form.get("csrf") == session.get("csrf") and not request.form.get("website")
                     and str(d.get("r")) == request.form.get("humano", "").strip()
                     and time.time() - d.get("t", 0) >= 2)
        perfil = c.validar_utilizador(u, p) if humano_ok else None
        if perfil:
            session.clear()
            session.permanent = True
            session["user"], session["perfil"] = u, perfil
            c.auditar("login", ip, u, f"perfil {perfil}", request.user_agent.string)
            c.log("INFO", "admin", f"login: {u}", cliente=ip)
            proximo = request.args.get("proximo") or ""
            return redirect(proximo if proximo.startswith("/") and not proximo.startswith("//") else url_for("painel"))
        motivo = "verificação humana falhada" if not humano_ok else "credenciais inválidas"
        c.auditar("login_falhado", ip, u[:40], motivo, request.user_agent.string)
        c.log("WARNING", "admin", f"login falhado: {u[:40]} ({motivo})", cliente=ip)
        flash("Não foi possível entrar. Verifique os dados e a resposta à verificação.", "err")
        espera = c.ip_bloqueado(ip)
        if espera:
            return render("Entrar", "entrada", ENTRADA, bloqueado=espera)
    return render("Entrar", "entrada", ENTRADA, pergunta=novo_desafio(), mostrar=mostrar)


@app.post("/logout")
@login_obrigatorio
def logout():
    c.auditar("logout", ip_cliente(), session.get("user"))
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


def tabela_logs(linhas, selecionar=False):
    return Markup(render_template_string("""<table>
<tr>{% if selecionar %}<th><input type="checkbox" onclick="document.querySelectorAll('input[name=id]').forEach(function(x){x.checked=this.checked}.bind(this))" aria-label="todos"></th>{% endif %}<th>Hora (UTC)</th><th>Nível</th><th>Origem</th><th>Cliente</th><th>Pedido</th><th>Status</th><th>ms</th><th>Mensagem</th></tr>
{% for l in linhas %}<tr>{% if selecionar %}<td class="sel" data-l="Selecionar"><input type="checkbox" name="id" value="{{ l.id }}" form="apagar_sel"></td>{% endif %}<td style="white-space:nowrap">{{ l.ts[:19].replace('T',' ') }}</td>
<td><span class="b {{ {'INFO':'ok','WARNING':'warn','ERROR':'err'}[l.nivel] }}">{{ l.nivel }}</span></td>
<td>{{ l.origem }}</td><td style="white-space:nowrap">{{ l.cliente or '' }}</td><td class="q ped">{{ (l.metodo or '') ~ ' ' ~ unq(l.caminho or '') }}</td>
<td>{{ l.status or '' }}</td><td>{{ l.ms if l.ms is not none else '' }}</td><td class="q msg">{{ l.mensagem or '' }}</td></tr>
{% else %}<tr><td colspan="9" class="mutt">Sem registos.</td></tr>{% endfor %}</table>""", linhas=linhas, selecionar=selecionar,
                                         unq=urllib.parse.unquote))


def sub_logs(atual):
    return Markup(f"<div class='sub'><a href='{url_for('logs')}' class='{'on' if atual == 'registos' else ''}'>Registos</a>"
                  f"<a href='{url_for('logs_api')}' class='{'on' if atual == 'api' else ''}'>API</a>"
                  f"<a href='{url_for('auditoria')}' class='{'on' if atual == 'auditoria' else ''}'>Auditoria</a></div>")


@app.get("/logs/api")
@login_obrigatorio
def logs_api():
    codigo = request.args.get("codigo") or None
    cliente = request.args.get("cliente") or None
    periodo = request.args.get("periodo", "7")
    incluir_ok = request.args.get("ok") == "1"
    ref = request.args.get("ref", "").strip().lstrip("#")
    desde = None if periodo == "tudo" else (datetime.now(timezone.utc) - timedelta(days=int(periodo) if periodo.isdigit() else 7)).isoformat(timespec="seconds")
    if ref.isdigit():
        with c.db() as con:
            linhas = [dict(r) for r in con.execute("SELECT * FROM logs WHERE id=?", (int(ref),))]
        _, resumo = c.ler_erros_api(desde=desde)
    else:
        linhas, resumo = c.ler_erros_api(codigo, cliente, desde, incluir_ok, min(int(request.args.get("limite", 300)), 3000))
    return render("Logs da API", "definicoes", """{{ sub }}
<div class="card"><h2>Erros da API por código</h2>
<p class="mutt">Os clientes recebem só o código e uma referência (<code>ref</code>); o motivo fica aqui.</p>
<table><tr><th>Código</th><th>Significado</th><th>Ocorrências</th></tr>
{% for r in resumo %}<tr><td><a href="?codigo={{ r.status }}&periodo={{ periodo }}"><span class="b {{ 'err' if r.status >= 500 else 'mut' if r.status == 408 else 'warn' }}">{{ r.status }}</span></a></td>
<td>{{ cod.get(r.status, ('', '—'))[1] }}</td><td>{{ r.n }}</td></tr>
{% else %}<tr><td colspan="3" class="mutt">Sem erros neste período.</td></tr>{% endfor %}</table></div>
<div class="card"><form method="get" class="row">
<label>Referência<input name="ref" value="{{ ref }}" placeholder="ex.: 1234" inputmode="numeric"></label>
<label>Código<select name="codigo"><option value="">todos os erros</option>
{% for k, (n, d) in cod.items() if k >= 400 %}<option value="{{ k }}" {{ 'selected' if codigo == k|string }}>{{ k }} {{ n }}</option>{% endfor %}</select></label>
<label>Cliente<select name="cliente"><option value="">todos</option>
{% for x in clientes %}<option {{ 'selected' if x.nome==cliente }}>{{ x.nome }}</option>{% endfor %}</select></label>
<label>Período<select name="periodo">{% for v, n in [('1','24 h'),('7','7 dias'),('30','30 dias'),('tudo','Tudo')] %}
<option value="{{ v }}" {{ 'selected' if v==periodo }}>{{ n }}</option>{% endfor %}</select></label>
<label style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" name="ok" value="1" {{ 'checked' if incluir_ok }}> incluir pedidos com sucesso</label>
<button>Filtrar</button></form></div>
<div class="card">
<form id="apagar_sel" method="post" action="{{ url_for('logs') }}" class="in" onsubmit="return confirm('Apagar os registos selecionados?')">
<input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="apagar_sel"><button class="del">Apagar selecionados</button></form>
<table style="margin-top:10px">
<tr><th><input type="checkbox" onclick="document.querySelectorAll('input[name=id]').forEach(function(x){x.checked=this.checked}.bind(this))" aria-label="todos"></th>
<th>Data (UTC)</th><th>Ref</th><th>Cliente</th><th>Pedido</th><th>Código</th><th>Significado</th><th>Motivo</th></tr>
{% for l in linhas %}<tr><td class="sel" data-l="Selecionar"><input type="checkbox" name="id" value="{{ l.id }}" form="apagar_sel"></td>
<td style="white-space:nowrap">{{ l.ts[:19].replace('T',' ') }}</td><td>#{{ l.id }}</td><td>{{ l.cliente or '—' }}</td>
<td><code>{{ l.metodo or '' }} {{ l.caminho or '' }}</code></td>
<td><span class="b {{ 'ok' if (l.status or 0) < 400 else 'err' if l.status >= 500 else 'mut' if l.status == 408 else 'warn' }}">{{ l.status }}</span></td>
<td>{{ cod.get(l.status, ('', '—'))[1] }}</td><td>{{ l.mensagem or '—' }}</td></tr>
{% else %}<tr><td colspan="8" class="mutt">Sem registos.</td></tr>{% endfor %}</table></div>
<div class="card"><h2>Resposta de erro para os clientes</h2>
<form method="post" action="{{ url_for('definicoes') }}"><input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="erros">
<label style="display:flex;gap:8px;padding:4px 0"><input type="radio" name="erros_detalhados" value="0" {{ 'checked' if not detalhe }}>
<span>Só o código e a referência (recomendado): <code>{"codigo": 403, "erro": "Forbidden", "ref": 1234}</code></span></label>
<label style="display:flex;gap:8px;padding:4px 0"><input type="radio" name="erros_detalhados" value="1" {{ 'checked' if detalhe }}>
<span>Também o motivo (útil a desenvolver): acrescenta <code>"detalhe": "..."</code></span></label>
<button>Guardar</button></form></div>""",
                  aba="logs", sub=sub_logs("api"), linhas=linhas, resumo=resumo, cod=c.CODIGOS_HTTP, codigo=codigo,
                  cliente=cliente, periodo=periodo, incluir_ok=incluir_ok, ref=ref, clientes=c.listar_clientes(),
                  detalhe=c.get_def("erros_detalhados") == "1")


@app.route("/logs", methods=["GET", "POST"])
@login_obrigatorio
def logs():
    if request.method == "POST":
        acao = request.form.get("acao")
        if acao == "apagar_sel":
            n = c.apagar_logs(ids=request.form.getlist("id"))
        elif acao == "apagar_dias":
            n = c.apagar_logs(dias=int(request.form.get("dias") or 30))
        elif acao == "apagar_todos":
            n = c.apagar_logs(tudo=True, nivel=request.form.get("nivel") or None)
        else:
            n = 0
        c.log("WARNING", "admin", f"{n} registos de log apagados por {session['user']}")
        flash(f"{n} registos apagados", "ok")
        return redirect(request.referrer or url_for("logs"))
    nivel = request.args.get("nivel") or None
    cliente = request.args.get("cliente") or None
    limite = min(int(request.args.get("limite", 200)), 2000)
    return render("Logs", "definicoes", """{{ sub }}
<div class="card"><form method="get" class="row">
<label>Nível<select name="nivel"><option value="">todos</option>
{% for n in ['INFO','WARNING','ERROR'] %}<option {{ 'selected' if n==nivel }}>{{ n }}</option>{% endfor %}</select></label>
<label>Cliente<select name="cliente"><option value="">todos</option>
{% for x in clientes %}<option {{ 'selected' if x.nome==cliente }}>{{ x.nome }}</option>{% endfor %}</select></label>
<label>Mostrar<select name="limite">{% for n in [100,200,500,1000] %}<option {{ 'selected' if n==limite }}>{{ n }}</option>{% endfor %}</select></label>
<button>Filtrar</button></form></div>
<div class="card">
<form id="apagar_sel" method="post" class="in" onsubmit="return confirm('Apagar os registos selecionados?')">
<input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="apagar_sel"><button class="del">Apagar selecionados</button></form>
<form method="post" class="in" onsubmit="return confirm('Apagar todos os registos{{ ' de nível ' ~ nivel if nivel }}?')">
<input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="apagar_todos"><input type="hidden" name="nivel" value="{{ nivel or '' }}">
<button class="sec">Apagar todos{{ ' (' ~ nivel ~ ')' if nivel }}</button></form>
<div style="margin-top:10px">{{ tabela(linhas, True) }}</div></div>
<div class="card"><h2>Retenção</h2><form method="post" action="{{ url_for('definicoes') }}" class="row">
<input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="retencao">
<label>Logs (dias)<input name="retencao_logs_dias" type="number" min="1" value="{{ ret }}"></label>
<label>Auditoria (dias)<input name="retencao_auditoria_dias" type="number" min="1" value="{{ ret_aud }}"></label>
<button>Guardar</button></form>
<form method="post" class="row"><input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="apagar_dias">
<label>Apagar logs com mais de (dias)<input name="dias" type="number" min="0" value="{{ ret }}"></label><button class="sec">Apagar</button></form>
<p class="mutt">A limpeza automática pela retenção é feita em cada arranque do contentor.</p></div>""",
                  aba="logs", sub=sub_logs("registos"), linhas=c.ler_logs(nivel, cliente, limite), nivel=nivel,
                  cliente=cliente, limite=limite, clientes=c.listar_clientes(), tabela=tabela_logs,
                  ret=c.get_def("retencao_logs_dias"), ret_aud=c.get_def("retencao_auditoria_dias"))


TIPOS_AUD = {"login": ("ok", "Login"), "login_falhado": ("err", "Login falhado"), "bloqueado": ("err", "Bloqueado"),
             "logout": ("mut", "Logout"), "acao": ("mut", "Ação"), "negado": ("warn", "Negado")}


@app.route("/logs/auditoria", methods=["GET", "POST"])
@login_obrigatorio
def auditoria():
    if request.method == "POST":
        acao = request.form.get("acao")
        if acao == "apagar_sel":
            n = c.apagar_auditoria(ids=request.form.getlist("id"))
        elif acao == "apagar_dias":
            n = c.apagar_auditoria(dias=int(request.form.get("dias") or 90))
        elif acao == "apagar_todos":
            n = c.apagar_auditoria(tudo=True)
        else:
            n = 0
        c.log("WARNING", "admin", f"{n} registos de auditoria apagados por {session['user']}")
        flash(f"{n} registos de auditoria apagados", "ok")
        return redirect(request.referrer or url_for("auditoria"))
    tipo = request.args.get("tipo") or None
    utilizador = request.args.get("utilizador") or None
    ip = request.args.get("ip") or None
    linhas = c.ler_auditoria(tipo, utilizador, ip, min(int(request.args.get("limite", 300)), 3000))
    return render("Auditoria", "definicoes", """{{ sub }}
<div class="card"><form method="get" class="row">
<label>Tipo<select name="tipo"><option value="">todos</option>
{% for k, (cl, n) in tipos.items() %}<option value="{{ k }}" {{ 'selected' if k==tipo }}>{{ n }}</option>{% endfor %}</select></label>
<label>Utilizador<input name="utilizador" value="{{ utilizador or '' }}"></label>
<label>IP<input name="ip" value="{{ ip or '' }}"></label>
<button>Filtrar</button></form></div>
<div class="card">
<form id="apagar_sel" method="post" class="in" onsubmit="return confirm('Apagar os registos selecionados?')">
<input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="apagar_sel"><button class="del">Apagar selecionados</button></form>
<form method="post" class="in" onsubmit="return confirm('Apagar TODA a auditoria?')"><input type="hidden" name="csrf" value="{{ csrf() }}">
<input type="hidden" name="acao" value="apagar_todos"><button class="sec">Apagar tudo</button></form>
<form method="post" class="in"><input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="apagar_dias">
<input type="hidden" name="dias" value="90"><button class="sec">Apagar com mais de 90 dias</button></form>
<table style="margin-top:10px">
<tr><th><input type="checkbox" onclick="document.querySelectorAll('input[name=id]').forEach(function(x){x.checked=this.checked}.bind(this))" aria-label="todos"></th>
<th>Data (UTC)</th><th>Tipo</th><th>Utilizador</th><th>IP</th><th>Detalhe</th><th>Navegador</th></tr>
{% for l in linhas %}<tr><td class="sel" data-l="Selecionar"><input type="checkbox" name="id" value="{{ l.id }}" form="apagar_sel"></td>
<td style="white-space:nowrap">{{ l.ts[:19].replace('T',' ') }}</td>
<td><span class="b {{ tipos.get(l.tipo, ('mut', l.tipo))[0] }}">{{ tipos.get(l.tipo, ('mut', l.tipo))[1] }}</span></td>
<td><a href="?utilizador={{ l.utilizador or '' }}">{{ l.utilizador or '—' }}</a></td><td><a href="?ip={{ l.ip or '' }}">{{ l.ip or '—' }}</a></td>
<td>{{ l.detalhe or '' }}</td><td class="mutt" style="font-size:12px">{{ (l.agente or '')[:60] }}</td></tr>
{% else %}<tr><td colspan="7" class="mutt">Sem registos.</td></tr>{% endfor %}</table></div>
<div class="card"><h2>Proteção do login</h2>
<form method="post" action="{{ url_for('definicoes') }}" class="row"><input type="hidden" name="csrf" value="{{ csrf() }}">
<input type="hidden" name="acao" value="login_protecao">
<label>Tentativas falhadas por IP<input name="login_max_tentativas" type="number" min="1" value="{{ p.login_max_tentativas }}"></label>
<label>Na janela de (min)<input name="login_janela_minutos" type="number" min="1" value="{{ p.login_janela_minutos }}"></label>
<label>Bloqueio (min)<input name="login_bloqueio_minutos" type="number" min="1" value="{{ p.login_bloqueio_minutos }}"></label>
<button>Guardar</button></form>
<p class="mutt">O login pede ainda uma verificação (soma simples), tem um campo-armadilha para robôs e recusa submissões feitas em menos de 2 s.</p></div>""",
                  aba="logs", sub=sub_logs("auditoria"), linhas=linhas, tipos=TIPOS_AUD, tipo=tipo,
                  utilizador=utilizador, ip=ip,
                  p={k: c.get_def(k) for k in ("login_max_tentativas", "login_janela_minutos", "login_bloqueio_minutos")})


@app.route("/definicoes/acessos", methods=["GET", "POST"])
@login_obrigatorio
def acessos():
    if request.method == "POST":
        acao, nome = request.form.get("acao"), request.form.get("nome", "")
        if acao == "criar":
            if request.form.get("password") != request.form.get("confirmar"):
                flash("As passwords não coincidem", "err")
            else:
                c.definir_utilizador(nome, request.form.get("password"), request.form.get("perfil"))
                flash(f"Utilizador '{nome}' criado", "ok")
        elif acao == "perfil":
            c.definir_utilizador(nome, perfil=request.form.get("perfil"), criar=False)
            flash(f"Perfil de '{nome}' alterado", "ok")
        elif acao in ("ativar", "desativar"):
            if nome == session["user"] and acao == "desativar":
                flash("Não pode desativar o seu próprio utilizador", "err")
            else:
                c.definir_utilizador(nome, ativo=acao == "ativar", criar=False)
                flash(f"Utilizador '{nome}' {acao}do", "ok")
        elif acao == "password":
            c.definir_utilizador(nome, password=request.form.get("password"), criar=False)
            flash(f"Password de '{nome}' alterada", "ok")
        elif acao == "apagar":
            if nome == session["user"]:
                flash("Não pode apagar o seu próprio utilizador", "err")
            else:
                c.apagar_utilizador(nome)
                flash(f"Utilizador '{nome}' apagado", "ok")
        return redirect(url_for("acessos"))
    return render("Acessos", "definicoes", """
<div class="card"><h2>Utilizadores do portal</h2>
<table><tr><th>Utilizador</th><th>Perfil</th><th>Estado</th><th>Último login</th><th>Criado</th><th></th></tr>
{% for u in lista %}<tr><td><b>{{ u.nome }}</b>{{ ' (eu)' if u.nome == eu }}</td>
<td><form method="post" class="in"><input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="perfil">
<input type="hidden" name="nome" value="{{ u.nome }}"><select name="perfil" onchange="this.form.submit()" style="width:auto">
{% for k, d in perfis.items() %}<option value="{{ k }}" {{ 'selected' if u.perfil==k }}>{{ 'Administrador' if k=='admin' else 'Só leitura' }}</option>{% endfor %}</select></form></td>
<td>{% if u.ativo %}<span class="b ok">ativo</span>{% else %}<span class="b mut">inativo</span>{% endif %}</td>
<td>{{ (u.ultimo_login or '—')[:16].replace('T',' ') }}</td><td>{{ (u.criado_em or '—')[:10] }}</td>
<td style="white-space:nowrap">
<form method="post" class="in"><input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="nome" value="{{ u.nome }}">
<input type="hidden" name="acao" value="{{ 'desativar' if u.ativo else 'ativar' }}"><button class="sec">{{ 'Desativar' if u.ativo else 'Ativar' }}</button></form>
<form method="post" class="in" onsubmit="var p=prompt('Nova password para {{ u.nome }} (mín. 8)'); if(!p) return false; this.password.value=p;">
<input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="nome" value="{{ u.nome }}"><input type="hidden" name="acao" value="password">
<input type="hidden" name="password"><button class="sec">Password</button></form>
<form method="post" class="in" onsubmit="return confirm('Apagar o utilizador {{ u.nome }}?')"><input type="hidden" name="csrf" value="{{ csrf() }}">
<input type="hidden" name="nome" value="{{ u.nome }}"><input type="hidden" name="acao" value="apagar"><button class="del">Apagar</button></form></td></tr>{% endfor %}
</table></div>
<div class="card"><h2>Novo utilizador</h2><form method="post" class="row"><input type="hidden" name="csrf" value="{{ csrf() }}">
<input type="hidden" name="acao" value="criar">
<label>Utilizador<input name="nome" required pattern="[A-Za-z0-9._-]{2,32}"></label>
<label>Perfil<select name="perfil"><option value="leitura">Só leitura</option><option value="admin">Administrador</option></select></label>
<label>Password<input name="password" type="password" minlength="8" required autocomplete="new-password"></label>
<label>Confirmar<input name="confirmar" type="password" minlength="8" required autocomplete="new-password"></label>
<button>Criar</button></form>
<table class="ft"><tr><th>Administrador</th><td>Vê e altera tudo: clientes, permissões, tesla.env, definições, utilizadores, backups.</td></tr>
<tr><th>Só leitura</th><td>Vê todas as páginas (segredos sempre mascarados) e exporta dados; não altera nada nem descarrega backups. Pode mudar a própria password.</td></tr></table></div>
<div class="card"><h2>A minha password</h2><form method="post" action="{{ url_for('minha_password') }}" class="row livre">
<input type="hidden" name="csrf" value="{{ csrf() }}">
<label>Atual<input name="atual" type="password" required autocomplete="current-password"></label>
<label>Nova (mín. 8)<input name="nova" type="password" minlength="8" required autocomplete="new-password"></label>
<label>Confirmar<input name="confirmar" type="password" minlength="8" required autocomplete="new-password"></label>
<button>Alterar</button></form></div>""",
                  aba="acessos", lista=c.listar_utilizadores(), perfis=c.PERFIS, eu=session["user"])


@app.post("/definicoes/minha-password")
@login_obrigatorio
def minha_password():
    if not c.validar_utilizador(session["user"], request.form.get("atual", "")):
        flash("Password atual errada", "err")
    elif request.form.get("nova") != request.form.get("confirmar"):
        flash("As passwords não coincidem", "err")
    else:
        c.definir_utilizador(session["user"], password=request.form.get("nova", ""), criar=False)
        flash("Password alterada", "ok")
    return redirect(url_for("acessos"))


@app.route("/definicoes/temas", methods=["GET", "POST"])
@login_obrigatorio
def temas():
    if request.method == "POST":
        t, m = request.form.get("tema"), request.form.get("modo")
        if t in PALETAS:
            c.set_def("tema", t)
        if m in MODOS:
            c.set_def("tema_modo", m)
        flash("Aparência guardada", "ok")
        return redirect(url_for("temas"))
    return render("Temas", "definicoes", """
<form method="post"><input type="hidden" name="csrf" value="{{ csrf() }}">
<div class="card"><h2>Tema</h2><p class="mutt">Aplica-se a todos os utilizadores e ao ecrã de entrada. Cada tema tem uma versão clara e uma escura.</p>
<div class="grid">{% for k, p in paletas.items() %}
<label class="kpi" style="display:flex;flex-direction:column;gap:10px;cursor:pointer">
<span style="display:flex;gap:8px;align-items:center"><input type="radio" name="tema" value="{{ k }}" {{ 'checked' if k == tema }}> <b>{{ p.nome }}</b></span>
<span style="display:flex;gap:6px">{% for cor in p.amostra %}<span style="width:26px;height:26px;border-radius:50%;background:{{ cor }};border:1px solid #9995"></span>{% endfor %}</span>
</label>{% endfor %}</div></div>
<div class="card"><h2>Claro / escuro</h2>
{% for k, d in modos.items() %}<label style="display:flex;gap:8px;align-items:flex-start;padding:6px 0">
<input type="radio" name="modo" value="{{ k }}" {{ 'checked' if k == modo }}><span>{{ d }}</span></label>{% endfor %}
<p class="mutt">"Como o sistema" segue a definição do telemóvel ou computador (muitos mudam sozinhos ao anoitecer).
"Pela hora" usa a hora do dispositivo de quem está a ver.</p>
<button>Guardar</button></div></form>""",
                  aba="temas", paletas=PALETAS, modos=MODOS, tema=c.get_def("tema") or "padrao",
                  modo=c.get_def("tema_modo") or "sistema")


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
            c.set_def("recolha_supercharger", "1" if request.form.get("recolha_supercharger") else "0")
            for k in ("recolha_intervalo_conduzir", "recolha_intervalo_parado", "recolha_minutos_parado",
                      "recolha_intervalo_dormir", "recolha_limite_diario", "recolha_intervalo_carregar"):
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
        elif acao == "retencao":
            c.set_def("retencao_logs_dias", max(1, int(request.form.get("retencao_logs_dias") or 30)))
            c.set_def("retencao_auditoria_dias", max(1, int(request.form.get("retencao_auditoria_dias") or 180)))
            flash("Retenção guardada", "ok")
        elif acao == "erros":
            c.set_def("erros_detalhados", "1" if request.form.get("erros_detalhados") == "1" else "0")
            flash("Resposta de erro guardada", "ok")
        elif acao == "login_protecao":
            for k in ("login_max_tentativas", "login_janela_minutos", "login_bloqueio_minutos"):
                c.set_def(k, max(1, int(request.form.get(k) or 1)))
            flash("Proteção do login guardada", "ok")
        return redirect(request.referrer or url_for("definicoes"))
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
<label>A carregar (s)<input name="recolha_intervalo_carregar" type="number" min="60" value="{{ rec.recolha_intervalo_carregar }}"></label>
</div>
<p><label><input type="checkbox" name="recolha_supercharger" value="1" {{ 'checked' if rec.recolha_supercharger == '1' }}>
Importar uma vez por dia o histórico de Superchargers da Tesla (local, kWh e custo; requer o scope <code>vehicle_charging_cmds</code>)</label></p>
<div>
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

<p class="mutt">Logs, auditoria, utilizadores, backups, ajuda da API e temas estão nas outras abas.</p>""",
                  aba="geral",
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
                                                  "recolha_limite_diario", "recolha_intervalo_carregar",
                                                  "recolha_supercharger")},
                  ativa=c.get_def("status_api_ativa") == "1", pref=c.get_def("status_api_token_prefixo"))


def donut(titulo, itens, unidade="$", casas=3):
    """Gráfico circular (anel) em SVG com legenda; itens = [(nome, valor)], mais de 6 agrupa em "Outros"."""
    itens = sorted([(n, v) for n, v in itens if v > 0], key=lambda x: -x[1])
    if len(itens) > 6:
        itens = itens[:5] + [("Outros", sum(v for _, v in itens[5:]))]
    total = sum(v for _, v in itens)
    if not total:
        return Markup(f"<div><h3>{escape(titulo)}</h3><p class='mutt'>Sem consumo neste mês.</p></div>")
    gap = 0.8 if len(itens) > 1 else 0
    arcos, legenda, acum = [], [], 0.0
    for i, (n, v) in enumerate(itens):
        pct = 100 * v / total
        comp = max(pct - gap, 0.4)
        arcos.append(f"<circle class='f' r='15.915' cx='21' cy='21' fill='none' stroke='var(--c{i + 1})' stroke-width='6' "
                     f"stroke-dasharray='{comp:.3f} {100 - comp:.3f}' stroke-dashoffset='{25 - acum:.3f}'>"
                     f"<title>{escape(n)}: {v:.{casas}f} {unidade} ({pct:.1f} %)</title></circle>")
        legenda.append(f"<li><span class='s' style='background:var(--c{i + 1})'></span><span class='n'>{escape(n)}</span>"
                       f"<b>{v:.{casas}f} {unidade}</b><span class='p mutt'>{pct:.0f} %</span></li>")
        acum += pct
    svg = (f"<svg viewBox='0 0 42 42' role='img' aria-label='{escape(titulo)}'>"
           f"<circle r='15.915' cx='21' cy='21' fill='none' stroke='var(--sec)' stroke-width='6'/>{''.join(arcos)}"
           f"<text x='21' y='20.5' text-anchor='middle' font-size='5' font-weight='600' fill='var(--txt)'>{total:.2f}</text>"
           f"<text x='21' y='26' text-anchor='middle' font-size='3' fill='var(--mut)'>{unidade} total</text></svg>")
    return Markup(f"<div><h3>{escape(titulo)}</h3><div class='donut'>{svg}<ul>{''.join(legenda)}</ul></div></div>")


@app.get("/consumo")
@login_obrigatorio
def consumo():
    m = c.consumo_mes(request.args.get("mes"))
    nomes = {"dados": "Pedidos de dados", "wake": "Wakes", "comando": "Comandos"}
    por_cli = {}
    for x in m["por_cliente"]:
        por_cli[x["cliente"]] = por_cli.get(x["cliente"], 0) + x["custo"]
    graf_tipo = donut("Por tipo de pedido", [(nomes[k], v["custo"]) for k, v in m["categorias"].items()])
    graf_cli = donut("Por cliente", list(por_cli.items()))
    return render("Consumo", "consumo", """
<div class="card"><h2>Onde foram gastos os créditos — {{ m.mes }}</h2>
<div class="donuts">{{ graf_tipo }}{{ graf_cli }}</div>
<p class="mutt" style="margin-bottom:0">Passe o rato (ou toque) numa fatia para ver o valor. Os mesmos números estão nas tabelas abaixo.</p></div>
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
</div>""", m=m, nomes=nomes, graf_tipo=graf_tipo, graf_cli=graf_cli)


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
    if periodo not in omissao:
        periodo = "mes"
    formatos = {"semana": r"\d{4}-W\d{2}", "mes": r"\d{4}-\d{2}", "ano": r"\d{4}", "dias": r"\d{1,4}", "tudo": r".*"}
    valor = request.args.get("valor") or ""
    if not re.fullmatch(formatos[periodo], valor):
        valor = omissao[periodo]          # ex.: ao mudar de "mês" para "ano" o valor antigo não serve
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
            v["duracao_min"] = (round((datetime.fromisoformat(v["fim"]) - datetime.fromisoformat(v["inicio"])).total_seconds() / 60)
                                if v["fim"] and v.get("origem") != "inferida" else None)
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
<td>{{ v.duracao_min ~ ' min' if v.duracao_min is not none else ('a decorrer' if v.estado=='aberta' else ('entre leituras' if v.origem=='inferida' else '—')) }}</td>
<td>{{ v.km ~ ' km' if v.km is not none else '—' }}</td>
<td>{{ v.bateria_inicio if v.bateria_inicio is not none else '?' }} → {{ v.bateria_fim if v.bateria_fim is not none else '?' }} %</td>
<td>{{ v.velocidade_max_kmh or '—' }}</td><td>{{ v.pontos }}{% if v.origem == 'inferida' %} <span class="b mut" title="Aconteceu entre duas leituras (o carro estava a dormir): distância pelo odómetro, sem percurso">inferida</span>{% elif v.origem == 'tessie' %} <span class="b ok" title="Importada da Tessie">tessie</span>{% elif v.tessie_id %} <span class="b mut" title="completada com dados da Tessie">+tessie</span>{% endif %}</td>
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
{% if v.local_inicio or v.energia_kwh is not none or v.velocidade_media_kmh is not none %}
<table class="ft" style="margin-bottom:12px">
{% if v.local_inicio %}<tr><th>De</th><td>{{ v.local_inicio }}</td></tr><tr><th>Para</th><td>{{ v.local_fim or '—' }}</td></tr>{% endif %}
{% if v.energia_kwh is not none %}<tr><th>Energia</th><td>{{ '%.1f'|format(v.energia_kwh) }} kWh{% if v.km %} · {{ '%.0f'|format(v.energia_kwh * 1000 / v.km) }} Wh/km{% endif %}</td></tr>{% endif %}
{% if v.velocidade_media_kmh is not none %}<tr><th>V. média</th><td>{{ '%.0f'|format(v.velocidade_media_kmh) }} km/h</td></tr>{% endif %}
{% if v.temp_exterior is not none %}<tr><th>Temperatura</th><td>exterior {{ v.temp_exterior }} °C · interior {{ v.temp_interior if v.temp_interior is not none else '—' }} °C</td></tr>{% endif %}
{% if v.autopilot_km %}<tr><th>Autopilot</th><td>{{ v.autopilot_km }} km</td></tr>{% endif %}
<tr><th>Origem</th><td>{{ v.origem }}{{ ' + tessie' if v.tessie_id and v.origem != 'tessie' }}</td></tr></table>{% endif %}
<div id="mapa" class="mapa"></div>
<p><a href="{{ url_for('exportar_viagem', viagem_id=v.id, formato='gpx') }}"><button type="button" class="sec">GPX</button></a>
<a href="{{ url_for('exportar_viagem', viagem_id=v.id, formato='json') }}"><button type="button" class="sec">JSON</button></a>
<a href="{{ url_for('viagens') }}"><button type="button" class="sec">Voltar</button></a></p>
<p class="mutt">{{ 'Percurso importado da Tessie.' if v.origem == 'tessie' else 'Percurso aproximado: um ponto por leitura (intervalo "a conduzir" em Definições).' }}</p></div>
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


@app.route("/carregamentos", methods=["GET", "POST"])
@login_obrigatorio
def carregamentos():
    if request.method == "POST":
        import recolha
        r = recolha.importar_supercharger()
        msg = (f"Histórico de Superchargers: {r['recebidas']} sessões recebidas da Tesla — {r['novos']} novas, "
               f"{r['associados']} associadas a carregamentos já registados, {r['existentes']} já existiam"
               + (f", {r['ignoradas']} ignoradas (sem id, data ou VIN)" if r["ignoradas"] else ""))
        if r["erro"]:
            flash(f"Histórico de Superchargers: {r['erro']}", "err")
        else:
            flash(msg + ("" if r["recebidas"] else ". A Tesla não devolveu sessões (conta sem Superchargers pagos, "
                         "ou falta o scope vehicle_charging_cmds — ver Tesla → Scopes)."), "ok" if r["recebidas"] else "warn")
        return redirect(request.referrer or url_for("carregamentos"))
    f = ler_filtro()
    origem = request.args.get("origem") or None
    lista = c.listar_carregamentos(f["vin"], f["de"], f["ate"], 1000, origem)
    res = c.resumo_carregamentos(f["vin"], f["de"], f["ate"])
    for x in lista:
        try:
            x["duracao_min"] = (round((datetime.fromisoformat(x["fim"]) - datetime.fromisoformat(x["inicio"])).total_seconds() / 60)
                                if x["fim"] and x["origem"] != "inferido" else None)
            if x["duracao_min"] is not None and x["duracao_min"] < 0:
                x["duracao_min"] = None
        except (TypeError, ValueError):
            x["duracao_min"] = None
    filtro = FILTRO.replace("<button>Filtrar</button>", FILTRO_ORIGEM)
    return render("Carregamentos", "carregamentos", '<div class="card">' + filtro + CARREG_LISTA,
                  f=f, lista=lista, res=res, origem=origem)


FILTRO_ORIGEM = """<label>Origem<select name="origem"><option value="">todas</option>
{% for k, n in [('leituras','Leituras'),('inferido','Inferido'),('supercharger','Supercharger'),('tessie','Tessie')] %}<option value="{{ k }}" {{ 'selected' if origem==k }}>{{ n }}</option>{% endfor %}</select></label>
<button>Filtrar</button>"""

CARREG_LISTA = """
<div class="grid" style="margin:12px 0">
 <div class="kpi"><small>Carregamentos</small><div>{{ res.n }}</div></div>
 <div class="kpi"><small>Energia</small><div>{{ res.kwh }} kWh</div></div>
 <div class="kpi"><small>Custo (Superchargers)</small><div>{{ '%.2f'|format(res.custo) }} {{ res.moeda or '' }}</div></div>
 <div class="kpi"><small>Rápidos (DC)</small><div>{{ res.dc }}</div></div>
</div>
<table><tr><th>Início</th><th>Duração</th><th>Tipo</th><th>Local</th><th>Bateria</th><th>Energia</th><th>Potência máx.</th><th>Custo</th><th>Origem</th><th></th></tr>
{% for x in lista %}<tr><td>{{ x.inicio[:16].replace('T',' ') }}</td>
<td>{{ x.duracao_min ~ ' min' if x.duracao_min is not none else ('a carregar' if x.estado=='a_carregar' else ('entre leituras' if x.origem=='inferido' else '—')) }}</td>
<td>{{ x.tipo or '—' }}</td><td>{{ x.local or ('%.4f, %.4f'|format(x.lat, x.lon) if x.lat is not none else '—') }}</td>
<td>{{ x.bateria_inicio if x.bateria_inicio is not none else '?' }} → {{ x.bateria_fim if x.bateria_fim is not none else '?' }} %</td>
<td>{{ ('%.1f kWh'|format(x.energia_kwh)) if x.energia_kwh is not none else '—' }}</td>
<td>{{ ('%.0f kW'|format(x.potencia_max_kw)) if x.potencia_max_kw else '—' }}</td>
<td>{{ ('%.2f %s'|format(x.custo, x.moeda or '')) if x.custo is not none else '—' }}</td>
<td><span class="b {{ 'ok' if x.origem in ('supercharger','tessie') else 'mut' }}">{{ x.origem }}</span>{% if x.tessie_id and x.origem != 'tessie' %} <span class="b mut" title="completado com dados da Tessie">+tessie</span>{% endif %}</td>
<td><a href="{{ url_for('carregamento', cid=x.id) }}"><button type="button" class="sec">Ver</button></a></td></tr>
{% else %}<tr><td colspan="10" class="mutt">Sem carregamentos neste período.</td></tr>{% endfor %}</table></div>
<div class="card"><h2>Como são registados</h2>
<table class="ft"><tr><th>leituras</th><td>Detetado pela recolha (estado "a carregar"): início, fim, bateria, energia, potência e curva.</td></tr>
<tr><th>inferido</th><td>A bateria subiu 3 % ou mais entre duas leituras sem se ver o carregamento (carro a dormir): sem curva.</td></tr>
<tr><th>supercharger</th><td>Importado do histórico de Superchargers da Tesla (local, kWh, custo); se coincidir com um carregamento já registado, junta-se a ele.</td></tr>
<tr><th>tessie</th><td>Importado do histórico da Tessie (Definições → Dados); os que coincidem com um registo do gateway completam-no.</td></tr></table>
<form method="post" style="margin-top:10px"><input type="hidden" name="csrf" value="{{ csrf() }}"><button class="sec">Importar agora o histórico de Superchargers</button></form></div>"""

CARREG_DETALHE = """
<div class="card"><h2>Carregamento {{ x.id }} — {{ x.inicio[:16].replace('T',' ') }}</h2>
<div class="grid" style="margin-bottom:12px">
 <div class="kpi"><small>Bateria</small><div>{{ x.bateria_inicio if x.bateria_inicio is not none else '?' }} → {{ x.bateria_fim if x.bateria_fim is not none else '?' }} %</div></div>
 <div class="kpi"><small>Energia</small><div>{{ ('%.1f kWh'|format(x.energia_kwh)) if x.energia_kwh is not none else '—' }}</div></div>
 <div class="kpi"><small>Potência máx.</small><div>{{ ('%.0f kW'|format(x.potencia_max_kw)) if x.potencia_max_kw else '—' }}</div></div>
 <div class="kpi"><small>Custo</small><div>{{ ('%.2f %s'|format(x.custo, x.moeda or '')) if x.custo is not none else '—' }}</div></div>
</div>
<table class="ft"><tr><th>Fim</th><td>{{ (x.fim or 'a carregar')[:16].replace('T',' ') }}</td></tr>
<tr><th>Tipo</th><td>{{ x.tipo or '—' }}</td></tr><tr><th>Local</th><td>{{ x.local or '—' }}</td></tr>
<tr><th>Origem</th><td>{{ x.origem }}</td></tr></table></div>
{% if x.curva %}<div class="card"><h2>Bateria (%)</h2>{{ graf_bat }}</div>
<div class="card"><h2>Potência (kW)</h2>{{ graf_kw }}</div>{% endif %}
{% if x.lat is not none %}<div class="card"><h2>Local</h2><div id="mapa" class="mapa" style="height:300px"></div></div>
<script>const m=L.map('mapa').setView([{{ x.lat }}, {{ x.lon }}], 15);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; OpenStreetMap'}).addTo(m);
L.circleMarker([{{ x.lat }}, {{ x.lon }}],{radius:8,color:'#1f8a4c'}).addTo(m);</script>{% endif %}
<p><a href="{{ url_for('carregamentos') }}"><button type="button" class="sec">Voltar</button></a></p>"""


@app.get("/carregamentos/<int:cid>")
@login_obrigatorio
def carregamento(cid):
    x = c.obter_carregamento(cid)
    graf_bat = linha_svg([p["bateria"] for p in x["curva"]], "%", 0, 100)
    graf_kw = linha_svg([p["potencia_kw"] for p in x["curva"]], "kW", 0, None)
    return render(f"Carregamento {cid}", "carregamentos", LEAFLET + CARREG_DETALHE, x=x, graf_bat=graf_bat, graf_kw=graf_kw)


def linha_svg(valores, unidade, ymin=0, ymax=None, rotulos=("início", "fim"), dicas=None):
    """Gráfico de linha simples (um eixo) em SVG. dicas: texto por ponto (tooltip)."""
    vals = [v for v in valores if v is not None]
    if ymin is None and vals:
        ymin = min(vals) * 0.97
    if len(vals) < 2:
        return Markup("<p class='mutt'>Poucas leituras para desenhar a curva.</p>")
    ymax = ymax if ymax is not None else (max(vals) * 1.15 or 1)
    w, h, pe, pb = 600, 160, 34, 18
    idx = [i for i, v in enumerate(valores) if v is not None]
    n = len(valores)
    pts = [(pe + i * (w - pe - 6) / (n - 1), h - pb - (valores[i] - ymin) / (ymax - ymin) * (h - pb - 8)) for i in idx]
    linha = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    grelha = "".join(
        f"<line x1='{pe}' x2='{w - 6}' y1='{h - pb - k * (h - pb - 8) / 4:.1f}' y2='{h - pb - k * (h - pb - 8) / 4:.1f}' "
        f"stroke='var(--bd)' stroke-width='1'/><text x='{pe - 6}' y='{h - pb - k * (h - pb - 8) / 4 + 4:.1f}' "
        f"text-anchor='end' font-size='10' fill='var(--mut)'>{ymin + k * (ymax - ymin) / 4:.0f}</text>" for k in range(5))
    textos = [dicas[i] for i in idx] if dicas else [""] * len(idx)
    r = 4 if len(pts) <= 60 else 2.5
    marcas = "".join(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='{r}' fill='var(--c1)' stroke='var(--card)' stroke-width='1.5'>"
                     f"<title>{escape(t)} {v} {unidade}</title></circle>" for (x, y), v, t in zip(pts, vals, textos))
    return Markup(f"<svg viewBox='0 0 {w} {h}' style='width:100%;height:auto' role='img' aria-label='curva em {unidade}'>"
                  f"{grelha}<polyline points='{linha}' fill='none' stroke='var(--c1)' stroke-width='2' stroke-linejoin='round'/>"
                  f"{marcas}<text x='{pe}' y='{h - 3}' font-size='10' fill='var(--mut)'>{escape(rotulos[0])}</text>"
                  f"<text x='{w - 6}' y='{h - 3}' text-anchor='end' font-size='10' fill='var(--mut)'>{escape(rotulos[1])}</text></svg>")


@app.get("/bateria")
@login_obrigatorio
def bateria():
    f = ler_filtro()
    sb = c.saude_bateria(f["vin"], f["de"], f["ate"])
    pg = c.listar_paragens(f["vin"], f["de"], f["ate"], limite=100)
    med = sb["medicoes"]
    # para o gráfico: Tessie quando existe (mais precisa), senão as leituras do gateway
    serie = [m for m in med if m["origem"] == "tessie"] or med
    graf_aut = linha_svg([m["autonomia_max_km"] for m in serie], "km", None, None,
                         (serie[0]["ts"][:10], serie[-1]["ts"][:10]) if serie else ("", ""),
                         [m["ts"][:10] for m in serie])
    cap = [m for m in med if m["capacidade_kwh"]]
    graf_cap = linha_svg([m["capacidade_kwh"] for m in cap], "kWh", None, None,
                         (cap[0]["ts"][:10], cap[-1]["ts"][:10]) if cap else ("", ""), [m["ts"][:10] for m in cap]) if cap else None
    return render("Bateria", "bateria", """
<div class="card">""" + FILTRO + """
<div class="grid" style="margin:12px 0">
 <div class="kpi"><small>Autonomia a 100 %</small><div>{{ ('%.0f km'|format(r.autonomia_max_km)) if r.autonomia_max_km else '—' }}</div></div>
 <div class="kpi"><small>Capacidade</small><div>{{ ('%.1f kWh'|format(r.capacidade_kwh)) if r.capacidade_kwh else '—' }}</div></div>
 <div class="kpi"><small>Degradação (autonomia)</small><div>{{ ('%.1f %%'|format(r.degradacao_autonomia_pct)) if r.degradacao_autonomia_pct is defined and r.degradacao_autonomia_pct is not none else '—' }}</div></div>
 <div class="kpi"><small>Medições</small><div>{{ r.n }}</div></div>
</div></div>
<div class="card"><h2>Autonomia estimada a 100 % (km)</h2>{{ graf_aut }}
<p class="mutt">Tessie: medição da Tessie. Gateway: 1×/dia, autonomia atual ÷ bateria × 100 (com bateria ≥ 50 %); varia com a temperatura.</p></div>
{% if graf_cap %}<div class="card"><h2>Capacidade (kWh)</h2>{{ graf_cap }}</div>{% endif %}
<div class="card"><h2>Perdas com o carro parado</h2>
<div class="grid" style="margin-bottom:12px">
 <div class="kpi"><small>Paragens</small><div>{{ pg.resumo.n }}</div></div>
 <div class="kpi"><small>Energia perdida</small><div>{{ pg.resumo.kwh }} kWh</div></div>
 <div class="kpi"><small>Bateria perdida</small><div>{{ pg.resumo.bateria }} %</div></div>
</div>
<table><tr><th>Início</th><th>Fim</th><th>Local</th><th>Bateria</th><th>Energia</th><th>Clima</th><th>Sentinela</th></tr>
{% for p in pg.paragens %}<tr><td>{{ p.inicio[:16].replace('T',' ') }}</td><td>{{ (p.fim or '—')[:16].replace('T',' ') }}</td>
<td>{{ p.local or '—' }}</td><td>{{ p.bateria_inicio }} → {{ p.bateria_fim }} %</td>
<td>{{ ('%.2f kWh'|format(p.energia_kwh)) if p.energia_kwh is not none else '—' }}</td>
<td>{{ ('%.0f %%'|format(p.clima_pct)) if p.clima_pct is not none else '—' }}</td><td>{{ ('%.0f %%'|format(p.sentinela_pct)) if p.sentinela_pct is not none else '—' }}</td></tr>
{% else %}<tr><td colspan="7" class="mutt">Sem paragens registadas (vêm da importação da Tessie).</td></tr>{% endfor %}</table>
<p class="mutt">Últimas 100 do período. Clima e sentinela: % do tempo parado com cada um ligado.</p></div>
<div class="card"><h2>Medições</h2><table><tr><th>Data</th><th>Odómetro</th><th>Autonomia a 100 %</th><th>Ideal</th><th>Capacidade</th><th>Origem</th></tr>
{% for m in med|reverse %}{% if loop.index <= 60 %}<tr><td>{{ m.ts[:10] }}</td><td>{{ ('%.0f km'|format(m.odo_km)) if m.odo_km else '—' }}</td>
<td>{{ ('%.0f km'|format(m.autonomia_max_km)) if m.autonomia_max_km else '—' }}</td><td>{{ ('%.0f km'|format(m.autonomia_ideal_km)) if m.autonomia_ideal_km else '—' }}</td>
<td>{{ ('%.1f kWh'|format(m.capacidade_kwh)) if m.capacidade_kwh else '—' }}</td><td><span class="b mut">{{ m.origem }}</span></td></tr>{% endif %}
{% else %}<tr><td colspan="6" class="mutt">Sem medições neste período.</td></tr>{% endfor %}</table></div>""",
                  f=f, r=sb["resumo"], med=med, pg=pg, graf_aut=graf_aut, graf_cap=graf_cap)


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
        elif acao == "tessie":
            import tessie
            token = (request.form.get("tessie_token") or "").strip()
            partes = [p for p in tessie.PARTES if request.form.get(f"t_{p}")]
            if not token or not partes:
                flash("Indique o token da Tessie e o que importar", "err")
            else:
                try:
                    tessie.importar_em_fundo(token, partes, bool(request.form.get("t_percursos")))
                    flash("Importação da Tessie iniciada (é feito um backup antes). Acompanhe o estado abaixo.", "ok")
                except c.ErroGateway as e:
                    flash(e.mensagem, "err")
        elif acao == "config":
            c.set_def("backup_automatico", "1" if request.form.get("backup_automatico") else "0")
            c.set_def("backup_retencao", max(1, int(request.form.get("backup_retencao") or 14)))
            flash("Definições de backup guardadas", "ok")
        return redirect(url_for("dados"))
    with c.db() as con:
        cont = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("viagens", "pontos", "carregamentos", "bateria_saude", "paragens", "consumo", "logs", "clientes")}
    tam = round(os.path.getsize(c.DB_FILE) / 1024, 1) if os.path.exists(c.DB_FILE) else 0
    return render("Dados", "definicoes", """
<div class="card"><h2>Base de dados</h2>
<div class="grid">{% for k, n in cont.items() %}<div class="kpi"><small>{{ k }}</small><div>{{ n }}</div></div>{% endfor %}
<div class="kpi"><small>Tamanho</small><div>{{ tam }} KB</div></div></div></div>

<div class="card"><h2>Exportar</h2>
<form method="get" action="{{ url_for('exportar') }}" class="row">
<label>Dados<select name="tipo"><option value="viagens">Viagens</option><option value="carregamentos">Carregamentos</option><option value="pontos">Posições</option>
<option value="bateria">Saúde da bateria</option><option value="paragens">Paragens</option>
<option value="consumo">Consumo</option><option value="logs">Logs</option></select></label>
<label>Formato<select name="formato"><option value="csv">CSV (Excel)</option><option value="json">JSON</option>
<option value="gpx">GPX (viagens)</option><option value="geojson">GeoJSON (posições)</option></select></label>
<label>Período<select name="periodo"><option value="tudo">Tudo</option><option value="ano">Este ano</option>
<option value="mes">Este mês</option><option value="semana">Esta semana</option></select></label>
<button>Exportar</button></form>
<p class="mutt">CSV com separador <code>;</code>. GPX abre no Google Earth, Strava, etc.; GeoJSON em QGIS/geojson.io.</p></div>

<div class="card"><h2>Importar da Tessie</h2>
<p>Traz o histórico guardado na Tessie para o gateway (antes de cancelar a subscrição): viagens com percurso, carregamentos,
saúde da bateria e paragens. Os registos que já existem no gateway são completados, não duplicados; as viagens e
carregamentos "inferidos" cobertos pela Tessie são substituídos. Pode repetir: o que já foi importado é ignorado.</p>
{% if ts.fase %}<table class="ft" style="margin-bottom:10px"><tr><th>Estado</th><td>{% if ts.a_decorrer %}<span class="b warn">a decorrer</span>{% elif ts.erro %}<span class="b err">falhou</span>{% else %}<span class="b ok">concluída</span>{% endif %}
{{ ts.fase }} <span class="mutt">({{ (ts.atualizado or '')[:19].replace('T',' ') }})</span>{% if ts.a_decorrer %} · <a href="{{ url_for('dados') }}">atualizar</a>{% endif %}</td></tr>
{% if ts.erro %}<tr><th>Erro</th><td>{{ ts.erro }}</td></tr>{% endif %}
<tr><th>Viagens</th><td>{{ ts.viagens_recebidas or 0 }} na Tessie: {{ ts.viagens_novas or 0 }} novas, {{ ts.viagens_associadas or 0 }} completadas, {{ ts.viagens_existentes or 0 }} já importadas{% if ts.inferidas_substituidas %}, {{ ts.inferidas_substituidas }} inferidas substituídas{% endif %}</td></tr>
<tr><th>Carregamentos</th><td>{{ ts.carregamentos_recebidos or 0 }} na Tessie: {{ ts.carregamentos_novos or 0 }} novos, {{ ts.carregamentos_associados or 0 }} completados, {{ ts.carregamentos_existentes or 0 }} já importados</td></tr>
<tr><th>Bateria / paragens</th><td>{{ ts.bateria_medicoes or 0 }} medições · {{ ts.paragens or 0 }} paragens{% if ts.pedidos %} · {{ ts.pedidos }} pedidos à Tessie em {{ ts.minutos }} min{% endif %}</td></tr>
{% if ts.avisos %}<tr><th>Avisos</th><td>{{ ts.avisos|length }} (ver Logs, origem tessie)</td></tr>{% endif %}</table>{% endif %}
{% if session.get('perfil') == 'admin' and not ts.a_decorrer %}
<form method="post" autocomplete="off"><input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="tessie">
<label>Token da Tessie (dash.tessie.com → Settings → API → Generate Access Token)<input name="tessie_token" type="password" required></label>
<p class="row">{% for k, n in [('viagens','Viagens'),('carregamentos','Carregamentos'),('bateria','Saúde da bateria'),('paragens','Paragens')] %}
<label style="flex-direction:row;align-items:center"><input type="checkbox" name="t_{{ k }}" value="1" checked> {{ n }}</label>{% endfor %}
<label style="flex-direction:row;align-items:center"><input type="checkbox" name="t_percursos" value="1" checked> Percursos das viagens (1 pedido por viagem, mais lento)</label></p>
<button>Importar da Tessie</button></form>
<p class="mutt">O token só é usado nesta importação: não é guardado nem fica nos logs. Pode demorar vários minutos com muitas viagens.</p>{% endif %}</div>

<div class="card"><h2>Backups</h2>
<form method="post" class="row"><input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="config">
<label style="flex-direction:row;align-items:center"><input type="checkbox" name="backup_automatico" value="1" {{ 'checked' if auto == '1' }}> Backup automático diário</label>
<label>Guardar (nº de backups automáticos)<input name="backup_retencao" type="number" min="1" value="{{ ret }}"></label>
<button>Guardar</button></form>
<form method="post"><input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="backup"><button>Criar backup agora</button></form>
<table style="margin-top:10px"><tr><th>Ficheiro</th><th>Data (UTC)</th><th>Tamanho</th><th></th></tr>
{% for b in backups %}<tr><td><code>{{ b.nome }}</code></td><td>{{ b.data[:19].replace('T',' ') }}</td><td>{{ b.tamanho_kb }} KB</td>
<td style="white-space:nowrap">{% if session.get('perfil') == 'admin' %}<a href="{{ url_for('descarregar_backup', nome=b.nome) }}"><button type="button" class="sec">Descarregar</button></a>{% endif %}
<form class="in" method="post" onsubmit="return confirm('Restaurar {{ b.nome }}? Os dados atuais são substituídos (é feito um backup antes).')">
<input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="restaurar"><input type="hidden" name="nome" value="{{ b.nome }}"><button class="sec">Restaurar</button></form>
<form class="in" method="post" onsubmit="return confirm('Apagar {{ b.nome }}?')"><input type="hidden" name="csrf" value="{{ csrf() }}">
<input type="hidden" name="acao" value="apagar"><input type="hidden" name="nome" value="{{ b.nome }}"><button class="del">Apagar</button></form></td></tr>
{% else %}<tr><td colspan="4" class="mutt">Sem backups.</td></tr>{% endfor %}</table>
<p class="mutt">Ficam em <code>tesla/backups/</code> no NAS (volume). Incluem clientes, chaves (em hash), login, viagens, consumo e logs;
o <code>tesla.env</code>, as chaves Tesla e os tokens estão em <code>tesla/</code> e não fazem parte do backup.</p></div>""",
                  aba="dados", cont=cont, tam=tam, backups=c.listar_backups(), auto=c.get_def("backup_automatico"),
                  ret=c.get_def("backup_retencao"), ts=__import__("tessie").estado())


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
    eps = c.endpoints_json()
    sel = request.args.get("ep", "inicio")
    subs = [("inicio", "Como usar")] + [(str(i), f"{e['metodo']} {e['caminho'].replace('/api/vehicles/{vin}', '…').replace('/api/', '/')}")
                                       for i, e in enumerate(eps)] + [("status", "Status API"), ("codigos", "Códigos")]
    barra = Markup("<div class='sub'>" + "".join(
        f"<a href='{url_for('api_ajuda', ep=k)}' class='{'on' if k == sel else ''}'>{escape(n)}</a>" for k, n in subs) + "</div>")
    e = eps[int(sel)] if sel.isdigit() and int(sel) < len(eps) else None
    return render("API", "definicoes", """{{ barra }}
{% if sel == 'inicio' %}
<div class="card"><h2>Como usar a API</h2>
<table class="ft"><tr><th>URL base (mesma rede Docker)</th><td><code>http://tesla-key:8000</code></td></tr>
<tr><th>Autenticação</th><td>Header <code>X-API-Key: tk_...</code> (chave do cliente, criada em Clientes)</td></tr>
<tr><th>Permissões</th><td>Cada endpoint exige uma permissão do cliente (Clientes → Permissões); sem ela a resposta é 403</td></tr>
<tr><th>Formato</th><td>JSON; erros em <code>{"erro": "..."}</code></td></tr>
<tr><th>Cache</th><td>Respostas repetidas vêm da cache (tempos em Definições → Geral); <code>?fresh=1</code> força um pedido novo à Tesla</td></tr>
<tr><th>Referência em JSON</th><td><code>GET /api/endpoints</code> (sem chave), útil para gerar clientes</td></tr></table>
<pre>curl -H "X-API-Key: tk_..." http://tesla-key:8000/api/vehicles</pre>
<h3>Endpoints</h3><table><tr><th>Método</th><th>Caminho</th><th>Permissão</th><th>Para quê</th></tr>
{% for x in eps %}<tr><td><span class="m {{ x.metodo }}">{{ x.metodo }}</span></td><td><a href="?ep={{ loop.index0 }}"><code>{{ x.caminho }}</code></a></td>
<td>{{ x.permissao or '—' }}</td><td>{{ x.descricao }}</td></tr>{% endfor %}</table></div>
{% elif e %}
<div class="card"><h2><span class="m {{ e.metodo }}">{{ e.metodo }}</span> <code>{{ e.caminho }}</code></h2>
<p>{{ e.descricao }}</p>
<table class="ft"><tr><th>Permissão</th><td>{% if e.permissao %}{% for p in e.permissao.split(', ') %}<span class="b mut">{{ p }}</span> {% endfor %}{% else %}<span class="mutt">nenhuma</span>{% endif %}</td></tr>
<tr><th>Parâmetros</th><td>{% if e.parametros %}<code>{{ e.parametros }}</code>{% else %}<span class="mutt">nenhum</span>{% endif %}</td></tr>
<tr><th>Chave</th><td>{{ 'Header X-API-Key obrigatório' if e.permissao or e.caminho == '/api/me' else 'Não precisa' }}</td></tr></table>
<h3>Pedido</h3>
<pre>curl {{ '-X POST ' if e.metodo == 'POST' }}{{ '-H "X-API-Key: tk_..." ' if e.permissao or e.caminho == '/api/me' }}"http://tesla-key:8000{{ e.caminho|replace('{vin}', '<VIN>')|replace('{id}', '1')|replace('{caminho}', 'dx/charging/history') }}"</pre>
<pre>import requests
r = requests.{{ e.metodo|lower }}("http://tesla-key:8000{{ e.caminho|replace('{vin}', '" + VIN + "')|replace('{id}', '1')|replace('{caminho}', 'dx/charging/history') }}",
{{ '                 headers={"X-API-Key": CHAVE}, ' if e.permissao or e.caminho == '/api/me' else '                 ' }}timeout=30)
print(r.status_code, r.json())</pre>
<h3>Exemplo de resposta</h3><pre>{{ e.exemplo }}</pre>
<p class="mutt">Detalhe completo dos campos no <code>API.md</code> e no <code>openapi.yaml</code> do projeto.</p></div>
{% elif sel == 'status' %}
<div class="card"><h2>Status API (para outro portal)</h2>
<p>Estado: {% if ativa %}<span class="b ok">ativa</span>{% else %}<span class="b mut">desativada</span>{% endif %}
{% if pref %} · token <code>{{ pref }}…</code>{% endif %}</p>
<p>Dá a outro portal o estado do gateway (sem acesso aos dados do carro), com o header
<code>Authorization: Bearer &lt;token&gt;</code>. Desativada responde 404.</p>
<table><tr><th>Endpoint</th><th>Conteúdo</th></tr>
<tr><td><code>/status/v1/health</code></td><td>Gateway a responder</td></tr>
<tr><td><code>/status/v1/resumo</code></td><td>Configuração, token, clientes, contagens 24 h, consumo do mês</td></tr>
<tr><td><code>/status/v1/clientes</code></td><td>Clientes com estado, último uso, pedidos e permissões</td></tr>
<tr><td><code>/status/v1/logs?nivel=ERROR&amp;limite=50</code></td><td>Últimos registos (máx. 500)</td></tr></table>
<pre>curl -H "Authorization: Bearer ts_..." http://tesla-key:8000/status/v1/resumo</pre>
{% for a,l,cl in ([('status_off','Desativar','sec')] if ativa else [('status_on','Ativar','')]) + [('status_token','Gerar novo token','sec')] %}
<form class="in" method="post" action="{{ url_for('definicoes') }}" {% if a=='status_token' %}onsubmit="return confirm('O token atual deixa de funcionar. Continuar?')"{% endif %}>
<input type="hidden" name="csrf" value="{{ csrf() }}"><input type="hidden" name="acao" value="{{ a }}"><button class="{{ cl }}">{{ l }}</button></form>{% endfor %}
</div>
{% else %}
<div class="card"><h2>Códigos de resposta</h2>
<p>Em caso de erro o cliente recebe <code>{"codigo": 403, "erro": "Forbidden", "ref": 1234}</code> (e, se ativo, <code>"detalhe"</code>).
O motivo de cada <code>ref</code> está em Definições → Logs → API.</p><table>
<tr><th>Código</th><th>Significado</th><th>O que fazer</th></tr>
<tr><td>200</td><td>OK</td><td>—</td></tr>
<tr><td>400</td><td>Parâmetro inválido</td><td>Rever os parâmetros</td></tr>
<tr><td>401</td><td>Chave em falta, errada ou cliente desativado</td><td>Verificar a chave; não repetir em ciclo</td></tr>
<tr><td>403</td><td>Sem permissão, funcionalidade desativada, ou scope em falta na Tesla</td><td>Ver <code>GET /api/me</code></td></tr>
<tr><td>404</td><td>Caminho ou recurso inexistente</td><td>Rever o URL</td></tr>
<tr><td>408</td><td>Carro a dormir ou offline</td><td>Mostrar o último valor; tentar mais tarde</td></tr>
<tr><td>429</td><td>Limite de pedidos da Tesla</td><td>Reduzir a frequência</td></tr>
<tr><td>502 / 503</td><td>Falha na Tesla / gateway sem tokens</td><td>Ver os logs; refazer o login Tesla</td></tr>
</table></div>
{% endif %}""", aba="api", barra=barra, sel=sel, e=e, eps=eps,
                  ativa=c.get_def("status_api_ativa") == "1", pref=c.get_def("status_api_token_prefixo"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001)
