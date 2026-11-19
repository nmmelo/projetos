"""Modo de manutencao.

Ativo: so quem gere o portal (administrador local e perfis com "config.portal") entra e navega;
os outros utilizadores veem a mensagem HTML configurada (HTTP 503). O estado fica na tabela
settings, por isso vale para todos os workers e entra nos backups.

Pagina: Configuracao > Portal > Manutencao.  Linha de comandos: manage.py maintenance on|off|status
"""
import html
import re
from html.parser import HTMLParser

from . import store

DEFAULT_HTML = """<h2>Portal em manutenção</h2>
<p>Estamos a fazer atualizações. O acesso normal volta dentro de momentos.</p>
<p><small>Durante a manutenção só os administradores do portal podem entrar.</small></p>"""

KEYS = ("maintenance_mode", "maintenance_html", "maintenance_since", "maintenance_by")
MAX_HTML = 20000

# Etiquetas e atributos permitidos na mensagem (o resto e removido; o texto fica).
TAGS = {"h1", "h2", "h3", "h4", "p", "br", "hr", "b", "strong", "i", "em", "u", "s", "small", "mark",
        "ul", "ol", "li", "a", "div", "span", "blockquote", "code", "pre", "img", "table", "thead",
        "tbody", "tr", "th", "td"}
VOID = {"br", "hr", "img"}
ATTRS = {"*": {"class", "style", "title", "align"}, "a": {"href", "target"}, "img": {"src", "alt", "width", "height"},
         "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan"}}
DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "noscript", "template", "svg", "math", "form"}
URL_OK = re.compile(r"^(https?:|mailto:|tel:|/|#)", re.I)
IMG_OK = re.compile(r"^(https?:|/|data:image/(png|gif|jpe?g|webp);base64,)", re.I)
STYLE_BAD = re.compile(r"url\s*\(|expression|javascript:|@import|behavior", re.I)


class _Limpa(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in TAGS:
            return
        ok = []
        for k, v in attrs:
            k = k.lower()
            v = v or ""
            if k not in ATTRS["*"] and k not in ATTRS.get(tag, ()):
                continue
            if k == "href" and not URL_OK.match(v.strip()):
                continue
            if k == "src" and not IMG_OK.match(v.strip()):
                continue
            if k == "style" and STYLE_BAD.search(v):
                continue
            if k == "target" and v not in ("_blank", "_self"):
                continue
            ok.append(f' {k}="{html.escape(v, quote=True)}"')
        if tag == "img" and not any(a.startswith(" src=") for a in ok):
            return
        if tag == "a" and any(a.startswith(' target="_blank"') for a in ok):
            ok.append(' rel="noopener noreferrer"')
        self.out.append(f"<{tag}{''.join(ok)}>")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in self.stack and tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in DROP_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in TAGS or tag in VOID or tag not in self.stack:
            return
        while self.stack:                       # fecha tambem as que ficaram abertas por dentro
            t = self.stack.pop()
            self.out.append(f"</{t}>")
            if t == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        self.close()
        return "".join(self.out) + "".join(f"</{t}>" for t in reversed(self.stack))


def limpar_html(texto: str) -> str:
    """HTML seguro para mostrar: so etiquetas/atributos permitidos, sem scripts nem eventos."""
    p = _Limpa()
    p.feed((texto or "")[:MAX_HTML])
    return p.result().strip()


def estado() -> dict:
    s = store.get_settings()
    return {"ativo": s.get("maintenance_mode") == "1",
            "html": s.get("maintenance_html") or DEFAULT_HTML,
            "desde": s.get("maintenance_since") or "", "por": s.get("maintenance_by") or ""}


def ativa() -> bool:
    try:
        return store.get_setting("maintenance_mode") == "1"
    except Exception:  # noqa: BLE001 - BD do portal indisponivel
        return False


def definir(ativo: bool, por: str, html_msg: str | None = None) -> None:
    valores = {"maintenance_mode": "1" if ativo else "0"}
    if ativo and not ativa():
        valores.update(maintenance_since=store.now(), maintenance_by=por)
    if html_msg is not None:
        valores["maintenance_html"] = limpar_html(html_msg) or DEFAULT_HTML
    store.save_settings(valores)
