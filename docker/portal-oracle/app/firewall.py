"""Firewall do servidor do portal: firewalld (firewall-cmd) ou ufw.

Le e altera a firewall com 'sudo -n firewall-cmd ...' / 'sudo -n ufw ...' (regra criada por
'instalar.sh --firewall NOME on'). Sem shell: os argumentos sao validados e passados em lista.
Cada alteracao fica na tabela firewall_acoes (quem, quando, comando, resultado).
"""
import ipaddress
import os
import re
import shutil
import subprocess
import time

from . import store

ZONA_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
NOME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
PORTA_RE = re.compile(r"^\d{1,5}(-\d{1,5})?$")
PROTOS = ("tcp", "udp", "sctp", "dccp")
TIPOS_IPSET = ("hash:ip", "hash:net", "hash:ip,port", "hash:net,port", "hash:mac")
_cache: dict = {}


def _bin(nome: str) -> str | None:
    for d in ("/usr/bin", "/usr/sbin", "/bin", "/sbin"):
        if os.path.exists(os.path.join(d, nome)):
            return os.path.join(d, nome)
    return shutil.which(nome)


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    if os.geteuid() != 0:
        cmd = ["sudo", "-n", *cmd]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + (("\n" + r.stderr) if r.stderr.strip() else "")).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 255, str(exc)


def backend() -> str:
    """'firewalld', 'ufw' ou ''. Prefere o que estiver ativo."""
    fc, uf = _bin("firewall-cmd"), _bin("ufw")
    if fc and os.path.exists("/run/firewalld") or (fc and not uf):
        return "firewalld"
    if uf:
        return "ufw"
    return "firewalld" if fc else ""


def sem_permissao(saida: str) -> bool:
    s = saida.lower()
    return "password is required" in s or "a password is required" in s or "not in the sudoers" in s \
        or "may not run sudo" in s or "authorization failed" in s or "not authorized" in s


def fc(*args, timeout: int = 30) -> tuple[int, str]:
    return _run([_bin("firewall-cmd") or "firewall-cmd", *args], timeout)


def ufw(*args, timeout: int = 30) -> tuple[int, str]:
    return _run([_bin("ufw") or "ufw", *args], timeout)


def registar(utilizador: str, comando: str, ok: bool, resultado: str) -> None:
    with store.tx() as t:
        t.insert("firewall_acoes", {"quando": store.now(), "utilizador": utilizador, "comando": comando[:500],
                                    "ok": 1 if ok else 0, "resultado": resultado[:2000]})


def historico(limite: int = 30) -> list[dict]:
    with store.tx() as t:
        return t.all("SELECT * FROM firewall_acoes ORDER BY id DESC LIMIT ?", (limite,))


# ------------------------------------------------------------------ leitura
def _lista(texto: str) -> list[str]:
    return [x for x in texto.split() if x]


def estado() -> dict:
    b = backend()
    if not b:
        return {"backend": "", "ok": False, "msg": "Nem firewalld nem ufw instalados neste servidor."}
    if b == "ufw":
        rc, out = ufw("status", "verbose")
        if rc != 0:
            return {"backend": b, "ok": False, "msg": out, "permissao": not sem_permissao(out)}
        info = {}
        for l in out.splitlines():
            if ":" in l and not l.startswith(("To", "--")):
                k, v = l.split(":", 1)
                info[k.strip().lower()] = v.strip()
        rc2, ver = _run([_bin("ufw") or "ufw", "version"])
        return {"backend": b, "ok": True, "ativo": info.get("status") == "active", "info": info,
                "versao": ver.splitlines()[0] if ver else "", "saida": out}
    rc, st = fc("--state")
    if sem_permissao(st):
        return {"backend": b, "ok": False, "permissao": False, "msg": st}
    ativo = st.strip() == "running"
    d = {"backend": b, "ok": True, "ativo": ativo, "estado": st.strip()}
    d["versao"] = fc("--version")[1].strip()
    if not ativo:
        return d
    d["zona_padrao"] = fc("--get-default-zone")[1].strip()
    d["log_denied"] = fc("--get-log-denied")[1].strip()
    d["panico"] = fc("--query-panic")[1].strip() == "yes"
    rc, out = fc("--get-active-zones")
    zonas, atual = [], None
    for l in out.splitlines():
        if not l.startswith((" ", "\t")) and l.strip():
            atual = {"nome": l.strip().split()[0], "interfaces": [], "sources": []}
            zonas.append(atual)
        elif atual and ":" in l:
            k, v = l.strip().split(":", 1)
            atual["interfaces" if k.strip() == "interfaces" else "sources"] = _lista(v)
    d["zonas_ativas"] = zonas
    d["zonas"] = _lista(fc("--get-zones")[1])
    return d


