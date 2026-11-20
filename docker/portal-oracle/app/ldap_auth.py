"""Autenticacao por LDAP: OpenLDAP ou Active Directory (Microsoft).

Usado quando o modulo Autenticacao esta ativo e o utilizador tem o tipo de autenticacao "LDAP"
(o administrador e sempre local). Fluxo: liga ao servidor (LDAP, STARTTLS ou LDAPS, com ou sem
validacao do certificado), faz bind com a conta de servico, procura o utilizador pelo filtro,
confirma o grupo obrigatorio (se houver) e faz bind com o DN do utilizador e a password dele.

Biblioteca: ldap3 (Python puro, requirements.txt). A configuracao fica na tabela settings
(ldap_*); a password da conta de servico e o certificado da CA ficam cifrados / guardados la.
"""
import hashlib
import os
import ssl
import time

from . import crypto, settings, store

DRIVERS = {"openldap": "OpenLDAP", "ad": "Active Directory (Microsoft)"}
SEGURANCA = {"nenhuma": "Sem cifra (porta 389)", "starttls": "STARTTLS (porta 389)", "ldaps": "LDAPS (porta 636)"}
PADRAO = {
    "openldap": {"filtro": "(objectClass=inetOrgPerson)", "utilizador": "uid", "nome_proprio": "givenName",
                 "ultimo_nome": "sn", "nome": "cn", "email": "mail",
                 "bind": "cn=leitor,ou=servicos,dc=empresa,dc=pt", "base": "ou=pessoas,dc=empresa,dc=pt"},
    "ad": {"filtro": "(&(objectCategory=person)(objectClass=user))", "utilizador": "sAMAccountName",
           "nome_proprio": "givenName", "ultimo_nome": "sn", "nome": "displayName", "email": "mail",
           "bind": "svc-portal@empresa.local", "base": "DC=empresa,DC=local"},
}
CAMPOS = ("ldap_driver", "ldap_hosts", "ldap_porta", "ldap_seguranca", "ldap_verificar", "ldap_bind_dn",
          "ldap_base_dn", "ldap_filtro", "ldap_attr_utilizador", "ldap_attr_nome_proprio", "ldap_attr_ultimo_nome",
          "ldap_attr_nome", "ldap_attr_email", "ldap_grupo", "ldap_auto_criar",
          "ldap_perfil", "ldap_timeout", "ldap_ativo", "ldap_atualizar_dados")
ATTR_RE = __import__("re").compile(r"^[A-Za-z][A-Za-z0-9-]{0,59}$")


def filtro_final(c: dict) -> str:
    """Filtro de procura: o antigo (com {u}) ou (&<filtro de objetos>(<atributo utilizador>={u}))."""
    f = (c.get("filtro_objetos") or "").strip()
    if "{u}" in f:
        return f
    attr = f"({c['attr_utilizador']}={{u}})"
    if not f:
        return attr
    if not f.startswith("("):
        f = f"({f})"
    return f"(&{f}{attr})"


class Falha(Exception):
    """Erro com uma explicacao para o utilizador."""


def config(s: dict | None = None) -> dict:
    s = s if s is not None else store.get_settings()
    drv = s.get("ldap_driver") or "openldap"
    pad = PADRAO.get(drv, PADRAO["openldap"])
    seg = s.get("ldap_seguranca") or "starttls"
    return {
        "driver": drv, "ativo": s.get("ldap_ativo") == "1",
        "hosts": [h.strip() for h in __import__("re").split(r"[,;\s]+", s.get("ldap_hosts") or "") if h.strip()],
        "porta": int(s.get("ldap_porta") or (636 if seg == "ldaps" else 389)),
        "seguranca": seg, "verificar": s.get("ldap_verificar", "1") == "1",
        "ca": s.get("ldap_ca_pem") or "", "bind_dn": s.get("ldap_bind_dn") or "",
        "bind_password": _decifrar(s.get("ldap_bind_password_enc")), "base_dn": s.get("ldap_base_dn") or "",
        "filtro_objetos": s.get("ldap_filtro") or pad["filtro"],
        "attr_utilizador": s.get("ldap_attr_utilizador") or pad["utilizador"],
        "attr_nome_proprio": s.get("ldap_attr_nome_proprio") or pad["nome_proprio"],
        "attr_ultimo_nome": s.get("ldap_attr_ultimo_nome") or pad["ultimo_nome"],
        "attr_nome": s.get("ldap_attr_nome") or pad["nome"],
        "attr_email": s.get("ldap_attr_email") or pad["email"], "grupo": s.get("ldap_grupo") or "",
        "auto_criar": s.get("ldap_auto_criar") == "1", "perfil": s.get("ldap_perfil") or "",
        "atualizar_dados": s.get("ldap_atualizar_dados", "1") == "1",
        "timeout": int(s.get("ldap_timeout") or 10),
    }


