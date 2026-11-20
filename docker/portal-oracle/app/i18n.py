"""Linguas do portal.

O portal e escrito em portugues (lingua base). As outras linguas sao dicionarios
"texto em portugues" -> "traducao", aplicados no browser (static/i18n.js) a todos os textos da
pagina, incluindo os que o JavaScript cria depois.

- Traducoes de origem: app/i18n/<codigo>.xml (vem com o portal; tambem e o catalogo dos textos).
- Alteracoes feitas no portal (Configuracao > Aparencia > Linguagem): tabela 'traducoes', que tem
  prioridade sobre o ficheiro e entra nos backups.
- Numeros dentro dos textos sao substituidos por {n} na procura ("ha 5 min" -> "ha {n} min").

Formato XML (exportar / importar):

    <?xml version="1.0" encoding="UTF-8"?>
    <lingua codigo="en" nome="English">
      <texto>
        <origem>Guardar</origem>
        <traducao>Save</traducao>
      </texto>
    </lingua>
"""
import hashlib
import json
import os
import re
import secrets
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from . import settings, store

BASE = "pt"
PASTA = os.path.join(os.path.dirname(__file__), "i18n")
CODIGO_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")
LINGUAS_PADRAO = [{"codigo": "pt", "nome": "Português", "ativa": True},
                  {"codigo": "en", "nome": "English", "ativa": True}]
_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_cache: dict = {}


def normalizar(texto: str) -> str:
    """Igual ao browser: espacos seguidos -> um, sem espacos nas pontas."""
    return re.sub(r"\s+", " ", texto or "").strip()


def chave(origem: str) -> str:
    return hashlib.sha1(normalizar(origem).encode()).hexdigest()


def marcadores(texto: str) -> list[str]:
    return sorted(re.findall(r"\{[a-z0-9]+\}", texto or ""))


# ------------------------------------------------------------------ linguas
def linguas() -> list[dict]:
    try:
        v = json.loads(store.get_setting("linguas") or "null")
    except ValueError:
        v = None
    if not isinstance(v, list) or not v:
        v = [dict(x) for x in LINGUAS_PADRAO]
    if not any(x["codigo"] == BASE for x in v):
        v.insert(0, dict(LINGUAS_PADRAO[0]))
    for x in v:
        x["base"] = x["codigo"] == BASE
        x["ativa"] = bool(x.get("ativa", True)) or x["base"]
        x["ficheiro"] = os.path.exists(_ficheiro(x["codigo"]))
    return v


def ativas() -> list[dict]:
    return [x for x in linguas() if x["ativa"]]


def guardar_linguas(lista: list[dict]) -> None:
    store.save_settings({"linguas": json.dumps([{"codigo": x["codigo"], "nome": x["nome"], "ativa": bool(x["ativa"])}
                                                for x in lista])})
    _invalidar()


def nome(codigo: str) -> str:
    return next((x["nome"] for x in linguas() if x["codigo"] == codigo), codigo)


def padrao() -> str:
    c = store.get_setting("lingua_padrao") or BASE
    return c if any(x["codigo"] == c for x in ativas()) else BASE


def do_utilizador(user_key: str | None, cookie: str | None = None) -> str:
    validas = {x["codigo"] for x in ativas()}
    for c in ((store.get_setting(f"idioma.{user_key}") if user_key else None), cookie):
        if c in validas:
            return c
    return padrao()


def escolher(user_key: str | None, codigo: str) -> bool:
    if codigo not in {x["codigo"] for x in ativas()}:
        return False
    if user_key:
        store.save_settings({f"idioma.{user_key}": codigo})
    return True


# ------------------------------------------------------------------ dicionarios
def _ficheiro(codigo: str) -> str:
    return os.path.join(PASTA, f"{codigo}.xml")


def _ler_xml(conteudo: bytes) -> tuple[ET.Element, list[tuple[str, str]]]:
    """Le um XML de traducoes. Recusa DOCTYPE/ENTITY (entidades externas ou expansao)."""
    cab = conteudo[:4096].lower()
    if b"<!doctype" in cab or b"<!entity" in conteudo.lower():
        raise ValueError("o XML não pode ter DOCTYPE nem ENTITY")
    raiz = ET.fromstring(conteudo)
    pares = []
    for el in raiz.iter("texto"):
        o, t = el.find("origem"), el.find("traducao")
        pares.append((o.text if o is not None and o.text else "", t.text if t is not None and t.text else ""))
    return raiz, pares


