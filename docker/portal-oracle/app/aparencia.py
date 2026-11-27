"""Aparencia do portal: titulo, texto do login, rodape, favicon e tema.

Tudo fica na tabela 'settings' (por isso entra nos backups e e igual em todos os workers):
  portal_name, login_subtitle, footer, theme, login_layout, show_icon, favicon_data (base64), favicon_type, favicon_ver
"""
import base64
import hashlib
import re
from datetime import date

# Os temas sao definidos no style.css (html[data-tema="..."]). As cores aqui so servem para a
# pre-visualizacao na pagina de configuracao: cabecalho, fundo, cartao, destaque, texto.
THEMES = {
    "claro":  {"label": "Claro", "desc": "Azul sobre fundo claro (por omissão)",
               "preview": ("#17202f", "#f5f6f8", "#ffffff", "#1f5fbf", "#1d2433")},
    "escuro": {"label": "Escuro", "desc": "Fundo escuro, cansa menos a vista em salas de operação",
               "preview": ("#0a0e14", "#0f141c", "#171e29", "#5b9cf5", "#e3e8f0")},
    "oracle": {"label": "Oracle", "desc": "Vermelho e grafite",
               "preview": ("#312d2a", "#f6f4f2", "#ffffff", "#c74634", "#2b2622")},
}
DEFAULT_THEME = "claro"

# Pagina de login: classico (cartao com utilizador e password), vidro (cartao translucido sobre um fundo
# com cor) e boas_vindas (caixa com o titulo e um botao "Entrar" que abre o formulario)
LOGIN_LAYOUTS = {
    "classico": {"label": "Clássico", "desc": "Cartão com utilizador e password"},
    "vidro": {"label": "Vidro", "desc": "Cartão translúcido sobre um fundo com as cores do tema"},
    "boas_vindas": {"label": "Boas-vindas", "desc": "Caixa com o título e o botão Entrar, que abre o login"},
}

DEFAULTS = {
    "portal_name": "Oracle · Gestão de espaço",
    "login_subtitle": "",
    "footer": "",
    "theme": DEFAULT_THEME,
    "login_layout": "classico",
    "show_icon": "1",
    "favicon_data": "",
    "favicon_type": "",
    "favicon_ver": "",
}
BLOB_KEYS = ("favicon_data",)          # nao e lido em cada pagina, so na rota do favicon

LIMITS = {"portal_name": 80, "login_subtitle": 120, "footer": 200}
FAVICON_MAX = 32 * 1024                # 32 KB (em base64 cabe num TEXT do MariaDB)
FAVICON_TYPES = {"png": "image/png", "ico": "image/x-icon", "svg": "image/svg+xml"}


def clean_text(key: str, value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value[:LIMITS[key]]


def favicon_kind(data: bytes) -> str:
    """Identifica o tipo pelo conteudo (nao pela extensao). Erro se nao for PNG, ICO ou SVG."""
    if not data:
        raise ValueError("O ficheiro está vazio.")
    if len(data) > FAVICON_MAX:
        raise ValueError(f"O ícone tem mais de {FAVICON_MAX // 1024} KB, que é o máximo.")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "ico"
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = ""
    if re.search(r"<svg[\s>]", text[:2048], re.I):
        if re.search(r"<script|<foreignObject|\son\w+\s*=|javascript:", text, re.I):
            raise ValueError("O SVG tem scripts ou eventos, que não são permitidos.")
        return "svg"
    raise ValueError("Formato não suportado: use PNG, ICO ou SVG.")


def favicon_settings(data: bytes) -> dict:
    kind = favicon_kind(data)
    return {"favicon_data": base64.b64encode(data).decode(), "favicon_type": FAVICON_TYPES[kind],
            "favicon_ver": hashlib.sha256(data).hexdigest()[:10]}


def accent(theme: str) -> str:
    """Cor de destaque do tema (usada no icone por omissao)."""
    return THEMES.get(theme, THEMES[DEFAULT_THEME])["preview"][3]


def context(s: dict) -> dict:
    """Variaveis para os templates (base.html, login.html)."""
    theme = s.get("theme") if s.get("theme") in THEMES else DEFAULT_THEME
    footer = (s.get("footer") or "").replace("{ano}", str(date.today().year))
    return {"portal_name": s.get("portal_name") or DEFAULTS["portal_name"],
            "login_subtitle": s.get("login_subtitle") or "",
            "footer": footer, "theme": theme,
            "login_layout": s.get("login_layout") if s.get("login_layout") in LOGIN_LAYOUTS else "classico", "show_icon": s.get("show_icon", "1") == "1",
            # a versao muda o URL do icone (cache): o personalizado, ou o por omissao na cor do tema
            "favicon_ver": s.get("favicon_ver") or f"tema-{theme}"}