def zona(nome: str, permanente: bool = False) -> dict:
    """Configuracao de uma zona (firewall-cmd --list-all)."""
    if not ZONA_RE.match(nome):
        raise ValueError("Zona inválida.")
    args = ["--zone", nome, "--list-all"] + (["--permanent"] if permanente else [])
    rc, out = fc(*args)
    if rc != 0:
        raise ValueError(out)
    d, chave = {"nome": nome, "rich rules": []}, None
    for l in out.splitlines()[1:]:
        if chave == "rich rules" and (l.startswith("\t") or l.startswith("    ")) and l.strip():
            d["rich rules"].append(l.strip())
            continue
        if ":" in l:
            k, v = l.strip().split(":", 1)
            chave = k.strip()
            if chave == "rich rules":
                continue
            d[chave] = v.strip()
    for k in ("services", "ports", "protocols", "source-ports", "sources", "interfaces", "forward-ports", "icmp-blocks"):
        d[k] = _lista(d.get(k, ""))
    return d


def ipsets() -> list[dict]:
    rc, out = fc("--permanent", "--get-ipsets")
    if rc != 0:
        raise ValueError(out)
    lista = []
    for n in _lista(out):
        rc, info = fc("--permanent", f"--info-ipset={n}")
        tipo, opcoes = "", ""
        for l in info.splitlines():
            l = l.strip()
            if l.startswith("type:"):
                tipo = l.split(":", 1)[1].strip()
            elif l.startswith("options:"):
                opcoes = l.split(":", 1)[1].strip()
        rc, ent = fc("--permanent", f"--ipset={n}", "--get-entries")
        entradas = [x for x in ent.splitlines() if x.strip()]
        lista.append({"nome": n, "tipo": tipo, "opcoes": opcoes, "entradas": entradas, "n": len(entradas),
                      "ficheiro": f"/etc/firewalld/ipsets/{n}.xml"})
    return lista


def regras_ufw() -> list[dict]:
    rc, out = ufw("status", "numbered")
    if rc != 0:
        raise ValueError(out)
    regras = []
    for l in out.splitlines():
        m = re.match(r"^\[\s*(\d+)\]\s+(.+?)\s{2,}(ALLOW|DENY|REJECT|LIMIT)( IN| OUT|)\s+(.+?)(\s+#\s*(.*))?$", l)
        if m:
            regras.append({"n": int(m.group(1)), "para": m.group(2).strip(), "acao": m.group(3),
                           "direcao": (m.group(4) or " IN").strip(), "de": m.group(5).strip(), "comentario": m.group(7) or ""})
    return regras


def logs(tipo: str = "servico", filtro: str = "") -> list[str]:
    jc = _bin("journalctl") or "/usr/bin/journalctl"
    if tipo == "kernel":
        rc, out = _run([jc, "-k", "--no-pager", "-o", "short-iso", "-n", "3000"], 60)
        linhas = [l for l in out.splitlines() if re.search(r"REJECT|DROP|DENY|BLOCK", l)]
    else:
        rc, out = _run([jc, "-u", "firewalld", "--no-pager", "-o", "short-iso", "-n", "500"], 60)
        linhas = out.splitlines()
    if sem_permissao(out):
        raise PermissionError(out)
    f = filtro.strip().lower()
    return [l for l in linhas if not f or f in l.lower()][-500:]


# ------------------------------------------------------------------ validacao
def _porta(v: str) -> str:
    v = v.strip()
    if not PORTA_RE.match(v) or any(not 1 <= int(x) <= 65535 for x in v.split("-")):
        raise ValueError("Porta inválida (1-65535, ou intervalo 8000-8100).")
    return v


def _proto(v: str) -> str:
    if v not in PROTOS:
        raise ValueError("Protocolo inválido.")
    return v


def _ip(v: str, permitir_rede: bool = True) -> str:
    v = v.strip()
    try:
        return str(ipaddress.ip_network(v, strict=False)) if "/" in v and permitir_rede else str(ipaddress.ip_address(v))
    except ValueError:
        raise ValueError(f"Endereço inválido: {v}") from None