def base_ficheiro(codigo: str) -> dict[str, str]:
    """Traducoes do ficheiro app/i18n/<codigo>.xml (origem normalizada -> traducao)."""
    f = _ficheiro(codigo)
    k = ("f", codigo, os.path.getmtime(f) if os.path.exists(f) else 0)
    if k not in _cache:
        d = {}
        if os.path.exists(f):
            with open(f, "rb") as fh:
                for o, t in _ler_xml(fh.read())[1]:
                    if normalizar(o):
                        d[normalizar(o)] = normalizar(t)
        _cache[k] = d
    return _cache[k]


def catalogo() -> list[str]:
    """Todos os textos do portal (origens), a partir do ficheiro ingles, que e completo."""
    return sorted(base_ficheiro("en"), key=str.lower)


def alteracoes(codigo: str) -> dict[str, dict]:
    with store.tx() as t:
        rows = t.all("SELECT origem, texto, updated_at, updated_by FROM traducoes WHERE lingua = ?", (codigo,))
    return {normalizar(r["origem"]): r for r in rows}


def dicionario(codigo: str) -> dict[str, str]:
    """Ficheiro + alteracoes do portal (estas primeiro). Sem entradas vazias."""
    if codigo == BASE:
        return {}
    d = dict(base_ficheiro(codigo))
    for o, r in alteracoes(codigo).items():
        if r["texto"]:
            d[o] = normalizar(r["texto"])
        else:
            d.pop(o, None)
    return {o: t for o, t in d.items() if t}


def versao(codigo: str) -> str:
    """Muda quando o ficheiro ou as alteracoes mudam (cache do browser)."""
    f = _ficheiro(codigo)
    m = os.path.getmtime(f) if os.path.exists(f) else 0
    return hashlib.md5(f"{codigo}{m}{store.get_setting('i18n_rev') or 0}".encode()).hexdigest()[:10]


def _invalidar() -> None:
    store.save_settings({"i18n_rev": str(time.time())})


def js(codigo: str) -> str:
    """Conteudo do /i18n/<codigo>.js: o dicionario para o static/i18n.js."""
    return "window.I18N=" + json.dumps({"lingua": codigo, "t": dicionario(codigo)}, ensure_ascii=False,
                                       separators=(",", ":")) + ";"


# ------------------------------------------------------------------ editar
def listar(codigo: str, q: str = "", estado: str = "") -> list[dict]:
    """Linhas para a tabela de Aparencia > Linguagem."""
    base, alt = base_ficheiro(codigo), alteracoes(codigo)
    origens = set(catalogo()) | set(base) | set(alt)
    q = normalizar(q).lower()
    out = []
    for o in sorted(origens, key=str.lower):
        fich = base.get(o, "")
        a = alt.get(o)
        atual = normalizar(a["texto"]) if a and a["texto"] else ("" if a else fich)
        est = "alterada" if a else ("traduzida" if fich else "em falta")
        if estado and est != estado:
            continue
        if q and q not in o.lower() and q not in atual.lower():
            continue
        out.append({"chave": chave(o), "origem": o, "traducao": atual, "ficheiro": fich, "estado": est,
                    "por": a["updated_by"] if a else "", "em": a["updated_at"] if a else ""})
    return out


def guardar(codigo: str, origem: str, texto: str, por: str) -> None:
    """Guarda uma traducao. Igual a do ficheiro -> volta a usar a do ficheiro."""
    o, texto = normalizar(origem), normalizar(texto)
    if marcadores(o) != marcadores(texto) and texto:
        raise ValueError(f"a tradução tem de manter os marcadores {', '.join(marcadores(o)) or '(nenhum)'}")
    with store.tx() as t:
        t.run("DELETE FROM traducoes WHERE lingua = ? AND chave = ?", (codigo, chave(o)))
        if texto != base_ficheiro(codigo).get(o, ""):
            t.run("INSERT INTO traducoes (lingua, chave, origem, texto, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?)",
                  (codigo, chave(o), o, texto, store.now(), por))
    _invalidar()


def repor(codigo: str, chaves: list[str] | None = None) -> int:
    with store.tx() as t:
        if chaves is None:
            n = t.val("SELECT COUNT(*) FROM traducoes WHERE lingua = ?", (codigo,))
            t.run("DELETE FROM traducoes WHERE lingua = ?", (codigo,))
        else:
            n = 0
            for k in chaves:
                n += 1
                t.run("DELETE FROM traducoes WHERE lingua = ? AND chave = ?", (codigo, k))
    _invalidar()
    return int(n or 0)


def contagens(codigo: str) -> dict:
    linhas = listar(codigo)
    c = {"total": len(linhas), "traduzida": 0, "alterada": 0, "em falta": 0}
    for x in linhas:
        c[x["estado"]] += 1
    return c


