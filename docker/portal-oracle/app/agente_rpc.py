"""Pedidos as bases de dados atraves do agente cliente (servico Oracle do agente-portal).

Uma base de dados associada a um cliente (oracle_dbs.cliente_id) com o agente online e o servico Oracle
ativo e tratada pelo agente: o portal envia cada pedido (HTTPS, assinado com o token do cliente) e fica a
aguardar a resposta. O codigo do modulo Oracle nao muda: bd.connect devolve uma Ligacao com a mesma
interface (cursor, execute, fetch*, var, callproc, close) que o python-oracledb.

Modos (oracle_dbs.agente_modo): nao (o portal liga-se diretamente), auto (o agente se estiver disponivel,
senao o portal) e obrigatorio (so pelo agente; sem ele da erro).

Seguranca: o certificado do agente e confirmado pela impressao digital (SHA-256) que o agente envia na
sincronizacao (canal autenticado com o token); o corpo so e enviado depois dessa confirmacao."""
import base64
import datetime
import decimal
import hashlib
import hmac
import http.client
import json
import secrets
import socket
import ssl
import threading
import time

try:
    import oracledb
    _BaseErro = getattr(oracledb, "DatabaseError", oracledb.Error)
except ImportError:  # pragma: no cover
    oracledb = None
    _BaseErro = Exception

MODOS = {"nao": "Não usar (o portal liga-se à base de dados)",
         "auto": "Automático: o agente, se estiver disponível; senão o portal",
         "obrigatorio": "Obrigatório: só pelo agente (sem ele dá erro)"}
PAUSA_FALHA = 60           # depois de uma falha de rede, o portal nao tenta o agente durante N s

_falhas: dict[int, tuple[float, str]] = {}
_ultimo: dict[int, dict] = {}       # db_id -> {"via", "motivo", "quando"} (para mostrar quem tratou)
_trava = threading.Lock()


class Indisponivel(Exception):
    """O agente nao respondeu (rede, certificado, servico parado...)."""


class ErroOracleRemoto(_BaseErro):
    """Erro do Oracle (ORA-...) devolvido pelo agente; e um oracledb.Error para o resto do codigo."""

    def __init__(self, msg: str):
        Exception.__init__(self, msg)
        self.msg = msg

    def __str__(self) -> str:
        return self.msg


# ============================================================ valores
def _enc(v):
    if isinstance(v, datetime.datetime):
        return {"$dt": v.isoformat()}
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, Var):
        return {"$var": "str" if v.tipo is str else "int", "size": v.tamanho}
    raise TypeError(f"tipo nao suportado: {type(v).__name__}")


def _dec(v):
    if isinstance(v, dict):
        if "$dt" in v:
            return datetime.datetime.fromisoformat(v["$dt"])
        if "$d" in v:
            return datetime.date.fromisoformat(v["$d"])
        if "$dec" in v:
            return decimal.Decimal(v["$dec"])
        if "$td" in v:
            return datetime.timedelta(seconds=v["$td"])
        if "$b64" in v:
            return base64.b64decode(v["$b64"])
    return v


# ============================================================ escolha do caminho
def modo(db: dict) -> str:
    m = (db or {}).get("agente_modo") or ""
    if not (db or {}).get("cliente_id"):
        return "nao"
    return m if m in MODOS else "auto"


def alvo(db: dict) -> tuple[dict | None, str]:
    """(destino, "") se os pedidos desta base de dados devem ir pelo agente; (None, motivo) se nao."""
    if modo(db) == "nao":
        return None, ""
    from . import clientes
    c = clientes.obter(db["cliente_id"])
    if not c:
        return None, "o cliente associado já não existe"
    nome = c["nome"]
    if c["contacto"]["cor"] not in ("verde", "laranja"):
        return None, f"agente {nome} sem contacto ({c['contacto']['texto'].lower()})"
    rpc = (c.get("info") or {}).get("rpc")
    if not rpc:
        return None, (f"agente {nome} sem o serviço Oracle (versão {c.get('versao') or '?'}; "
                      "instale com setup.sh --oracle)")
    if not rpc.get("oracledb"):
        return None, f"agente {nome}: python-oracledb não instalado"
    if not rpc.get("ativo"):
        return None, f"serviço Oracle do agente {nome} parado: {rpc.get('erro') or 'sem detalhe'}"
    if not rpc.get("fp"):
        return None, f"agente {nome} sem certificado"
    f = _falhas.get(c["id"])
    if f and time.time() - f[0] < PAUSA_FALHA:
        return None, f"agente {nome} indisponível há pouco: {f[1]}"
    tk = clientes.token(c)
    if not tk:
        return None, f"token do cliente {nome} ilegível (fernet.key mudou?)"
    return {"cliente_id": c["id"], "nome": nome, "host": c["endereco"], "porta": int(rpc.get("porta") or 8795),
            "fp": rpc["fp"], "token": tk, "versao": c.get("versao")}, ""


