"""Modulo Seguranca: auditoria, acesso por IP, dois fatores, fator humano e registo por pedido.

Definicoes (tabela settings, entram nos backups):
  seg_ip_ativo 0/1, seg_ip_modo codigo|firewalld, seg_ip_redes (uma rede por linha, '# comentario'),
  seg_ip_agentes 0/1 (firewalld: IPs dos clientes com agente entram no ipset), seg_ip_msg,
  seg_fw_portas, seg_fw_zona, seg_fw_estado (JSON: o que foi aplicado na firewall, para desfazer),
  seg_2fa desligado|opcional|admins|obrigatorio, seg_captcha desligado|falhas|sempre, seg_captcha_falhas,
  seg_bloqueio_falhas, seg_bloqueio_min, seg_registo 0/1, seg_registo_notificar admins|gestores|ambos,
  seg_auditoria_dias.
Tudo so se aplica com o modulo Seguranca ativo. Salvaguardas: 127.0.0.1/::1 tem sempre acesso; a API dos
agentes (/clientes/agente/api/, autenticada por token) nao passa pelo filtro de IP no codigo; e
'python3 manage.py seguranca-ip off' desliga o acesso por IP (e desfaz a firewall) a partir da consola.
"""
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import time

from . import modulos, settings, store, totp

log = logging.getLogger(__name__)

PADRAO = {
    "seg_ip_ativo": "0", "seg_ip_modo": "codigo", "seg_ip_redes": "", "seg_ip_agentes": "1",
    "seg_ip_msg": "O seu endereço ({ip}) não tem acesso a este portal. Se precisa de acesso, contacte o administrador.",
    "seg_fw_portas": "80,443", "seg_fw_zona": "",
    "seg_2fa": "opcional", "seg_captcha": "falhas", "seg_captcha_falhas": "3",
    "seg_bloqueio_falhas": "10", "seg_bloqueio_min": "15",
    "seg_registo": "0", "seg_registo_notificar": "admins", "seg_auditoria_dias": "90",
}
MODOS_2FA = {"desligado": "Desligado", "opcional": "Opcional (cada utilizador ativa na Minha área)",
             "admins": "Obrigatório para administradores", "obrigatorio": "Obrigatório para todos"}
MODOS_CAPTCHA = {"desligado": "Desligado", "falhas": "Só depois de logins falhados do IP", "sempre": "Sempre"}
NOTIFICAR = {"admins": "Administradores", "gestores": "Gestores do portal", "ambos": "Administradores e gestores do portal"}
IPSET = "ipset-portal-acesso"
PERFIL_GESTOR = "Gestor do portal"
POW_BITS = 16                       # fator humano: ~65 mil tentativas no browser (menos de 1 s)
LIVRES = ("/clientes/agente/api/", "/static/", "/favicon.ico", "/i18n/")


def ativo() -> bool:
    return "seguranca" in modulos.ativos()


def cfg(s: dict | None = None) -> dict:
    s = store.get_settings() if s is None else s
    return {k: (s.get(k) if s.get(k) not in (None, "") else v) for k, v in PADRAO.items()} | \
           {"seg_ip_redes": s.get("seg_ip_redes") or "", "seg_fw_zona": s.get("seg_fw_zona") or ""}