def _decifrar(v):
    try:
        return crypto.decrypt(v) if v else ""
    except Exception:  # noqa: BLE001 - chave mudou
        return ""


def configurado(c: dict | None = None) -> bool:
    c = c or config()
    return bool(c["hosts"] and c["base_dn"])


def disponivel() -> tuple[bool, str]:
    try:
        import ldap3  # noqa: F401
        return True, ""
    except ImportError:
        return False, "Falta o pacote Python 'ldap3' (pip install ldap3, ou volte a correr o instalar.sh / recrie o contentor)."


# ------------------------------------------------------------------ ligacao
def _tls(c: dict, reg, host: str = ""):
    """Tls do ldap3 para um servidor. sni = o nome do servidor: sem ele o Python recusa a ligacao
    ("server_hostname cannot be an empty string") e nao consegue validar o nome no certificado."""
    import ldap3
    if c["seguranca"] == "nenhuma":
        return None
    kw = {"validate": ssl.CERT_REQUIRED if c["verificar"] else ssl.CERT_NONE, "version": ssl.PROTOCOL_TLS_CLIENT}
    if host:
        kw["sni"] = host
    if c["verificar"] and c["ca"].strip():
        kw["ca_certs_file"] = _ficheiro_ca(c["ca"])
        reg(f"certificado da CA: o guardado no portal ({c['ca'].count('BEGIN CERTIFICATE')} certificado(s))")
    elif c["verificar"]:
        reg("certificado da CA: os do sistema operativo")
    else:
        reg("AVISO: sem validação do certificado do servidor")
    return ldap3.Tls(**kw)


def _host_porta(h: str) -> tuple[str, int | None]:
    """'ldaps://dc01.empresa.local:636/' -> ('dc01.empresa.local', 636). Aceita [IPv6]:porta."""
    h = h.strip()
    for pre in ("ldaps://", "ldap://"):
        if h.lower().startswith(pre):
            h = h[len(pre):]
    h = h.split("/")[0].strip()
    porta = None
    if h.startswith("["):                       # [IPv6]:porta
        fim = h.find("]")
        host, resto = h[1:fim], h[fim + 1:]
        if resto.startswith(":") and resto[1:].isdigit():
            porta = int(resto[1:])
        return host, porta
    if h.count(":") == 1:
        host, _, p = h.partition(":")
        if p.isdigit():
            return host.strip().strip("."), int(p)
        raise Falha(f"Porta inválida em '{h}'.")
    return h.strip(".") if h.count(":") == 0 else h, porta


def ligar(c: dict, reg=lambda m: None, user_dn: str | None = None, password: str | None = None):
    """Liga a um dos servidores e faz bind (conta de servico, ou o DN indicado). Devolve a Connection."""
    import ldap3
    from ldap3.core import exceptions as lex
    if not c["hosts"]:
        raise Falha("Indique pelo menos um servidor LDAP.")
    servidores, primeiro = [], True
    for h in c["hosts"]:
        host, porta = _host_porta(h)
        if not host:
            raise Falha(f"Servidor inválido: '{h}'. Use o nome ou o IP (ex. dc01.empresa.local ou dc01.empresa.local:636).")
        tls = _tls(c, reg if primeiro else (lambda m: None), host)
        primeiro = False
        servidores.append(ldap3.Server(host, port=porta or c["porta"], use_ssl=c["seguranca"] == "ldaps",
                                       tls=tls, get_info=ldap3.NONE, connect_timeout=c["timeout"]))
    pool = ldap3.ServerPool(servidores, ldap3.FIRST, active=1, exhaust=True) if len(servidores) > 1 else servidores[0]
    dn = user_dn if user_dn is not None else c["bind_dn"]
    pwd = password if user_dn is not None else c["bind_password"]
    auth = ldap3.SIMPLE if dn else ldap3.ANONYMOUS
    reg(f"a ligar a {', '.join(c['hosts'])} (porta {c['porta']}, {SEGURANCA.get(c['seguranca'], c['seguranca'])})")
    try:
        conn = ldap3.Connection(pool, user=dn or None, password=pwd or None, authentication=auth,
                                receive_timeout=c["timeout"], raise_exceptions=True, read_only=True)
        conn.open()
        reg("ligação aberta")
        if c["seguranca"] == "starttls":
            conn.start_tls()
            reg("STARTTLS: canal cifrado")
        conn.bind()
        reg(f"bind OK como {dn or '(anónimo)'}")
        return conn
    except lex.LDAPInvalidCredentialsResult:
        raise Falha(f"Credenciais inválidas para {dn or '(anónimo)'}.") from None
    except lex.LDAPSocketOpenError as exc:
        msg = str(exc)
        if "certificate" in msg.lower() or "ssl" in msg.lower():
            raise Falha(f"Certificado do servidor não validado ({msg[:160]}). Carregue a CA ou desligue a validação.") from None
        raise Falha(f"Não foi possível ligar ({msg[:160]}): servidor, porta ou firewall.") from None
    except lex.LDAPStartTLSError as exc:
        raise Falha(f"STARTTLS falhou ({str(exc)[:160]}): o servidor suporta STARTTLS? Certificado válido?") from None
    except lex.LDAPException as exc:
        raise Falha(f"{type(exc).__name__}: {str(exc)[:200]}") from None