def falhou(a: dict, motivo: str) -> None:
    _falhas[a["cliente_id"]] = (time.time(), motivo)


def registar(db: dict, via: str, motivo: str = "") -> None:
    if db and db.get("id"):
        with _trava:
            _ultimo[int(db["id"])] = {"via": via, "motivo": motivo, "quando": time.time()}


def estado(db: dict) -> dict:
    """Para mostrar na pagina: quem trata os pedidos desta base de dados (sem contactar o agente)."""
    m = modo(db)
    if m == "nao":
        return {"via": "portal", "texto": "Portal", "motivo": "", "modo": m, "cor": "cinza"}
    a, motivo = alvo(db)
    if a:
        return {"via": "agente", "texto": f"Agente {a['nome']}", "motivo": f"{a['host']}:{a['porta']}", "modo": m,
                "cor": "verde", "cliente_id": a["cliente_id"]}
    return {"via": "portal" if m == "auto" else "erro", "texto": "Portal" if m == "auto" else "Indisponível",
            "motivo": motivo, "modo": m, "cor": "laranja" if m == "auto" else "vermelho",
            "cliente_id": db.get("cliente_id")}


# ============================================================ pedidos
def chamar(a: dict, caminho: str, dados: dict, timeout: float = 60) -> dict:
    corpo = json.dumps(dados, default=_enc).encode()
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    assin = hmac.new(a["token"].encode(), f"{ts}\n{nonce}\n{caminho}\n".encode() + corpo, hashlib.sha256).hexdigest()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE                  # certificado proprio: confirmado pela impressao digital
    c = http.client.HTTPSConnection(a["host"], a["porta"], timeout=min(timeout, 15), context=ctx)
    try:
        try:
            c.connect()
        except (OSError, ssl.SSLError) as e:
            raise Indisponivel(f"sem ligação a {a['host']}:{a['porta']} ({e})") from None
        fp = hashlib.sha256(c.sock.getpeercert(binary_form=True) or b"").hexdigest()
        if not hmac.compare_digest(fp, a["fp"]):
            raise Indisponivel(f"o certificado de {a['host']}:{a['porta']} não é o do agente {a['nome']}")
        c.sock.settimeout(timeout)
        try:
            c.request("POST", caminho, corpo, {"Content-Type": "application/json", "X-Ts": ts, "X-Nonce": nonce,
                                           "X-Assinatura": assin})
            r = c.getresponse()
            texto = r.read()
        except (OSError, http.client.HTTPException) as e:
            if isinstance(e, (socket.timeout, TimeoutError)):
                raise Indisponivel(f"o agente {a['nome']} não respondeu em {int(timeout)} s") from None
            raise Indisponivel(f"ligação ao agente {a['nome']} perdida ({e})") from None
    finally:
        c.close()
    try:
        d = json.loads(texto.decode() or "{}")
    except ValueError:
        d = {"erro": texto[:200].decode(errors="replace")}
    if r.status == 409 or (r.status == 500 and d.get("oracle")):
        raise ErroOracleRemoto(d.get("erro") or "erro do Oracle")
    if r.status == 404:
        raise LookupError(d.get("erro") or "não encontrado")
    if r.status != 200:
        raise Indisponivel(f"agente {a['nome']}: HTTP {r.status} {d.get('erro') or ''}".strip())
    return d


def _db(db: dict, password: str) -> dict:
    return {k: db.get(k) for k in ("host", "port", "sid", "service_name", "pdb", "username", "role")} | \
        {"password": password}


# ============================================================ ligacao com a interface do python-oracledb
class Var:
    def __init__(self, tipo, tamanho=None):
        self.tipo, self.tamanho, self.valor = tipo, tamanho or (4000 if tipo is str else None), None

    def getvalue(self, *_a):
        return self.valor

    def setvalue(self, _i, v):
        self.valor = v