# ------------------------------------------------------------------ XML
def exportar(codigo: str) -> bytes:
    linhas = listar(codigo)
    partes = ['<?xml version="1.0" encoding="UTF-8"?>',
              f'<!-- Traducoes do portal: {escape(nome(codigo))}. Altere so o texto de <traducao>; '
              'mantenha <origem> e os marcadores como {n}. Traducao vazia = mostra o portugues. -->',
              f'<lingua codigo="{escape(codigo)}" nome="{escape(nome(codigo))}" textos="{len(linhas)}">']
    for x in linhas:
        partes.append(f"  <texto>\n    <origem>{escape(x['origem'])}</origem>\n"
                      f"    <traducao>{escape(x['traducao'])}</traducao>\n  </texto>")
    partes.append("</lingua>\n")
    return "\n".join(partes).encode("utf-8")


def validar_xml(codigo: str, conteudo: bytes) -> dict:
    """Valida um XML a importar, sem alterar nada. Devolve o relatorio e as alteracoes a aplicar."""
    r = {"ok": False, "erros": [], "avisos": [], "novas": [], "mudadas": [], "iguais": 0, "vazias": 0,
         "desconhecidas": [], "total": 0, "lingua": "", "nome": ""}
    if len(conteudo) > 5 * 1024 * 1024:
        r["erros"].append("Ficheiro maior que 5 MB.")
        return r
    try:
        raiz, pares = _ler_xml(conteudo)
    except (ET.ParseError, ValueError) as exc:
        r["erros"].append(f"XML inválido: {exc}")
        return r
    if raiz.tag != "lingua":
        r["erros"].append(f"O elemento principal tem de ser <lingua> (encontrado <{raiz.tag}>).")
        return r
    r["lingua"], r["nome"] = raiz.get("codigo", ""), raiz.get("nome", "")
    if r["lingua"] and r["lingua"] != codigo:
        r["erros"].append(f"O ficheiro é da língua '{r['lingua']}', não de '{codigo}'.")
    if not pares:
        r["erros"].append("O ficheiro não tem nenhum <texto>.")
    cat = set(catalogo())
    atual = {x["origem"]: x["traducao"] for x in listar(codigo)}
    vistos = set()
    for i, (o, t) in enumerate(pares, 1):
        o, t = normalizar(o), normalizar(t)
        if not o:
            r["erros"].append(f"Texto n.º {i}: <origem> vazia.")
            continue
        if o in vistos:
            r["avisos"].append(f"Repetido (fica o último): {o[:80]}")
        vistos.add(o)
        if len(t) > 4000:
            r["erros"].append(f"Tradução demasiado longa (> 4000): {o[:80]}")
            continue
        if t and marcadores(o) != marcadores(t):
            r["erros"].append(f"Marcadores diferentes em «{o[:60]}»: origem {marcadores(o) or '[]'}, "
                              f"tradução {marcadores(t) or '[]'}")
            continue
        if o not in cat:
            r["desconhecidas"].append(o)
        if not t:
            r["vazias"] += 1
        if atual.get(o, None) is None:
            r["novas"].append({"origem": o, "traducao": t})
        elif atual[o] != t:
            r["mudadas"].append({"origem": o, "antes": atual[o], "traducao": t})
        else:
            r["iguais"] += 1
    r["total"] = len(pares)
    if r["desconhecidas"]:
        r["avisos"].append(f"{len(r['desconhecidas'])} texto(s) não existem no portal (são guardados, mas não aparecem).")
    r["ok"] = not r["erros"]
    return r


def guardar_pendente(relatorio: dict) -> str:
    token = secrets.token_hex(12)
    d = os.path.join(settings.DATA_DIR, "tmp")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"i18n-{token}.json"), "w", encoding="utf-8") as fh:
        json.dump(relatorio, fh, ensure_ascii=False)
    return token


def ler_pendente(token: str) -> dict | None:
    if not re.fullmatch(r"[0-9a-f]{24}", token or ""):
        return None
    f = os.path.join(settings.DATA_DIR, "tmp", f"i18n-{token}.json")
    if not os.path.exists(f) or time.time() - os.path.getmtime(f) > 3600:
        return None
    with open(f, encoding="utf-8") as fh:
        return json.load(fh)


def aplicar_pendente(codigo: str, token: str, por: str) -> int:
    r = ler_pendente(token)
    if not r or not r.get("ok"):
        raise ValueError("A validação expirou ou falhou: volte a importar o ficheiro.")
    n = 0
    for x in r["novas"] + r["mudadas"]:
        guardar(codigo, x["origem"], x["traducao"], por)
        n += 1
    os.remove(os.path.join(settings.DATA_DIR, "tmp", f"i18n-{token}.json"))
    return n