def _esc(v: str) -> str:
    from ldap3.utils.conv import escape_filter_chars
    return escape_filter_chars(v)


def procurar(conn, c: dict, username: str) -> dict | None:
    import ldap3
    filtro = filtro_final(c).replace("{u}", _esc(username))
    attrs = [a for a in {c["attr_utilizador"], c["attr_nome_proprio"], c["attr_ultimo_nome"], c["attr_nome"],
                         c["attr_email"], "memberOf"} if a]
    conn.search(c["base_dn"], filtro, search_scope=ldap3.SUBTREE, attributes=attrs, size_limit=2)
    res = [e for e in conn.response if e.get("type") == "searchResEntry"]
    if len(res) != 1:
        return None
    e = res[0]
    a = {k.lower(): v for k, v in (e.get("attributes") or {}).items()}
    um = lambda k: str(((lambda v: v[0] if isinstance(v, list) and v else v)(a.get((k or "").lower()))) or "")  # noqa: E731
    np_, un = um(c["attr_nome_proprio"]), um(c["attr_ultimo_nome"])
    return {"dn": e["dn"], "utilizador": um(c["attr_utilizador"]), "nome_proprio": np_, "ultimo_nome": un,
            "nome": " ".join(x for x in (np_, un) if x) or um(c["attr_nome"]), "email": um(c["attr_email"]),
            "grupos": [str(x) for x in (a.get("memberof") or [])]}


def consultar(username: str) -> dict:
    """Procura um utilizador no diretorio com a conta de servico (sem password do utilizador): para preencher
    a ficha. {ok, utilizador, nome_proprio, ultimo_nome, email, dn} ou {ok: False, error}."""
    ok, msg = disponivel()
    if not ok:
        return {"ok": False, "error": msg}
    c = config()
    if not configurado(c):
        return {"ok": False, "error": "LDAP por configurar (Configuração → Autenticação)."}
    try:
        conn = ligar(c)
        try:
            u = procurar(conn, c, username)
        finally:
            conn.unbind()
    except Falha as exc:
        return {"ok": False, "error": str(exc)}
    if not u:
        return {"ok": False, "error": f"'{username}' não encontrado ({c['attr_utilizador']}) em {c['base_dn']}."}
    return {"ok": True, **{k: u[k] for k in ("dn", "utilizador", "nome_proprio", "ultimo_nome", "nome", "email")}}


def no_grupo(conn, c: dict, u: dict, username: str) -> bool:
    import ldap3
    g = c["grupo"].strip()
    if not g:
        return True
    if c["driver"] == "ad":     # grupos aninhados: LDAP_MATCHING_RULE_IN_CHAIN
        conn.search(u["dn"], f"(memberOf:1.2.840.113556.1.4.1941:={_esc(g)})", search_scope=ldap3.BASE, attributes=["cn"])
        return any(e.get("type") == "searchResEntry" for e in conn.response)
    if any(x.lower() == g.lower() for x in u["grupos"]):
        return True
    conn.search(g, f"(|(member={_esc(u['dn'])})(uniqueMember={_esc(u['dn'])})(memberUid={_esc(username)}))",
                search_scope=ldap3.BASE, attributes=["cn"])
    return any(e.get("type") == "searchResEntry" for e in conn.response)