# ------------------------------------------------------------------ alteracoes (firewalld)
def aplicar(utilizador: str, acao: str, d: dict) -> tuple[bool, str]:
    """Alteracao na firewall. Permanente e em execucao (--permanent + a mesma em runtime)."""
    b = backend()
    if b == "ufw":
        return _aplicar_ufw(utilizador, acao, d)
    z = d.get("zona", "")
    if acao not in ("ipset_novo", "ipset_apagar", "ipset_add", "ipset_del", "reload", "log_denied", "panico") \
            and not ZONA_RE.match(z):
        raise ValueError("Zona inválida.")
    args: list[str]
    if acao in ("servico_add", "servico_del"):
        if not NOME_RE.match(d.get("servico", "")):
            raise ValueError("Serviço inválido.")
        args = ["--zone", z, f"--{'add' if acao.endswith('add') else 'remove'}-service={d['servico']}"]
    elif acao in ("porta_add", "porta_del"):
        args = ["--zone", z, f"--{'add' if acao.endswith('add') else 'remove'}-port={_porta(d.get('porta', ''))}/{_proto(d.get('proto', 'tcp'))}"]
    elif acao in ("source_add", "source_del"):
        s = d.get("source", "").strip()
        src = f"ipset:{s[6:]}" if s.startswith("ipset:") and NOME_RE.match(s[6:]) else _ip(s)
        args = ["--zone", z, f"--{'add' if acao.endswith('add') else 'remove'}-source={src}"]
    elif acao in ("rich_add", "rich_del"):
        r = d.get("regra", "").strip()
        if not r.startswith("rule ") or len(r) > 500 or "\n" in r:
            raise ValueError("Regra rica inválida: começa por 'rule ' (ex. rule family=\"ipv4\" source address=\"10.0.0.0/8\" service name=\"ssh\" accept).")
        args = ["--zone", z, f"--{'add' if acao.endswith('add') else 'remove'}-rich-rule={r}"]
    elif acao == "ipset_novo":
        if not NOME_RE.match(d.get("ipset", "")) or d.get("tipo") not in TIPOS_IPSET:
            raise ValueError("Nome ou tipo de ipset inválido.")
        args = [f"--new-ipset={d['ipset']}", f"--type={d['tipo']}"]
        ok, out = _exec(utilizador, ["--permanent", *args])
        if ok:
            fc("--reload")
        return ok, out
    elif acao == "ipset_apagar":
        if not NOME_RE.match(d.get("ipset", "")):
            raise ValueError("Ipset inválido.")
        ok, out = _exec(utilizador, ["--permanent", f"--delete-ipset={d['ipset']}"])
        if ok:
            fc("--reload")
        return ok, out
    elif acao in ("ipset_add", "ipset_del"):
        if not NOME_RE.match(d.get("ipset", "")):
            raise ValueError("Ipset inválido.")
        entradas = [x.strip() for x in re.split(r"[\s,;]+", d.get("entrada", "")) if x.strip()]
        if not entradas or len(entradas) > 500:
            raise ValueError("Indique de 1 a 500 endereços.")
        res = []
        for e in entradas:
            e = _ip(e) if "," not in e else e
            ok, out = _exec(utilizador, ["--permanent", f"--ipset={d['ipset']}",
                                         f"--{'add' if acao.endswith('add') else 'remove'}-entry={e}"])
            if ok:
                fc(f"--ipset={d['ipset']}", f"--{'add' if acao.endswith('add') else 'remove'}-entry={e}")
            res.append((ok, f"{e}: {out}"))
        return all(r[0] for r in res), "\n".join(r[1] for r in res)
    elif acao == "reload":
        return _exec(utilizador, ["--reload"])
    elif acao == "log_denied":
        v = d.get("valor", "off")
        if v not in ("all", "unicast", "broadcast", "multicast", "off"):
            raise ValueError("Valor inválido.")
        return _exec(utilizador, [f"--set-log-denied={v}"])
    elif acao == "panico":
        return _exec(utilizador, ["--panic-on" if d.get("valor") == "on" else "--panic-off"])
    else:
        raise ValueError("Ação desconhecida.")
    ok, out = _exec(utilizador, ["--permanent", *args])
    if ok:
        ok2, out2 = _exec(utilizador, args, registar_ok=False)
        out = out + ("" if ok2 else f"\n(em execução: {out2})")
    return ok, out


def _exec(utilizador: str, args: list[str], registar_ok: bool = True) -> tuple[bool, str]:
    rc, out = fc(*args)
    ok = rc == 0
    if registar_ok or not ok:
        registar(utilizador, "firewall-cmd " + " ".join(args), ok, out)
    return ok, out


def _aplicar_ufw(utilizador: str, acao: str, d: dict) -> tuple[bool, str]:
    if acao == "ufw_add":
        politica = d.get("politica", "allow")
        if politica not in ("allow", "deny", "reject", "limit"):
            raise ValueError("Ação inválida.")
        args = [politica]
        if d.get("de", "").strip():
            args += ["from", _ip(d["de"])]
            args += ["to", "any"]
            if d.get("porta", "").strip():
                args += ["port", _porta(d["porta"]).replace("-", ":"), "proto", _proto(d.get("proto", "tcp"))]
        else:
            if not d.get("porta", "").strip():
                raise ValueError("Indique a porta ou a origem.")
            args += [f"{_porta(d['porta']).replace('-', ':')}/{_proto(d.get('proto', 'tcp'))}"]
        com = re.sub(r"[^\w .,:-]", "", d.get("comentario", ""))[:60]
        if com:
            args += ["comment", com]
    elif acao == "ufw_del":
        n = str(d.get("n", ""))
        if not n.isdigit():
            raise ValueError("Número inválido.")
        args = ["--force", "delete", n]
    elif acao == "reload":
        args = ["reload"]
    else:
        raise ValueError("Ação desconhecida.")
    rc, out = ufw(*args)
    registar(utilizador, "ufw " + " ".join(args), rc == 0, out)
    return rc == 0, out