class Cursor:
    def __init__(self, lig: "Ligacao"):
        self.lig, self.description, self._linhas, self._i, self.rowcount = lig, None, [], 0, -1

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        self.close()

    def close(self):
        self._linhas = []

    def execute(self, sql, parametros=None, **kw):
        binds = parametros if parametros is not None else ({} if kw else [])
        if kw:
            binds = {**(binds if isinstance(binds, dict) else {}), **kw}
        if isinstance(binds, tuple):
            binds = list(binds)
        d = self.lig._pedido("/rpc/sql", {"sql": sql, "binds": binds}, timeout=1800)
        self.description = [(c, None, None, None, None, None, None) for c in d["cols"]] if d.get("cols") else None
        self._linhas = [tuple(_dec(v) for v in l) for l in d.get("linhas") or []]
        self._i, self.rowcount = 0, d.get("rowcount", -1)
        outs = d.get("outs") or {}
        if isinstance(binds, dict):
            for k, v in binds.items():
                if isinstance(v, Var) and str(k) in outs:
                    v.valor = _dec(outs[str(k)])
        else:
            for i, v in enumerate(binds):
                if isinstance(v, Var) and str(i) in outs:
                    v.valor = _dec(outs[str(i)])
        return self

    def fetchone(self):
        if self._i < len(self._linhas):
            self._i += 1
            return self._linhas[self._i - 1]
        return None

    def fetchall(self):
        r, self._i = self._linhas[self._i:], len(self._linhas)
        return r

    def fetchmany(self, n=100):
        r = self._linhas[self._i:self._i + n]
        self._i += len(r)
        return r

    def __iter__(self):
        while True:
            r = self.fetchone()
            if r is None:
                return
            yield r

    def var(self, tipo, tamanho=None, *_a, **_k):
        return Var(tipo, tamanho)

    def callproc(self, nome, args=()):
        if any(isinstance(x, Var) for x in args or ()):
            raise NotImplementedError("callproc com variaveis pelo agente: use Ligacao.dbms_output()")
        self.lig._pedido("/rpc/proc", {"nome": nome, "args": list(args or [])})


class Ligacao:
    remoto = True

    def __init__(self, a: dict, db: dict, password: str):
        self.a = a
        self.via = f"agente cliente {a['nome']}"
        try:
            d = chamar(a, "/rpc/abrir", {"db": _db(db, password)}, timeout=90)
        except Indisponivel as e:
            falhou(a, str(e))
            raise
        self.sessao, self.version = d["sessao"], d.get("versao")
        registar(db, "agente", a["nome"])

    def _pedido(self, caminho: str, dados: dict, timeout: float = 120) -> dict:
        try:
            return chamar(self.a, caminho, {"sessao": self.sessao, **dados}, timeout=timeout)
        except Indisponivel as e:
            raise ErroOracleRemoto(str(e)) from None
        except LookupError as e:
            raise ErroOracleRemoto(f"Agente {self.a['nome']}: {e}") from None

    def cursor(self) -> Cursor:
        return Cursor(self)

    def dbms_output(self) -> list[str]:
        return self._pedido("/rpc/dbms_output", {})["linhas"]

    def commit(self):
        self.cursor().execute("COMMIT")

    def rollback(self):
        self.cursor().execute("ROLLBACK")

    def close(self):
        try:
            chamar(self.a, "/rpc/fechar", {"sessao": self.sessao}, timeout=15)
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        self.close()


def ligar(db: dict, password: str):
    """Ligacao pelo agente (Ligacao) ou None se for o portal a ligar-se. Com o modo 'obrigatorio' e o
    agente indisponivel, erro (ErroOracleRemoto). Regista quem tratou o pedido."""
    a, motivo = alvo(db)
    if a:
        try:
            return Ligacao(a, db, password)
        except Indisponivel as e:
            motivo = str(e)
    if modo(db) == "obrigatorio":
        registar(db, "erro", motivo)
        raise ErroOracleRemoto(f"Base de dados só acessível pelo agente cliente, que está indisponível: {motivo}")
    registar(db, "portal", motivo)
    return None


def ultimo(db_id) -> dict | None:
    return _ultimo.get(int(db_id)) if db_id else None