def _int(v, padrao: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return padrao


# ============================================================================ IP do cliente
def ip_cliente(request) -> str:
    """IP real: o Nginx (mesma maquina) passa-o em X-Real-IP; de outra origem, ignora o cabecalho."""
    ra = request.remote_addr or ""
    xr = (request.headers.get("X-Real-IP") or "").strip()
    try:
        if xr and ipaddress.ip_address(ra).is_loopback:
            ipaddress.ip_address(xr)
            return xr
    except ValueError:
        pass
    return ra


_cache_redes: dict = {}


def redes(texto: str) -> list:
    """Lista de redes (ip_network) a partir do texto (uma por linha ou separadas por virgula; '#' comenta)."""
    if texto in _cache_redes:
        return _cache_redes[texto]
    out = []
    for ln in (texto or "").splitlines():
        ln = ln.split("#", 1)[0]
        for p in re.split(r"[\s,;]+", ln):
            if p:
                out.append(ipaddress.ip_network(p, strict=False))
    if len(_cache_redes) > 20:
        _cache_redes.clear()
    _cache_redes[texto] = out
    return out


def validar_redes(texto: str) -> tuple[str, list]:
    """Normaliza o texto (erro com a linha invalida). Devolve (texto, redes)."""
    linhas, todas = [], []
    for i, ln in enumerate((texto or "").splitlines(), 1):
        ln = ln.strip()
        if not ln:
            continue
        corpo, _, com = ln.partition("#")
        partes = []
        for p in re.split(r"[\s,;]+", corpo.strip()):
            if not p:
                continue
            try:
                n = ipaddress.ip_network(p, strict=False)
            except ValueError:
                raise ValueError(f"Linha {i}: '{p}' não é um IP nem uma rede (ex. 10.10.101.0/23).") from None
            partes.append(str(n) if n.num_addresses > 1 else str(n.network_address))
            todas.append(n)
        linhas.append(" ".join(partes) + (f"  # {com.strip()}" if com.strip() else "") if partes else f"# {com.strip()}")
    if len(todas) > 2000:
        raise ValueError("Máximo de 2000 redes.")
    return "\n".join(linhas), todas


def permitido(ip: str, lista: list) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if a.is_loopback:
        return True
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return any(a.version == n.version and a in n for n in lista)


def filtro_ip(request) -> str | None:
    """Antes de cada pedido: IP fora da lista -> mensagem a mostrar (e fica na auditoria). None = passa."""
    if not request.path or request.path.startswith(LIVRES):
        return None
    try:
        if not ativo():
            return None
        s = store.get_settings()
        if s.get("seg_ip_ativo") != "1":
            return None
        lista = redes(s.get("seg_ip_redes") or "")
    except Exception:  # noqa: BLE001 - BD indisponivel / lista invalida: nao bloqueia
        return None
    if not lista:
        return None
    ip = ip_cliente(request)
    if permitido(ip, lista):
        return None
    auditar("acesso_ip", "BLOQUEADO", ip=ip, detalhe="IP fora da lista de acesso", caminho=request.path,
            agente=request.headers.get("User-Agent", ""), limitar=300)
    return (s.get("seg_ip_msg") or PADRAO["seg_ip_msg"]).replace("{ip}", ip)


# ============================================================================ auditoria
EVENTOS = {"visita": "Visita ao login", "login": "Login", "logout": "Saída", "2fa": "Dois fatores",
           "captcha": "Fator humano", "acesso_ip": "Acesso por IP", "tentativas": "Bloqueio por tentativas",
           "registo": "Pedido de registo", "validacao": "Validação de registo", "config": "Configuração",
           "firewall": "Firewall"}
_limite: dict = {}


def auditar(evento: str, resultado: str, ip: str = "", utilizador: str = "", detalhe: str = "",
            caminho: str = "", agente: str = "", limitar: int = 0) -> None:
    """Regista na auditoria. limitar=N: no maximo um registo igual (evento, resultado, ip) a cada N segundos."""
    try:
        if limitar:
            ch = (evento, resultado, ip)
            agora = time.time()
            if agora - _limite.get(ch, 0) < limitar:
                return
            if len(_limite) > 5000:
                _limite.clear()
            _limite[ch] = agora
            with store.tx() as t:      # outros workers
                if t.val("SELECT COUNT(*) FROM seg_auditoria WHERE evento = ? AND resultado = ? AND ip = ? AND quando >= ?",
                         (evento, resultado, ip, store.ago(seconds=limitar))):
                    return
        with store.tx() as t:
            t.insert("seg_auditoria", {"quando": store.now(), "ip": ip[:64], "utilizador": (utilizador or "")[:64],
                                       "evento": evento[:20], "resultado": resultado[:10], "detalhe": (detalhe or "")[:2000],
                                       "caminho": (caminho or "")[:255], "agente": (agente or "")[:255]})
            if secrets.randbelow(200) == 0:
                dias = _int(store.get_setting("seg_auditoria_dias"), 90)
                t.run("DELETE FROM seg_auditoria WHERE quando < ?", (store.ago(days=max(dias, 1)),))
    except Exception:  # noqa: BLE001
        log.exception("auditoria")


def _libertados() -> dict:
    try:
        return json.loads(store.get_setting("seg_libertar") or "{}")
    except ValueError:
        return {}


def falhas_recentes(ip: str, minutos: int) -> int:
    desde = max(store.ago(minutes=minutos), _libertados().get(ip, ""))
    with store.tx() as t:
        return int(t.val("SELECT COUNT(*) FROM seg_auditoria WHERE ip = ? AND resultado = 'FALHOU' "
                         "AND evento IN ('login', '2fa', 'captcha') AND quando > ?", (ip, desde)) or 0)


def ips_bloqueados() -> list[dict]:
    """IPs bloqueados agora por tentativas falhadas: [{ip, n, ultimo, minutos}]."""
    c = cfg()
    n, m = _int(c["seg_bloqueio_falhas"], 10), _int(c["seg_bloqueio_min"], 15)
    if n <= 0:
        return []
    with store.tx() as t:
        rows = t.all("SELECT ip, COUNT(*) AS n, MAX(quando) AS ultimo FROM seg_auditoria WHERE resultado = 'FALHOU' "
                     "AND evento IN ('login', '2fa', 'captcha') AND quando >= ? GROUP BY ip", (store.ago(minutes=m),))
    out = []
    for r in rows:
        if r["ip"] and int(r["n"]) >= n:
            falta = bloqueado_por_tentativas(r["ip"])
            if falta:
                out.append({**r, "n": falhas_recentes(r["ip"], m), "minutos": falta})
    return out


def bloqueado_por_tentativas(ip: str) -> int:
    """Minutos de bloqueio que faltam (0 = livre), por N falhas de login do IP em M minutos."""
    if not ativo():
        return 0
    c = cfg()
    n, m = _int(c["seg_bloqueio_falhas"], 10), _int(c["seg_bloqueio_min"], 15)
    if n <= 0 or m <= 0:
        return 0
    try:
        if ipaddress.ip_address(ip).is_loopback:
            return 0
    except ValueError:
        pass
    if falhas_recentes(ip, m) < n:
        return 0
    with store.tx() as t:
        ult = t.val("SELECT MAX(quando) FROM seg_auditoria WHERE ip = ? AND resultado = 'FALHOU' "
                    "AND evento IN ('login', '2fa', 'captcha')", (ip,))
    import datetime as dt
    try:
        fim = dt.datetime.strptime(ult, "%Y-%m-%d %H:%M:%S") + dt.timedelta(minutes=m)
        return max(1, int((fim - dt.datetime.now()).total_seconds() // 60) + 1)
    except (TypeError, ValueError):
        return m


# ============================================================================ fator humano (prova de trabalho)
def captcha_necessario(ip: str) -> bool:
    if not ativo():
        return False
    c = cfg()
    if c["seg_captcha"] == "sempre":
        return True
    if c["seg_captcha"] == "falhas":
        return falhas_recentes(ip, 60) >= _int(c["seg_captcha_falhas"], 3)
    return False


def captcha_novo(session) -> dict:
    d = {"c": secrets.token_hex(16), "b": POW_BITS, "ts": int(time.time())}
    session["seg_pow"] = d
    return d


def captcha_ok(session, nonce: str) -> bool:
    """sha256('<desafio>:<nonce>') com os primeiros N bits a zero. O desafio so serve uma vez (sessao)."""
    d = session.pop("seg_pow", None)
    if not d or time.time() - d.get("ts", 0) > 600 or not re.fullmatch(r"\d{1,12}", nonce or ""):
        return False
    h = int.from_bytes(hashlib.sha256(f"{d['c']}:{nonce}".encode()).digest(), "big")
    return h >> (256 - int(d["b"])) == 0


# ============================================================================ dois fatores
def _admin_2fa() -> dict:
    a = settings.admin() or {}
    return {"enc": a.get("totp_enc") or "", "ultimo": a.get("totp_ultimo") or -1, "rec": a.get("totp_rec") or []}


def dados_2fa(uid) -> dict:
    """{segredo, ultimo, rec(lista de hashes)} do utilizador ('admin' ou id). segredo vazio = sem 2FA."""
    from . import crypto
    if uid == "admin":
        d = _admin_2fa()
    else:
        u = store.get_user(uid=uid) or {}
        d = {"enc": u.get("totp_enc") or "", "ultimo": u.get("totp_ultimo") if u.get("totp_ultimo") is not None else -1,
             "rec": json.loads(u.get("totp_rec") or "[]")}
    try:
        seg = crypto.decrypt(d["enc"]) if d["enc"] else ""
    except Exception:  # noqa: BLE001 - chave mudou
        seg = ""
    return {"segredo": seg, "ultimo": int(d["ultimo"]), "rec": d["rec"]}


def guardar_2fa(uid, segredo: str | None = None, ultimo: int | None = None, rec: list | None = None,
                remover: bool = False) -> None:
    from . import crypto
    campos = {}
    if remover:
        campos = {"totp_enc": "", "totp_ultimo": -1, "totp_rec": "[]"}
    else:
        if segredo is not None:
            campos["totp_enc"] = crypto.encrypt(segredo)
        if ultimo is not None:
            campos["totp_ultimo"] = ultimo
        if rec is not None:
            campos["totp_rec"] = json.dumps(rec)
    if uid == "admin":
        a = settings.admin()
        a.update({k: (json.loads(v) if k == "totp_rec" else v) for k, v in campos.items()})
        settings.save_admin(a)
    else:
        store.update_user_fields(uid, **campos)


def tem_2fa(uid) -> bool:
    return bool(dados_2fa(uid)["segredo"])


def exige_2fa(user) -> bool:
    """O utilizador tem de passar pelo segundo fator (ja configurado ou a configurar no login)."""
    if not ativo():
        return False
    modo = cfg()["seg_2fa"]
    if modo == "desligado":
        return False
    if tem_2fa(user.id):
        return True
    return modo == "obrigatorio" or (modo == "admins" and user.total)


def verificar_2fa(uid, codigo: str) -> str | None:
    """'codigo' / 'recuperacao' se valido (e marca-o usado), senao None."""
    d = dados_2fa(uid)
    if not d["segredo"]:
        return None
    c = totp.verificar(d["segredo"], codigo, d["ultimo"])
    if c is not None:
        guardar_2fa(uid, ultimo=c)
        return "codigo"
    h = totp.hash_recuperacao(codigo or "")
    if codigo and len(codigo.strip()) >= 8 and h in d["rec"]:
        guardar_2fa(uid, rec=[x for x in d["rec"] if x != h])
        return "recuperacao"
    return None


def ativar_2fa(uid, segredo: str, codigo: str) -> list[str] | None:
    """Confirma o primeiro codigo e guarda. Devolve os codigos de recuperacao (mostrados uma vez)."""
    c = totp.verificar(segredo, codigo)
    if c is None:
        return None
    rec = totp.codigos_recuperacao()
    guardar_2fa(uid, segredo=segredo, ultimo=c, rec=[totp.hash_recuperacao(x) for x in rec])
    return rec


def qr_2fa(segredo: str, conta: str) -> dict:
    from . import qr
    emissor = (store.get_setting("portal_name") or "Portal").strip()[:40] or "Portal"
    u = totp.uri(segredo, conta, emissor)
    return {"svg": qr.svg(u, modulo=4), "uri": u, "segredo": totp.formatar(segredo)}


# ============================================================================ registo por pedido
def registo_ativo() -> bool:
    return ativo() and cfg()["seg_registo"] == "1"


def pendentes() -> list[dict]:
    return [u for u in store.list_users() if (u.get("estado") or "") == "pendente"]


def perfis_gestor() -> list[int]:
    """Perfis com a acao 'Validar pedidos de registo' que nao sao de administrador."""
    return [p["id"] for p in store.list_profiles() if "seguranca.pedidos" in p["permissions"] and not p["administrador"]]


def _destino_notificacao() -> dict:
    modo = cfg()["seg_registo_notificar"]
    d = {}
    if modo in ("admins", "ambos"):
        d["admins"] = True
    if modo in ("gestores", "ambos"):
        d["perfis"] = perfis_gestor()
        if modo == "gestores" and not d["perfis"]:
            d["admins"] = True           # ninguem com o perfil: nao perder o pedido
    return d


def notificar_pedido(u: dict) -> None:
    from . import eventos
    dest = _destino_notificacao()
    nome = u.get("name") or u["username"]
    texto = (f"O utilizador {u['username']} ({nome}{', ' + u['email'] if u.get('email') else ''}) autenticou-se no "
             f"diretório (LDAP / Active Directory) e pediu acesso ao portal.\n\n"
             f"Para validar: Segurança > Registo — atribua um perfil e ative a conta.")
    eventos.entregar("seguranca.registo", "pendente", f"Pedido de acesso pendente: {u['username']}", texto,
                     "/seguranca/registo", [{"canal": "portal", "nome": "Registo", "destino": dest},
                                            {"canal": "email", "nome": "Registo (Segurança)", "destino": dest}])


def notificar_aprovado(u: dict, perfil: str) -> None:
    from . import eventos
    if not u.get("email"):
        return
    portal = store.get_setting("portal_name") or "Portal"
    texto = (f"Olá {u.get('name') or u['username']},\n\nO seu acesso ao {portal} foi autorizado"
             f"{' (perfil ' + perfil + ')' if perfil else ''}.\nJá pode entrar com o seu utilizador e a password do diretório.")
    eventos.entregar("seguranca.registo", "aprovado", f"Acesso autorizado: {portal}", texto, "/login",
                     [{"canal": "email", "nome": "Registo (Segurança)", "destino": {"emails": [u["email"]]}}])


def garantir_perfil_gestor() -> None:
    """Cria (uma vez) o perfil 'Gestor do portal': valida pedidos de registo e ve a auditoria."""
    try:
        if store.get_setting("seg_perfil_gestor"):
            return
        if not any(p["name"] == PERFIL_GESTOR for p in store.list_profiles()):
            store.save_profile({"name": PERFIL_GESTOR,
                                "description": "Valida os pedidos de registo (atribui perfil e ativa) e recebe as notificações",
                                "permissions": ["modulo.seguranca", "seguranca.auditoria", "seguranca.pedidos"]})
        store.save_settings({"seg_perfil_gestor": "1"})
    except Exception:  # noqa: BLE001 - outro worker criou ao mesmo tempo
        pass


# ============================================================================ firewalld
def ips_agentes() -> list[str]:
    out = []
    try:
        with store.tx() as t:
            rows = t.all("SELECT endereco, ip_origem FROM clientes")
    except Exception:  # noqa: BLE001
        return out
    for r in rows:
        for v in (r.get("ip_origem"), r.get("endereco")):
            try:
                a = ipaddress.ip_address((v or "").strip())
                if a.version == 4:
                    out.append(str(a))
                    break
            except ValueError:
                continue
    return sorted(set(out))


def portas(c: dict | None = None) -> list[str]:
    c = c or cfg()
    out = []
    for p in re.split(r"[\s,;]+", c["seg_fw_portas"]):
        if p:
            if not p.isdigit() or not 1 <= int(p) <= 65535:
                raise ValueError(f"Porta inválida: {p}")
            out.append(p)
    return list(dict.fromkeys(out))


def fw_disponivel() -> tuple[bool, str]:
    from . import firewall
    if firewall.backend() != "firewalld":
        return False, "O firewalld não está instalado neste servidor (no Docker, a firewall é a do anfitrião)."
    e = firewall.estado(fresco=True)
    if e.get("permissao") is False:
        return False, "O portal não tem permissão para alterar a firewall: sudo ./instalar.sh --firewall <instância> on"
    if not e.get("ok"):
        return False, e.get("msg") or "Não foi possível ler o estado do firewalld."
    if not e.get("ativo"):
        return False, "O serviço firewalld não está ativo."
    return True, ""


def fw_estado() -> dict:
    try:
        return json.loads(store.get_setting("seg_fw_estado") or "{}")
    except ValueError:
        return {}


def _regra(p: str) -> str:
    return f'rule family="ipv4" source ipset="{IPSET}" port port="{p}" protocol="tcp" accept'


def fw_aplicar(utilizador: str) -> list[str]:
    """Cria/atualiza o ipset com as redes IPv4 (e os IPs dos agentes) e as regras ricas por porta; retira da
    zona os servicos/portas que abriam o portal a todos (guardados para repor). Devolve as linhas do relatorio."""
    from . import firewall
    ok, msg = fw_disponivel()
    if not ok:
        raise ValueError(msg)
    c = cfg()
    _, lista = validar_redes(c["seg_ip_redes"])
    v4 = [str(n) for n in lista if n.version == 4]
    if c["seg_ip_agentes"] == "1":
        v4 += [a for a in ips_agentes() if not any(ipaddress.ip_address(a) in n for n in lista if n.version == 4)]
    if not v4:
        raise ValueError("A lista não tem redes IPv4: a firewall ficaria fechada para todos.")
    ps = portas(c)
    rel = []
    zona = c["seg_fw_zona"] or firewall.estado().get("zona_padrao") or "public"
    if not firewall.ZONA_RE.match(zona):
        raise ValueError("Zona inválida.")
    ant = fw_estado()
    rc, out = firewall.fc("--permanent", "--get-ipsets")
    if IPSET not in out.split():
        ok, o = firewall._exec(utilizador, ["--permanent", f"--new-ipset={IPSET}", "--type=hash:net", "--option=family=inet"])
        if not ok:
            raise ValueError(f"Não foi possível criar o ipset: {o}")
        firewall.fc("--reload")
        rel.append(f"ipset {IPSET} criado (hash:net)")
    rc, out = firewall.fc("--permanent", f"--ipset={IPSET}", "--get-entries")
    atuais = set(out.split()) if rc == 0 else set()
    novos = set(v4)
    if novos - atuais:
        firewall.aplicar(utilizador, "ipset_add", {"ipset": IPSET, "entrada": " ".join(sorted(novos - atuais))})
        rel.append(f"ipset: +{len(novos - atuais)} entrada(s)")
    if atuais - novos:
        firewall.aplicar(utilizador, "ipset_del", {"ipset": IPSET, "entrada": " ".join(sorted(atuais - novos))})
        rel.append(f"ipset: -{len(atuais - novos)} entrada(s)")
    # regras ricas: as portas que deixaram de estar na lista saem
    for p in ant.get("portas", []):
        if p not in ps or ant.get("zona") != zona:
            firewall.aplicar(utilizador, "rich_del", {"zona": ant.get("zona") or zona, "regra": _regra(p)})
    for p in ps:
        firewall.aplicar(utilizador, "rich_add", {"zona": zona, "regra": _regra(p)})
    rel.append(f"zona {zona}: regras ricas para {', '.join(ps)}/tcp a partir do ipset")
    # o que abria a porta a todos sai da zona (fica guardado para repor ao desligar)
    removidos = [r for r in ant.get("removidos", []) if ant.get("zona") == zona]
    firewall.invalidar()
    z = firewall.zona(zona, permanente=True)
    svc_porta = {"http": "80", "https": "443"}
    for s in z.get("services", []):
        if svc_porta.get(s) in ps:
            ok, o = firewall.aplicar(utilizador, "servico_del", {"zona": zona, "servico": s})
            if ok:
                removidos.append({"tipo": "servico", "valor": s})
                rel.append(f"serviço {s} retirado da zona (aberto a todos)")
    for pp in z.get("ports", []):
        num, _, proto = pp.partition("/")
        if proto == "tcp" and num in ps:
            ok, o = firewall.aplicar(utilizador, "porta_del", {"zona": zona, "porta": num, "proto": "tcp"})
            if ok:
                removidos.append({"tipo": "porta", "valor": pp})
                rel.append(f"porta {pp} retirada da zona (aberta a todos)")
    firewall.invalidar()
    store.save_settings({"seg_fw_estado": json.dumps({"zona": zona, "portas": ps, "removidos": removidos,
                                                      "entradas": len(novos), "aplicado_em": store.now(),
                                                      "por": utilizador})})
    auditar("firewall", "OK", utilizador=utilizador, detalhe="; ".join(rel))
    return rel


def fw_remover(utilizador: str) -> list[str]:
    """Desfaz o que fw_aplicar fez: regras ricas fora, servicos/portas repostos, ipset apagado."""
    from . import firewall
    ant = fw_estado()
    if not ant:
        return []
    rel = []
    zona = ant.get("zona") or "public"
    for r in ant.get("removidos", []):
        if r["tipo"] == "servico":
            firewall.aplicar(utilizador, "servico_add", {"zona": zona, "servico": r["valor"]})
        else:
            num, _, proto = r["valor"].partition("/")
            firewall.aplicar(utilizador, "porta_add", {"zona": zona, "porta": num, "proto": proto or "tcp"})
        rel.append(f"reposto: {r['tipo']} {r['valor']}")
    for p in ant.get("portas", []):
        firewall.aplicar(utilizador, "rich_del", {"zona": zona, "regra": _regra(p)})
    rel.append(f"regras ricas do ipset retiradas da zona {zona}")
    try:
        firewall.aplicar(utilizador, "ipset_apagar", {"ipset": IPSET})
        rel.append(f"ipset {IPSET} apagado")
    except ValueError:
        pass
    firewall.invalidar()
    store.save_settings({"seg_fw_estado": ""})
    auditar("firewall", "OK", utilizador=utilizador, detalhe="desfeito: " + "; ".join(rel))
    return rel


def desligar_ip(utilizador: str = "consola") -> list[str]:
    """Desliga o acesso por IP (e desfaz a firewall, se aplicada). Usado tambem pelo manage.py."""
    rel = []
    if fw_estado():
        try:
            rel = fw_remover(utilizador)
        except Exception as exc:  # noqa: BLE001
            rel = [f"firewall: {exc}"]
    store.save_settings({"seg_ip_ativo": "0"})
    auditar("config", "OK", utilizador=utilizador, detalhe="acesso por IP desligado")
    return rel


def resumo() -> dict:
    """Numeros para o cartao/aba de auditoria (ultimas 24 h)."""
    desde = store.ago(hours=24)
    with store.tx() as t:
        rows = t.all("SELECT evento, resultado, COUNT(*) AS n FROM seg_auditoria WHERE quando >= ? "
                     "GROUP BY evento, resultado", (desde,))
        top = t.all("SELECT ip, COUNT(*) AS n, MAX(quando) AS ultimo FROM seg_auditoria WHERE quando >= ? "
                    "AND resultado IN ('FALHOU', 'BLOQUEADO') GROUP BY ip ORDER BY n DESC LIMIT 8", (desde,))
    c = {"login_ok": 0, "login_falhou": 0, "bloqueados": 0, "visitas": 0}
    for r in rows:
        n = int(r["n"])
        if r["evento"] == "login":
            c["login_ok" if r["resultado"] == "OK" else "login_falhou"] += n
        elif r["resultado"] == "BLOQUEADO":
            c["bloqueados"] += n
        elif r["evento"] == "visita":
            c["visitas"] += n
    return {"c": c, "top": top, "pendentes": len(pendentes())}


def _env_desligado() -> bool:
    return os.environ.get("PORTAL_SEG_IP_DESLIGAR") == "1"