def autenticar(username: str, password: str) -> tuple[bool, dict | str]:
    """(True, {dn, nome, email}) ou (False, motivo). Nunca aceita password vazia (bind anonimo)."""
    if not password:
        return False, "password vazia"
    ok, msg = disponivel()
    if not ok:
        return False, msg
    c = config()
    if not configurado(c):
        return False, "LDAP por configurar"
    try:
        conn = ligar(c)
        try:
            u = procurar(conn, c, username)
            if not u:
                return False, "utilizador não encontrado no LDAP"
            if not no_grupo(conn, c, u, username):
                return False, "não pertence ao grupo obrigatório"
        finally:
            conn.unbind()
        conn2 = ligar(c, user_dn=u["dn"], password=password)
        conn2.unbind()
        return True, u
    except Falha as exc:
        return False, str(exc)


def testar(dados: dict, utilizador: str = "", password: str = "") -> dict:
    """Teste da configuracao (do formulario, ainda nao guardada). Devolve {ok, msg, log}."""
    linhas, t0 = [], time.time()
    reg = lambda m: linhas.append(f"[{time.time() - t0:5.2f}s] {m}")  # noqa: E731
    ok, msg = disponivel()
    if not ok:
        return {"ok": False, "msg": msg, "log": msg}
    c = config(dados)
    try:
        conn = ligar(c, reg)
        try:
            import ldap3
            conn.search(c["base_dn"], "(objectClass=*)", search_scope=ldap3.BASE, attributes=["objectClass"])
            if not any(e.get("type") == "searchResEntry" for e in conn.response):
                raise Falha(f"A base de procura '{c['base_dn']}' não existe ou não é visível para a conta de serviço.")
            reg(f"base de procura OK: {c['base_dn']}")
            if utilizador:
                u = procurar(conn, c, utilizador)
                if not u:
                    raise Falha(f"Utilizador '{utilizador}' não encontrado com o filtro {filtro_final(c)}.")
                reg(f"utilizador encontrado: {u['dn']}")
                reg(f"  {c['attr_utilizador']} = {u['utilizador'] or '—'} · {c['attr_nome_proprio']} = {u['nome_proprio'] or '—'}"
                    f" · {c['attr_ultimo_nome']} = {u['ultimo_nome'] or '—'} · {c['attr_email']} = {u['email'] or '—'}")
                if c["grupo"]:
                    if not no_grupo(conn, c, u, utilizador):
                        raise Falha(f"'{utilizador}' não pertence ao grupo {c['grupo']}.")
                    reg(f"pertence ao grupo {c['grupo']}")
                if password:
                    ligar(c, reg, user_dn=u["dn"], password=password).unbind()
                    reg("password do utilizador: OK")
        finally:
            conn.unbind()
        msg = "Ligação OK" + (f"; utilizador '{utilizador}' validado" if utilizador else "")
        reg(msg)
        return {"ok": True, "msg": msg, "log": "\n".join(linhas)}
    except Falha as exc:
        reg(f"ERRO: {exc}")
        return {"ok": False, "msg": str(exc), "log": "\n".join(linhas)}
    except Exception as exc:  # noqa: BLE001
        reg(f"ERRO: {type(exc).__name__}: {exc}")
        return {"ok": False, "msg": f"{type(exc).__name__}: {exc}", "log": "\n".join(linhas)}


def registar_acesso(utilizador: str, metodo: str, ok: bool, detalhe: str = "", ip: str = "") -> None:
    try:
        with store.tx() as t:
            t.insert("auth_log", {"quando": store.now(), "utilizador": utilizador[:64], "metodo": metodo,
                                  "resultado": "OK" if ok else "FALHOU", "detalhe": detalhe[:500], "ip": ip[:64]})
    except Exception:  # noqa: BLE001 - o registo nunca impede o login
        pass


def _ficheiro_ca(pem: str) -> str:
    """O ssl precisa de um ficheiro: data/ldap/ca-<hash>.pem (0600), criado quando falta."""
    d = os.path.join(settings.DATA_DIR, "ldap")
    os.makedirs(d, mode=0o700, exist_ok=True)
    f = os.path.join(d, f"ca-{hashlib.sha256(pem.encode()).hexdigest()[:16]}.pem")
    if not os.path.exists(f):
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(pem)
    return f


def validar_ca(pem: str) -> str:
    """Confirma que o texto tem certificados PEM validos. Devolve um resumo (assunto e validade)."""
    from cryptography import x509
    pem = pem.strip()
    if not pem:
        return ""
    if "BEGIN CERTIFICATE" not in pem:
        raise ValueError("O certificado tem de estar em formato PEM (-----BEGIN CERTIFICATE-----).")
    try:
        certs = x509.load_pem_x509_certificates(pem.encode())
    except ValueError as exc:
        raise ValueError(f"Certificado inválido: {exc}") from None
    return "; ".join(f"{c.subject.rfc4514_string()} (até {c.not_valid_after_utc:%Y-%m-%d})" for c in certs)
