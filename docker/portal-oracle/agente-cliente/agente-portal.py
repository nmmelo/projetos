#!/usr/bin/env python3
"""Agente cliente do Portal (estilo agente Zabbix).

Corre como servico (systemd) no servidor cliente e, a cada INTERVALO segundos:
  - envia ao portal o estado do servidor (SO, CPU, memoria, discos, uptime);
  - recebe as regras de notificacao (id, nome, ficheiro de log) definidas no portal;
  - le a ultima linha do log de cada regra e, se mudou, envia-a;
  - envia as notificacoes recebidas pelo socket local (de outros scripts).

Outros scripts (rman, shrink, ...) notificam o portal com:
  agente-portal notificar <ID> [ok|aviso|erro|info] "mensagem"

Servico Oracle (opcional, ORACLE_RPC=1): o portal envia ao agente os pedidos as bases de dados deste
servidor (consultas, inspecao, shrink) e fica a aguardar a resposta; o agente liga-se ao Oracle com o
python-oracledb (venv em /opt/agente-portal/venv). HTTPS na porta RPC_PORTA (8795) com certificado
proprio (o portal confirma a impressao digital que o agente lhe envia na sincronizacao) e cada pedido
assinado com o token (HMAC-SHA256 com hora e nonce: um pedido nao pode ser repetido).

So usa a biblioteca padrao do Python 3 (3.6+). O .env tem os valores sensiveis cifrados com uma
chave local (.chave, so o root le) ligada a esta maquina (/etc/machine-id).

Comandos: run | configurar | notificar | testar | estado | intervalo | valor | oracle | versao
"""
import argparse
import base64
import datetime
import decimal
import glob
import grp
import http.server
import hashlib
import hmac
import json
import os
import platform
import pwd
import re
import secrets
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

VERSAO = "1.8.1"
BASE = os.environ.get("AGENTE_DIR", "/opt/agente-portal")
ENV = os.path.join(BASE, ".env")
CHAVE = os.path.join(BASE, ".chave")
FILA = os.path.join(BASE, "fila.jsonl")
ESTADO = os.path.join(BASE, "estado.json")
SOCKET = os.environ.get("AGENTE_SOCKET", "/run/agente-portal/agente.sock")
ATUALIZACAO = os.path.join(BASE, "atualizacao.json")
SERVICOS_F = os.path.join(BASE, "servicos.json")     # servicos monitorizados (lista vinda do portal)
TLS_CRT = os.path.join(BASE, "tls.crt")
TLS_KEY = os.path.join(BASE, "tls.key")
RPC_PORTA = 8795
GRUPO = "agente-portal"
SENSIVEIS = ("TOKEN",)
ESTADOS = ("ok", "aviso", "erro", "info")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,40}$")


# ============================================================ cifra (so biblioteca padrao)
def _chave() -> bytes:
    with open(CHAVE, "rb") as f:
        k = f.read().strip()
    try:
        with open("/etc/machine-id", "rb") as f:
            k += f.read().strip()
    except OSError:
        pass
    return hashlib.sha256(k).digest()


def _fluxo(chave: bytes, nonce: bytes, n: int) -> bytes:
    out, i = b"", 0
    while len(out) < n:
        out += hmac.new(chave, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
        i += 1
    return out[:n]


def cifrar(texto: str) -> str:
    k = _chave()
    ke, km = hashlib.sha256(k + b"e").digest(), hashlib.sha256(k + b"m").digest()
    nonce, dados = secrets.token_bytes(16), texto.encode()
    c = bytes(a ^ b for a, b in zip(dados, _fluxo(ke, nonce, len(dados))))
    tag = hmac.new(km, nonce + c, hashlib.sha256).digest()
    return "enc:" + base64.urlsafe_b64encode(nonce + c + tag).decode()


def decifrar(valor: str) -> str:
    if not valor.startswith("enc:"):
        return valor
    k = _chave()
    ke, km = hashlib.sha256(k + b"e").digest(), hashlib.sha256(k + b"m").digest()
    b = base64.urlsafe_b64decode(valor[4:].encode())
    nonce, c, tag = b[:16], b[16:-32], b[-32:]
    if not hmac.compare_digest(tag, hmac.new(km, nonce + c, hashlib.sha256).digest()):
        raise ValueError("valor cifrado invalido (chave diferente ou .env copiado de outra maquina)")
    return bytes(a ^ b for a, b in zip(c, _fluxo(ke, nonce, len(c)))).decode()


# ============================================================ configuracao (.env)
def ler_env(decifrado: bool = True) -> dict:
    cfg = {}
    with open(ENV) as f:
        for l in f:
            l = l.strip()
            if l and not l.startswith("#") and "=" in l:
                k, v = l.split("=", 1)
                cfg[k.strip()] = v.strip()
    if decifrado:
        for k in SENSIVEIS:
            if k in cfg:
                cfg[k] = decifrar(cfg[k])
    return cfg


def gravar_env(cfg: dict) -> None:
    linhas = ["# Agente do portal. Gerado por 'agente-portal configurar'. Valores sensiveis cifrados (enc:).",
              "# Mudar o intervalo: agente-portal intervalo <segundos>"]
    for k in ("PORTAL_URL", "CLIENTE_ID", "TOKEN", "INTERVALO", "VERIFICAR_TLS", "CA_FICHEIRO", "UTILIZADOR_SSH", "EXTRAS", "LOGS_PASTAS", "COMANDOS_CADA", "FIREWALL_GERIR",
              "ORACLE_RPC", "RPC_PORTA", "RPC_ESCUTA", "RPC_ORIGENS", "ORACLE_LIB"):
        if k in cfg:
            v = cfg[k]
            if k in SENSIVEIS and not str(v).startswith("enc:"):
                v = cifrar(str(v))
            linhas.append(f"{k}={v}")
    tmp = ENV + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(linhas) + "\n")
    os.replace(tmp, ENV)


# ============================================================ HTTP para o portal
def _ctx(cfg):
    if not cfg.get("PORTAL_URL", "").startswith("https"):
        return None
    if cfg.get("VERIFICAR_TLS", "1") == "0":
        c = ssl.create_default_context()
        c.check_hostname, c.verify_mode = False, ssl.CERT_NONE
        return c
    return ssl.create_default_context(cafile=cfg.get("CA_FICHEIRO") or None)


def pedido(cfg: dict, caminho: str, dados: dict, timeout: int = 20) -> dict:
    url = cfg["PORTAL_URL"].rstrip("/") + "/clientes/agente/api/" + caminho
    req = urllib.request.Request(url, data=json.dumps(dados).encode(), method="POST", headers={
        "Content-Type": "application/json", "User-Agent": f"agente-portal/{VERSAO}",
        "X-Agente-Id": cfg["CLIENTE_ID"], "Authorization": "Bearer " + cfg["TOKEN"]})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx(cfg)) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error")
        except Exception:  # noqa: BLE001
            msg = ""
        raise RuntimeError(f"HTTP {e.code} {msg or e.reason}") from None


# ============================================================ estado do servidor
def _ler(caminho, defeito=""):
    try:
        with open(caminho) as f:
            return f.read()
    except OSError:
        return defeito


def _os_release() -> dict:
    d = {}
    for l in _ler("/etc/os-release").splitlines():
        if "=" in l:
            k, v = l.split("=", 1)
            d[k] = v.strip().strip('"')
    return d


def _cmd(*args) -> str:
    try:
        return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


_cpu_ant = None


def _cpu():
    global _cpu_ant
    p = _ler("/proc/stat").splitlines()[0].split()[1:]
    v = [int(x) for x in p]
    idle, total = v[3] + (v[4] if len(v) > 4 else 0), sum(v)
    res = None
    if _cpu_ant:
        dt, di = total - _cpu_ant[0], idle - _cpu_ant[1]
        res = round(100 * (1 - di / dt), 1) if dt > 0 else None
    _cpu_ant = (total, idle)
    return res


def recolher(cfg: dict) -> dict:
    osr = _os_release()
    mem = {}
    for l in _ler("/proc/meminfo").splitlines():
        k, v = l.split(":", 1)
        mem[k] = int(v.split()[0]) * 1024
    total, livre = mem.get("MemTotal", 0), mem.get("MemAvailable", mem.get("MemFree", 0))
    discos, vistos = [], set()
    for l in _ler("/proc/mounts").splitlines():
        dev, ponto, fs = l.split()[:3]
        if fs in ("ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "vfat", "nfs", "nfs4", "cifs", "ocfs2", "acfs") \
                and dev not in vistos:
            vistos.add(dev)
            try:
                s = os.statvfs(ponto)
            except OSError:
                continue
            t, l_ = s.f_blocks * s.f_frsize, s.f_bavail * s.f_frsize
            if t:
                discos.append({"ponto": ponto, "fs": fs, "total": t, "livre": l_,
                               "pct": round(100 * (t - l_) / t, 1)})
    up = float((_ler("/proc/uptime", "0 0").split() or ["0"])[0])
    familia = "redhat" if re.search(r"rhel|fedora|centos", osr.get("ID_LIKE", "") + osr.get("ID", "")) else \
        ("debian" if re.search(r"debian|ubuntu", osr.get("ID_LIKE", "") + osr.get("ID", "")) else osr.get("ID", ""))
    return {
        "versao": VERSAO, "intervalo": int(cfg.get("INTERVALO", 60)), "hostname": socket.getfqdn(),
        "so": osr.get("PRETTY_NAME", platform.platform()), "familia": familia, "kernel": platform.release(),
        "arquitetura": platform.machine(), "cpus": os.cpu_count(), "cpu": _cpu(),
        "load": [round(x, 2) for x in os.getloadavg()], "uptime": int(up),
        "mem": {"total": total, "livre": livre, "pct": round(100 * (total - livre) / total, 1) if total else None},
        "discos": discos, "virtual": _cmd("systemd-detect-virt") or "none", "python": platform.python_version(),
        "utilizador_ssh": cfg.get("UTILIZADOR_SSH", ""), "agora": datetime.datetime.now().isoformat(timespec="seconds"),
        "rpc": rpc_info(cfg),
        "atualizacao": _ler_json(ATUALIZACAO),
        "servicos": estado_servicos(),
    }


SERV_RE = re.compile(r"^[A-Za-z0-9@_.:-]{1,120}$")


def estado_servicos() -> list:
    """Estado dos servicos monitorizados (lista definida no portal, no cartao Servicos do resumo do cliente)."""
    nomes = [n for n in (_ler_json(SERVICOS_F) or []) if isinstance(n, str) and SERV_RE.match(n)][:60]
    if not nomes or not shutil.which("systemctl"):
        return []
    out = []
    for n in nomes:
        unit = n if "." in n else n + ".service"
        rc, txt, _ = _run(["systemctl", "show", unit, "--no-pager",
                           "-p", "ActiveState,SubState,Description,LoadState,ActiveEnterTimestamp,MainPID"], timeout=10)
        d = dict(l.split("=", 1) for l in txt.splitlines() if "=" in l)
        out.append({"unit": n, "estado": d.get("ActiveState") or "desconhecido", "sub": d.get("SubState", ""),
                    "descricao": d.get("Description", ""), "existe": d.get("LoadState") not in ("not-found", None, ""),
                    "desde": d.get("ActiveEnterTimestamp", ""), "pid": d.get("MainPID", "")})
    return out


# ============================================================ dados extra (processos, sistema, logs, atualizacoes)
# Enviados na sincronizacao quando estao "devidos" (cada tipo tem o seu intervalo) ou quando o portal os pede
# (botao "Pedir dados agora"). As atualizacoes pendentes sao lentas (dnf/yum consultam os repositorios): correm
# numa thread e seguem na sincronizacao seguinte. EXTRAS=0 no .env desliga tudo.
EXTRAS_CADA = {"processos": 0, "sistema": 3600, "logs": 300, "ficheiros": 300, "atualizacoes": 6 * 3600, "scan": None}
_ext = {"ultimo": {}, "prontos": {}, "a_correr": set(), "ficheiros_cfg": []}
LOGS_PASTAS = "/var/log /u01 /u02 /opt /oracle /app /home"    # onde o portal pode pedir ficheiros (LOGS_PASTAS no .env)
ROTADO_RE = re.compile(r"([-._]\d{8}(\d{2,6})?|\.\d{1,3})$|\.(gz|xz|bz2|zst|zip|old)$")
_proc_ant = {"t": None, "d": {}}


def _run(args, timeout=60, env=None):
    try:
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                           timeout=timeout, env=env)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, "", str(e)


def _procs_ler() -> dict:
    d = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            st = _ler("/proc/%s/stat" % pid)
            if not st:
                continue
            comm = st[st.index("(") + 1:st.rindex(")")]
            f = st[st.rindex(")") + 2:].split()
            uid = os.stat("/proc/" + pid).st_uid
            cmd = _ler("/proc/%s/cmdline" % pid).replace("\0", " ").strip() or "[%s]" % comm
            d[int(pid)] = {"ppid": int(f[1]), "estado": f[0], "ticks": int(f[11]) + int(f[12]), "threads": int(f[17]),
                           "inicio": int(f[19]), "rss": int(f[21]) * os.sysconf("SC_PAGE_SIZE"), "uid": uid,
                           "nome": comm, "cmd": cmd[:300]}
        except (OSError, ValueError, IndexError):
            continue
    return d


def recolher_processos() -> dict:
    hz = os.sysconf("SC_CLK_TCK")
    if _proc_ant["t"] is None or time.time() - _proc_ant["t"] > 3600:
        _proc_ant.update(t=time.time(), d=_procs_ler())
        time.sleep(1)
    agora, d = time.time(), _procs_ler()
    dt = max(0.1, agora - _proc_ant["t"])
    ant = _proc_ant["d"]
    btime = 0
    for l in _ler("/proc/stat").splitlines():
        if l.startswith("btime"):
            btime = int(l.split()[1])
    utilizadores = {}
    lista = []
    for pid, p in d.items():
        a = ant.get(pid)
        cpu = round(100.0 * (p["ticks"] - a["ticks"]) / hz / dt, 1) if a and a["inicio"] == p["inicio"] else 0.0
        if p["uid"] not in utilizadores:
            try:
                utilizadores[p["uid"]] = pwd.getpwuid(p["uid"]).pw_name
            except KeyError:
                utilizadores[p["uid"]] = str(p["uid"])
        lista.append({"pid": pid, "ppid": p["ppid"], "user": utilizadores[p["uid"]], "cpu": max(0.0, cpu), "rss": p["rss"],
                      "threads": p["threads"], "estado": p["estado"], "nome": p["nome"], "cmd": p["cmd"],
                      "inicio": int(btime + p["inicio"] / hz) if btime else None})
    _proc_ant.update(t=agora, d=d)
    estados = {}
    for p in lista:
        estados[p["estado"]] = estados.get(p["estado"], 0) + 1
    topo = sorted(lista, key=lambda p: -p["cpu"])[:30]
    vistos = {p["pid"] for p in topo}
    topo += [p for p in sorted(lista, key=lambda p: -p["rss"]) if p["pid"] not in vistos][:20]
    return {"total": len(lista), "threads": sum(p["threads"] for p in lista), "estados": estados,
            "lista": sorted(topo, key=lambda p: (-p["cpu"], -p["rss"]))}


def recolher_sistema() -> dict:
    mem = {}
    for l in _ler("/proc/meminfo").splitlines():
        k, v = l.split(":", 1)
        mem[k] = int(v.split()[0]) * 1024
    modelo = ""
    for l in _ler("/proc/cpuinfo").splitlines():
        if l.lower().startswith("model name"):
            modelo = l.split(":", 1)[1].strip()
            break
    ips = [l.split()[1] + " " + l.split()[3] for l in _cmd("ip", "-o", "addr", "show").splitlines()
           if len(l.split()) > 3 and l.split()[1] != "lo" and l.split()[2] in ("inet", "inet6")
           and not l.split()[3].startswith("fe80")]
    rota = _cmd("ip", "route", "show", "default").split("\n")[0]
    dns = [l.split()[1] for l in _ler("/etc/resolv.conf").splitlines() if l.startswith("nameserver") and len(l.split()) > 1]
    btime = 0
    for l in _ler("/proc/stat").splitlines():
        if l.startswith("btime"):
            btime = int(l.split()[1])
    falhados = [l.split()[0] for l in _cmd("systemctl", "--failed", "--no-legend", "--plain").splitlines() if l.strip()]
    portas = sorted({l.split()[3].rsplit(":", 1)[-1] for l in _cmd("ss", "-H", "-ltn").splitlines() if len(l.split()) > 3},
                    key=lambda x: int(x) if x.isdigit() else 0)
    fw = "firewalld " + (_cmd("firewall-cmd", "--state") or "parado") if shutil.which("firewall-cmd") else \
        ("ufw " + (_cmd("ufw", "status").split("\n")[0].split(":")[-1].strip() or "?") if shutil.which("ufw") else "")
    pacotes = len(_cmd("rpm", "-qa").splitlines()) if shutil.which("rpm") and not shutil.which("dpkg-query") else \
        len(_cmd("dpkg-query", "-f", ".\n", "-W").splitlines()) if shutil.which("dpkg-query") else None
    return {"cpu_modelo": modelo, "mem_total": mem.get("MemTotal"), "swap_total": mem.get("SwapTotal"),
            "swap_livre": mem.get("SwapFree"), "ips": ips[:12], "rota": rota, "dns": dns[:4],
            "fuso": _cmd("timedatectl", "show", "-p", "Timezone", "--value") or _ler("/etc/timezone").strip(),
            "ntp": _cmd("timedatectl", "show", "-p", "NTPSynchronized", "--value"),
            "selinux": _cmd("getenforce") if shutil.which("getenforce") else "", "firewall": fw,
            "arranque": datetime.datetime.fromtimestamp(btime).isoformat(timespec="seconds") if btime else "",
            "sessoes": len(_cmd("who").splitlines()), "servicos_falhados": falhados[:30], "portas": portas[:60],
            "pacotes": pacotes}


def recolher_logs() -> dict:
    if shutil.which("journalctl"):
        rc, out, err = _run(["journalctl", "--no-pager", "-q", "-o", "short-iso", "-p", "warning", "--since", "-24h",
                             "-n", "300"], timeout=60)
        fonte = "journalctl -p warning (últimas 24 h)"
        linhas = out.splitlines() if rc == 0 else ["(erro: %s)" % err.strip()[:200]]
    else:
        f = "/var/log/messages" if os.path.exists("/var/log/messages") else "/var/log/syslog"
        fonte = f + " (erros e avisos)"
        linhas = [l for l in _ler(f).splitlines()[-5000:] if re.search(r"(?i)error|fail|warn|crit", l)]
    return {"fonte": fonte, "linhas": [l[:400] for l in linhas[-300:]]}


def recolher_atualizacoes() -> dict:
    env = dict(os.environ, LANG="C", LC_ALL="C")
    pacotes, seg, erro, ferramenta, reinicio = [], set(), "", "", None
    tipos, gravidade = {}, {}
    if shutil.which("dnf") or shutil.which("yum"):
        ferramenta = "dnf" if shutil.which("dnf") else "yum"
        rc, out, err = _run([ferramenta, "-q", "check-update"], timeout=900, env=env)
        if rc not in (0, 100):
            erro = (err or out).strip()[-300:]
        for l in out.splitlines():
            if l.startswith(("Obsoleting", "Security:")) or not l.strip():
                if l.startswith("Obsoleting"):
                    break
                continue
            c = l.split()
            if len(c) == 3 and "." in c[0]:
                pacotes.append({"nome": c[0].rsplit(".", 1)[0], "arq": c[0].rsplit(".", 1)[1], "versao": c[1], "repo": c[2]})
        rc2, out2, _e = _run([ferramenta, "-q", "updateinfo", "list"], timeout=600, env=env)
        for l in out2.splitlines():               # ADVISORY  TIPO[/GRAVIDADE]  PACOTE-VERSAO
            c = l.split()
            if len(c) >= 3:
                nome, t = c[-1].rsplit("-", 2)[0], c[-2].lower()
                tipo = ("seguranca" if ("sec" in t or "security" in t) else "correcao" if "bugfix" in t
                        else "melhoria" if "enhancement" in t else "novo" if "newpackage" in t else "")
                if tipo == "seguranca":
                    seg.add(nome)
                if tipo and tipos.get(nome) != "seguranca":
                    tipos[nome] = tipo
                if "/" in c[-2]:
                    gravidade[nome] = c[-2].split("/")[0]
        if shutil.which("needs-restarting"):
            reinicio = _run(["needs-restarting", "-r"], timeout=120, env=env)[0] == 1
    elif shutil.which("apt"):
        ferramenta = "apt"
        rc, out, err = _run(["apt", "list", "--upgradable"], timeout=300, env=env)
        if rc != 0:
            erro = err.strip()[-300:]
        for l in out.splitlines():
            m = re.match(r"^([^/\s]+)/(\S+)\s+(\S+)\s+(\S+)(?:\s+\[upgradable from: ([^\]]+)\])?", l)
            if m:
                pacotes.append({"nome": m.group(1), "arq": m.group(4), "versao": m.group(3), "repo": m.group(2),
                                "atual": m.group(5) or ""})
                if "security" in m.group(2):
                    seg.add(m.group(1))
        reinicio = os.path.exists("/var/run/reboot-required")
    else:
        erro = "sem dnf, yum nem apt"
    for p in pacotes:
        p["seguranca"] = p["nome"] in seg
        p["tipo"] = "seguranca" if p["seguranca"] else tipos.get(p["nome"], "")
        if p["nome"] in gravidade:
            p["gravidade"] = gravidade[p["nome"]]
    return {"ferramenta": ferramenta, "total": len(pacotes), "seguranca": sum(1 for p in pacotes if p["seguranca"]),
            "reinicio": reinicio, "erro": erro, "pacotes": pacotes[:600],
            "verificado": datetime.datetime.now().isoformat(timespec="seconds")}


def _permitido(caminho: str) -> bool:
    try:
        pastas = (ler_env().get("LOGS_PASTAS") or LOGS_PASTAS).split()
    except OSError:
        pastas = LOGS_PASTAS.split()
    real = os.path.realpath(caminho)
    return any(real == p.rstrip("/") or real.startswith(p.rstrip("/") + "/") for p in pastas)


def _cauda(caminho: str, n: int) -> list:
    with open(caminho, "rb") as f:
        f.seek(0, 2)
        tam = f.tell()
        f.seek(max(0, tam - min(tam, max(n, 1) * 512 + 4096)))
        bloco = f.read()
    if b"\0" in bloco[-65536:]:
        raise ValueError("ficheiro binario")
    linhas = bloco.decode("utf-8", errors="replace").splitlines()
    if tam > len(bloco) and linhas:
        linhas = linhas[1:]                     # a primeira pode estar cortada
    return [l[:500] for l in linhas[-n:]]


def recolher_ficheiros() -> dict:
    """Ultimas linhas dos ficheiros de log configurados no portal (pasta + ficheiro, aceita * e ?)."""
    out = {}
    for f in _ext["ficheiros_cfg"]:
        fid = str(f.get("id"))
        if not f.get("ficheiro"):                       # pasta: lista os ficheiros (para abrir no portal)
            out[fid] = _listar_pasta(f.get("pasta") or "/")
            continue
        padrao = os.path.join(f.get("pasta") or "/", f.get("ficheiro") or "")
        try:
            cands = [c for c in glob.glob(padrao) if os.path.isfile(c)]
            if not cands:
                out[fid] = {"erro": "nao encontrado: " + padrao}
                continue
            p = max(cands, key=os.path.getmtime)
            if not _permitido(p):
                out[fid] = {"erro": "fora das pastas permitidas no agente (LOGS_PASTAS): " + p}
                continue
            st = os.stat(p)
            out[fid] = {"caminho": p, "tamanho": st.st_size, "alterado": int(st.st_mtime), "correspondencias": len(cands),
                        "linhas": _cauda(p, min(2000, int(f.get("linhas") or 200)))}
        except (OSError, ValueError) as e:
            out[fid] = {"erro": str(e)}
    return out


def _listar_pasta(pasta: str) -> dict:
    if not _permitido(pasta):
        return {"pasta": True, "erro": "fora das pastas permitidas no agente (LOGS_PASTAS): " + pasta}
    if not os.path.isdir(pasta):
        return {"pasta": True, "erro": "pasta nao encontrada: " + pasta}
    lista = []
    try:
        for e in os.scandir(pasta):
            try:
                if e.is_file(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    lista.append({"nome": e.name, "tamanho": st.st_size, "alterado": int(st.st_mtime)})
                elif e.is_dir(follow_symlinks=False):
                    lista.append({"nome": e.name, "pasta": True, "alterado": int(e.stat(follow_symlinks=False).st_mtime)})
            except OSError:
                continue
    except OSError as e:
        return {"pasta": True, "erro": str(e)}
    lista.sort(key=lambda x: (not x.get("pasta"), -x["alterado"]))
    return {"pasta": True, "caminho": pasta, "ficheiros": lista[:1000], "total": len(lista)}


# ============================================================ firewall (firewalld ou ufw) pelo portal
# Leitura sempre; alteracoes so com FIREWALL_GERIR=1 no .env (setup.sh --gerir-firewall). Cada operacao e validada
# e corre como lista de argumentos (sem shell); no firewalld vai para a configuracao permanente e faz reload.
FW_NOME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
FW_PORTA_RE = re.compile(r"^\d{1,5}(-\d{1,5})?/(tcp|udp|sctp|dccp)$")


def fw_backend() -> str:
    if shutil.which("firewall-cmd") and _run(["firewall-cmd", "--state"], timeout=20)[1].strip() == "running":
        return "firewalld"
    if shutil.which("ufw") and "Status: active" in _run(["ufw", "status"], timeout=20)[1]:
        return "ufw"
    if shutil.which("firewall-cmd"):
        return "firewalld-parado"
    if shutil.which("ufw"):
        return "ufw-inativo"
    return ""


def _fw_listar_zona(z: str) -> dict:
    out = _run(["firewall-cmd", f"--zone={z}", "--list-all"], timeout=30)[1]
    d, chave = {"nome": z, "rich": []}, None
    for l in out.splitlines()[1:]:
        if chave == "rich rules" and l.strip().startswith("rule"):
            d["rich"].append(l.strip())
        elif ":" in l:
            k, v = l.strip().split(":", 1)
            chave = k.strip()
            if chave == "rich rules":
                if v.strip():
                    d["rich"].append(v.strip())
            else:
                d[chave] = v.split() if chave in ("interfaces", "sources", "services", "ports", "protocols", "source-ports",
                                                  "icmp-blocks", "forward-ports") else v.strip()
    return d


def fw_estado() -> dict:
    b = fw_backend()
    cfg = ler_env()
    d = {"backend": b, "gerir": cfg.get("FIREWALL_GERIR") == "1", "lido": datetime.datetime.now().isoformat(timespec="seconds")}
    if b == "firewalld":
        d["zona_omissao"] = _cmd("firewall-cmd", "--get-default-zone")
        ativas, z = {}, None
        for l in _cmd("firewall-cmd", "--get-active-zones").splitlines():
            if l and not l[0].isspace():
                z = l.strip()
                ativas[z] = {}
            elif z and ":" in l:
                k, v = l.strip().split(":", 1)
                ativas[z][k.strip()] = v.split()
        d["ativas"] = ativas
        d["zonas_todas"] = _cmd("firewall-cmd", "--get-zones").split()
        d["zonas"] = [_fw_listar_zona(z) for z in sorted(set(ativas) | {d["zona_omissao"]})]
        d["servicos"] = _cmd("firewall-cmd", "--get-services").split()
        ips = []
        for n in _cmd("firewall-cmd", "--get-ipsets").split():
            info = _run(["firewall-cmd", f"--info-ipset={n}"], timeout=30)[1]
            tipo = next((l.split(":", 1)[1].strip() for l in info.splitlines() if l.strip().startswith("type:")), "")
            ent = next((l.split(":", 1)[1].split() for l in info.splitlines() if l.strip().startswith("entries:")), [])
            ips.append({"nome": n, "tipo": tipo, "entradas": ent[:1000], "total": len(ent)})
        d["ipsets"] = ips
    elif b == "ufw":
        v = _run(["ufw", "status", "verbose"], timeout=30)[1]
        d["politica"] = next((l.split(":", 1)[1].strip() for l in v.splitlines() if l.startswith("Default:")), "")
        regras = []
        for l in _run(["ufw", "status", "numbered"], timeout=30)[1].splitlines():
            m = re.match(r"^\[\s*(\d+)\]\s+(.+?)\s{2,}(ALLOW|DENY|REJECT|LIMIT)( IN| OUT|)\s+(.+)$", l)
            if m:
                de, _, com = m.group(5).partition("#")
                regras.append({"n": int(m.group(1)), "para": m.group(2).strip(), "acao": m.group(3), "dir": m.group(4).strip() or "IN",
                               "de": de.strip(), "comentario": com.strip()})
        d["regras"] = regras
    return d


def _ip_ok(x: str) -> bool:
    import ipaddress  # noqa: PLC0415
    try:
        ipaddress.ip_network(x, strict=False)
        return True
    except ValueError:
        return False


def _fw_args(op: dict) -> list:
    """Operacao -> argumentos do firewall-cmd/ufw (ValueError se invalida)."""
    t = op.get("op", "")
    z, n = str(op.get("zona") or ""), str(op.get("nome") or "")

    def nome(x, o="nome"):
        if not FW_NOME_RE.match(x):
            raise ValueError(f"{o} invalido: {x!r}")
        return x
    if t in ("porta_add", "porta_del"):
        p = str(op.get("porta") or "")
        if not FW_PORTA_RE.match(p):
            raise ValueError("porta invalida (ex. 8795/tcp ou 6000-6010/tcp)")
        return ["firewall-cmd", "--permanent", f"--zone={nome(z, 'zona')}", ("--add-port=" if t == "porta_add" else "--remove-port=") + p]
    if t in ("servico_add", "servico_del"):
        return ["firewall-cmd", "--permanent", f"--zone={nome(z, 'zona')}",
                ("--add-service=" if t == "servico_add" else "--remove-service=") + nome(str(op.get("servico") or ""), "servico")]
    if t in ("rich_add", "rich_del"):
        r = str(op.get("regra") or "").strip()
        if not r.startswith("rule ") or len(r) > 600 or "\n" in r:
            raise ValueError("regra rica invalida (comeca por 'rule ')")
        return ["firewall-cmd", "--permanent", f"--zone={nome(z, 'zona')}", ("--add-rich-rule=" if t == "rich_add" else "--remove-rich-rule=") + r]
    if t in ("fonte_add", "fonte_del"):
        f = str(op.get("fonte") or "")
        if f.startswith("ipset:"):
            nome(f[6:], "ipset")
        elif not _ip_ok(f):
            raise ValueError("fonte invalida (IP, rede CIDR ou ipset:NOME)")
        return ["firewall-cmd", "--permanent", f"--zone={nome(z, 'zona')}", ("--add-source=" if t == "fonte_add" else "--remove-source=") + f]
    if t == "ipset_novo":
        tipo = str(op.get("tipo") or "hash:net")
        if tipo not in ("hash:ip", "hash:net"):
            raise ValueError("tipo de ipset invalido")
        a = ["firewall-cmd", "--permanent", f"--new-ipset={nome(n, 'ipset')}", f"--type={tipo}"]
        return a + (["--option=family=inet6"] if op.get("ipv6") else [])
    if t == "ipset_apagar":
        return ["firewall-cmd", "--permanent", f"--delete-ipset={nome(n, 'ipset')}"]
    if t in ("entrada_add", "entrada_del"):
        e = str(op.get("entrada") or "")
        if not _ip_ok(e):
            raise ValueError("entrada invalida (IP ou rede CIDR)")
        return ["firewall-cmd", "--permanent", f"--ipset={nome(n, 'ipset')}", ("--add-entry=" if t == "entrada_add" else "--remove-entry=") + e]
    if t == "ufw_regra":
        acao = str(op.get("acao") or "allow")
        if acao not in ("allow", "deny", "reject", "limit"):
            raise ValueError("acao invalida")
        a = ["ufw", acao]
        de = str(op.get("de") or "")
        if de and not _ip_ok(de):
            raise ValueError("origem invalida")
        porta = str(op.get("porta") or "")
        if not re.match(r"^\d{1,5}(:\d{1,5})?$", porta):
            raise ValueError("porta invalida (ex. 8795 ou 6000:6010)")
        proto = str(op.get("proto") or "tcp")
        if proto not in ("tcp", "udp"):
            raise ValueError("protocolo invalido")
        a += ["from", de or "any", "to", "any", "port", porta, "proto", proto]
        coment = re.sub(r"[^\w .:/-]", "", str(op.get("comentario") or ""))[:60]
        return a + (["comment", coment] if coment else [])
    if t == "ufw_apagar":
        num = int(op.get("numero") or 0)
        if num < 1:
            raise ValueError("numero de regra invalido")
        return ["ufw", "--force", "delete", str(num)]
    raise ValueError("operacao desconhecida: " + t)


def fw_reiniciar(cfg: dict, cmd_id, svc: str) -> None:
    """Reinicia o servico da firewall depois das alteracoes (ipsets novos so ficam ativos de certeza assim)
    e espera que volte a responder."""
    if svc == "ufw":
        rc, out, err = _run(["ufw", "reload"], timeout=120)
        _cmd_post(cfg, cmd_id, ["$ ufw reload", f"  {(out + err).strip() or 'ok'}"])
        return
    rc, out, err = _run(["systemctl", "restart", "firewalld"], timeout=180)
    _cmd_post(cfg, cmd_id, ["$ systemctl restart firewalld", f"  {(out + err).strip() or ('ok' if rc == 0 else 'erro')}"])
    if rc != 0:
        raise RuntimeError("restart do firewalld falhou: " + (out + err).strip())
    for _ in range(30):
        if _run(["firewall-cmd", "--state"], timeout=20)[1].strip() == "running":
            _cmd_post(cfg, cmd_id, ["  firewalld ativo (running)"])
            return
        time.sleep(1)
    raise RuntimeError("o firewalld nao voltou a ficar ativo depois do restart")


def firewall_comando(cfg: dict, cmd_id, args: dict) -> None:
    try:
        ops = args.get("ops") or []
        if ops:
            if cfg.get("FIREWALL_GERIR") != "1":
                raise PermissionError("este agente nao permite alterar a firewall pelo portal: no servidor, "
                                      "sudo agente-portal configurar --gerir-firewall (ou setup.sh --gerir-firewall)")
            precisa_reload = usa_ufw = False
            for op in ops:
                if op.get("op") == "reiniciar":                 # restart pedido no portal (sem alteracoes)
                    b = fw_backend()
                    fw_reiniciar(cfg, cmd_id, "ufw" if b.startswith("ufw") else "firewalld")
                    continue
                a = _fw_args(op)
                rc, out, err = _run(a, timeout=120)
                txt = (out + err).strip()
                _cmd_post(cfg, cmd_id, [f"$ {' '.join(a)}", f"  {txt or ('ok' if rc == 0 else 'erro')}"])
                if rc != 0 and "ALREADY_ENABLED" not in txt and "NOT_ENABLED" not in txt:
                    raise RuntimeError(f"falhou (codigo {rc}): {txt}")
                precisa_reload = precisa_reload or a[0] == "firewall-cmd"
                usa_ufw = usa_ufw or a[0] == "ufw"
            if precisa_reload:
                rc, out, err = _run(["firewall-cmd", "--reload"], timeout=120)
                _cmd_post(cfg, cmd_id, ["$ firewall-cmd --reload", f"  {(out + err).strip() or 'ok'}"])
                if rc != 0:
                    raise RuntimeError("reload falhou: " + (out + err).strip())
                fw_reiniciar(cfg, cmd_id, "firewalld")
            if usa_ufw:
                fw_reiniciar(cfg, cmd_id, "ufw")
        d = fw_estado()
        pedido(cfg, f"comando/{cmd_id}", {"linhas": [f"estado lido ({d['backend'] or 'sem firewalld/ufw'})"], "estado": "OK",
                                          "dados": d}, timeout=60)
    except Exception as e:  # noqa: BLE001
        _cmd_post(cfg, cmd_id, [f"erro: {e}"], "ERRO")


LER_MAX = 10 * 1024 * 1024


def ler_ficheiro(cfg: dict, cmd_id, args: dict) -> None:
    """Comando 'ler': envia ao portal o conteudo de um ficheiro de log (ultimas N linhas, ou ate 10 MB) para o
    visualizador e o download. So dentro de LOGS_PASTAS; ficheiros binarios sao recusados."""
    import gzip  # noqa: PLC0415
    caminho = args.get("caminho") or ""
    try:
        if args.get("listar"):
            d = _listar_pasta(caminho)
            if d.get("erro"):
                raise ValueError(d["erro"])
            pedido(cfg, f"comando/{cmd_id}", {"linhas": [f"pasta {caminho}: {d['total']} entrada(s)"], "estado": "OK", "dados": d}, timeout=60)
            return
        real = os.path.realpath(caminho)
        if not _permitido(real):
            raise ValueError("fora das pastas permitidas no agente (LOGS_PASTAS): " + caminho)
        if not os.path.isfile(real):
            raise ValueError("ficheiro nao encontrado: " + caminho)
        st = os.stat(real)
        n = int(args.get("linhas") or 0)
        with open(real, "rb") as f:
            inicio = max(0, st.st_size - LER_MAX)
            f.seek(inicio)
            dados = f.read(LER_MAX)
        if b"\0" in dados[:65536] or b"\0" in dados[-65536:]:
            raise ValueError("ficheiro binario: nao e um log de texto")
        texto = dados.decode("utf-8", errors="replace")
        linhas = texto.splitlines()
        if inicio and linhas:
            linhas = linhas[1:]
        truncado = bool(inicio)
        if n and len(linhas) > n:
            linhas, truncado = linhas[-n:], True
        corpo = "\n".join(linhas)
        gz = base64.b64encode(gzip.compress(corpo.encode("utf-8"))).decode()
        msg = (f"lido {real}: {len(linhas)} linha(s), {st.st_size // 1024} KB no disco"
               + (" (so a parte final)" if truncado else ""))
        pedido(cfg, f"comando/{cmd_id}", {"linhas": [msg], "estado": "OK",
                                          "dados": {"caminho": real, "tamanho": st.st_size, "alterado": int(st.st_mtime),
                                                    "linhas": len(linhas), "truncado": truncado, "gz": gz}}, timeout=120)
    except Exception as e:  # noqa: BLE001
        _cmd_post(cfg, cmd_id, [f"erro: {e}"], "ERRO")


def recolher_scan() -> dict:
    """Ficheiros de texto em /var/log (ate 3 niveis), sem os rodados nem comprimidos."""
    lista, base = [], "/var/log"
    for raiz, dirs, fichs in os.walk(base):
        prof = raiz[len(base):].count("/")
        if prof >= 3 or "/journal" in raiz:
            dirs[:] = []
        for n in fichs:
            if ROTADO_RE.search(n) or n in ("wtmp", "btmp", "lastlog", "faillog", "utmp") or n.endswith((".journal", ".pid", ".lock")):
                continue
            p = os.path.join(raiz, n)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if not os.path.isfile(p) or os.path.islink(p) or st.st_size == 0:
                continue
            lista.append({"caminho": p, "pasta": raiz, "ficheiro": n, "tamanho": st.st_size, "alterado": int(st.st_mtime)})
            if len(lista) >= 600:
                break
    return {"base": base, "ficheiros": sorted(lista, key=lambda x: x["caminho"])}


RECOLHER = {"processos": recolher_processos, "sistema": recolher_sistema, "logs": recolher_logs,
            "atualizacoes": recolher_atualizacoes, "ficheiros": recolher_ficheiros, "scan": recolher_scan}


def _extra_thread(tipo: str) -> None:
    try:
        _ext["prontos"][tipo] = RECOLHER[tipo]()
    except Exception as e:  # noqa: BLE001
        _ext["prontos"][tipo] = {"erro": str(e)}
    finally:
        _ext["a_correr"].discard(tipo)


def extras(cfg: dict, pedidos=()) -> dict:
    """Os dados extra devidos nesta sincronizacao (e os pedidos pelo portal)."""
    if cfg.get("EXTRAS", "1") == "0":
        return {}
    agora, out = time.time(), {}
    pedidos = set(pedidos or ())
    if "logs" in pedidos:
        pedidos.add("ficheiros")
    for tipo, cada in EXTRAS_CADA.items():
        if tipo in _ext["a_correr"]:
            continue
        if cada is None and tipo not in pedidos:          # so a pedido (scan)
            continue
        if tipo == "ficheiros" and not _ext["ficheiros_cfg"]:
            continue
        if tipo in pedidos or agora - _ext["ultimo"].get(tipo, 0) >= (cada or 0):
            _ext["ultimo"][tipo] = agora
            if tipo == "atualizacoes":                      # lento: segue na sincronizacao seguinte
                _ext["a_correr"].add(tipo)
                threading.Thread(target=_extra_thread, args=(tipo,), daemon=True).start()
                continue
            try:
                out[tipo] = RECOLHER[tipo]()
            except Exception as e:  # noqa: BLE001
                out[tipo] = {"erro": str(e)}
    for tipo in list(_ext["prontos"]):
        out[tipo] = _ext["prontos"].pop(tipo)
    if _ext["a_correr"]:
        out["_a_verificar"] = sorted(_ext["a_correr"])
    return out


# ============================================================ atualizacao pelo portal
def _ler_json(p: str):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _baixar(cfg: dict, caminho: str, timeout: int = 60) -> bytes:
    url = cfg["PORTAL_URL"].rstrip("/") + caminho
    req = urllib.request.Request(url, headers={"User-Agent": f"agente-portal/{VERSAO}"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx(cfg)) as r:
        return r.read()


def atualizar_agente(cfg: dict, alvo: dict, cmd_id=None) -> None:
    """Descarrega o agente-portal.py do portal, confirma o SHA-256 recebido pelo canal autenticado, guarda a
    versao anterior, substitui-se e reinicia (execv; mesmo PID para o systemd). Com cmd_id, cada passo vai
    para o output do comando no portal; depois do reinicio, o processo novo fecha o comando."""
    def passo(texto, estado=None):
        log("atualizacao: " + texto)
        if cmd_id:
            _cmd_post(cfg, cmd_id, [texto], estado)

    eu = os.path.abspath(__file__)
    with open(eu, "rb") as f:
        if hashlib.sha256(f.read()).hexdigest() == alvo.get("sha256"):
            passo(f"o agente ja esta na versao {VERSAO} (igual a do portal)", "OK")
            return
    espera = 0
    while any(j["estado"] == "a correr" for j in JOBS.values()):
        if espera % 300 == 0:
            passo("ha execucoes Oracle a correr neste agente: a atualizacao espera que terminem")
        if espera >= 7200:
            passo("desisti: execucoes Oracle ainda a correr ao fim de 2 h", "ERRO")
            return
        time.sleep(30)
        espera += 30
    reg = {"de": VERSAO, "para": alvo.get("versao"), "quando": datetime.datetime.now().isoformat(timespec="seconds"),
           "erro": "", "cmd": cmd_id, "reportado": False}
    try:
        url = cfg["PORTAL_URL"].rstrip("/") + "/clientes/agente/agente-portal.py"
        passo(f"a descarregar {url}")
        dados = _baixar(cfg, "/clientes/agente/agente-portal.py")
        sha = hashlib.sha256(dados).hexdigest()
        passo(f"descarregado: {len(dados) // 1024} KB, SHA-256 {sha[:16]}...")
        if sha != alvo.get("sha256"):
            raise RuntimeError("o SHA-256 nao corresponde ao indicado pelo portal (ficheiro alterado no caminho?)")
        passo("SHA-256 confirmado (igual ao indicado pelo portal)")
        compile(dados, eu, "exec")
        passo(f"sintaxe verificada com Python {platform.python_version()} ({sys.executable})")
        novo = eu + ".novo"
        with open(novo, "wb") as f:
            f.write(dados)
        os.chmod(novo, 0o755)
        if os.path.exists(eu):
            try:
                shutil.copy2(eu, eu + ".anterior")
                passo(f"versao {VERSAO} guardada em {eu}.anterior")
            except OSError as e:
                passo(f"aviso: nao foi possivel guardar a versao anterior ({e})")
        os.replace(novo, eu)
        passo(f"{eu} substituido pela versao {alvo.get('versao')}")
    except Exception as e:  # noqa: BLE001
        reg["erro"], reg["reportado"] = str(e), True
        passo(f"falhou: {e}", "ERRO")
        with open(ATUALIZACAO, "w") as f:
            json.dump(reg, f)
        return
    with open(ATUALIZACAO, "w") as f:
        json.dump(reg, f)
    passo(f"a reiniciar o agente (PID {os.getpid()})...")
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, eu, "run"])


def _cmd_post(cfg: dict, cmd_id, linhas=None, estado=None) -> bool:
    try:
        pedido(cfg, f"comando/{cmd_id}", {"linhas": linhas or [], "estado": estado}, timeout=15)
        return True
    except Exception as e:  # noqa: BLE001
        log(f"comando {cmd_id}: nao foi possivel enviar ao portal: {e}")
        return False


def _reportar_reinicio(cfg: dict) -> bool:
    """Depois de uma atualizacao pelo portal: o processo novo fecha o comando (OK ou ERRO)."""
    reg = _ler_json(ATUALIZACAO) or {}
    if not reg.get("cmd") or reg.get("reportado"):
        return True
    ok = reg.get("para") == VERSAO
    linhas = [f"agente {VERSAO} a correr (PID {os.getpid()}, Python {platform.python_version()})",
              "atualizacao concluida" if ok else f"esperava a versao {reg.get('para')} e arrancou a {VERSAO}"]
    if _cmd_post(cfg, reg["cmd"], linhas, "OK" if ok else "ERRO"):
        reg["reportado"] = True
        with open(ATUALIZACAO, "w") as f:
            json.dump(reg, f)
        return True
    return False


# ============================================================ upgrade do sistema (pacotes) pedido no portal
PKG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._:~@-]{0,200}$")
ERRO_PKG_RE = re.compile(r"(^E: |^W: |^Error|^ERROR|Error:|^Erro|[Ff]ailed|FAILED|^dpkg: error|Errors were encountered|"
                         r"No package|Problem: |conflicts with|^\s*- nothing provides|Could not get lock|Unable to)")
_upgrade = {"a_correr": None}


def _corre_stream(cfg: dict, cmd_id, cmd: list, env: dict, timeout: int = 4 * 3600) -> int:
    """Corre um comando e envia o output ao portal em direto (lotes a cada ~2 s). Devolve o codigo de saida."""
    _cmd_post(cfg, cmd_id, ["$ " + " ".join(cmd)])
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             env=env, text=True, errors="replace", bufsize=1)
    except OSError as e:
        _cmd_post(cfg, cmd_id, [f"  nao foi possivel correr: {e}"])
        return 127
    lote, t0, ultimo = [], time.time(), time.time()
    for linha in p.stdout:
        lote.append(linha.rstrip("\n")[:2000])
        if len(lote) >= 80 or time.time() - ultimo > 2:
            _cmd_post(cfg, cmd_id, lote)
            lote, ultimo = [], time.time()
        if time.time() - t0 > timeout:
            p.kill()
            lote.append(f"tempo maximo ({timeout // 60} min) esgotado: processo terminado")
            break
    rc = p.wait()
    if lote:
        _cmd_post(cfg, cmd_id, lote)
    return rc


def upgrade_comando(cfg: dict, cmd_id, args: dict) -> None:
    """Upgrade de pacotes (dnf/yum ou apt), sem interacao. modo: todos | seguranca | pacotes. retomar: antes, acaba o
    que ficou a meio (apt: dpkg --configure -a e apt-get -f install; yum: yum-complete-transaction)."""
    modo = args.get("modo") or "todos"
    pacotes = [x for x in (args.get("pacotes") or []) if isinstance(x, str) and PKG_RE.match(x)][:2000]
    env = dict(os.environ, LANG="C", LC_ALL="C", DEBIAN_FRONTEND="noninteractive", NEEDRESTART_MODE="a",
               APT_LISTCHANGES_FRONTEND="none")
    passos = []
    if shutil.which("dnf") or shutil.which("yum"):
        f = "dnf" if shutil.which("dnf") else "yum"
        if args.get("retomar") and f == "yum" and shutil.which("yum-complete-transaction"):
            passos.append(["yum-complete-transaction", "-y", "--cleanup-only"])
        base = [f, "-y", "upgrade"]
        if modo == "seguranca":
            base.append("--security")
        passos.append(base + (pacotes if modo == "pacotes" else []))
        ferramenta = f
    elif shutil.which("apt-get"):
        ferramenta = "apt"
        o = ["-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold", "-o", "DPkg::Lock::Timeout=600"]
        if args.get("retomar"):
            passos += [["dpkg", "--configure", "-a"], ["apt-get", "-y", *o, "-f", "install"]]
        passos.append(["apt-get", "-o", "DPkg::Lock::Timeout=600", "update"])
        if modo == "todos":
            passos.append(["apt-get", "-y", *o, "upgrade"])
        else:                                   # seguranca: o portal envia a lista dos pacotes de seguranca
            passos.append(["apt-get", "-y", *o, "install", "--only-upgrade", *pacotes])
    else:
        _cmd_post(cfg, cmd_id, ["sem dnf, yum nem apt-get neste servidor"], "ERRO")
        return
    if modo in ("pacotes",) and not pacotes or (modo == "seguranca" and ferramenta == "apt" and not pacotes):
        _cmd_post(cfg, cmd_id, ["nenhum pacote valido para atualizar"], "ERRO")
        return
    _upgrade["a_correr"] = cmd_id
    try:
        _cmd_post(cfg, cmd_id, [f"upgrade ({ferramenta}, {modo}{', ' + str(len(pacotes)) + ' pacote(s)' if pacotes else ''})"
                                + (" - retoma" if args.get("retomar") else "")])
        rc = 0
        for cmd in passos:
            rc = _corre_stream(cfg, cmd_id, cmd, env)
            if rc != 0 and cmd[0] not in ("yum-complete-transaction",):
                break
        reinicio = None
        if ferramenta != "apt" and shutil.which("needs-restarting"):
            reinicio = _run(["needs-restarting", "-r"], timeout=120, env=env)[0] == 1
        elif ferramenta == "apt":
            reinicio = os.path.exists("/var/run/reboot-required")
        fim = [f"codigo de saida {rc}"]
        if reinicio:
            fim.append("AVISO: o servidor precisa de ser reiniciado para aplicar todas as atualizacoes")
        pedido(cfg, f"comando/{cmd_id}", {"linhas": fim, "estado": "OK" if rc == 0 else "ERRO",
                                          "dados": {"rc": rc, "reinicio": reinicio, "ferramenta": ferramenta}}, timeout=30)
    except Exception as e:  # noqa: BLE001
        _cmd_post(cfg, cmd_id, [f"erro: {e}"], "ERRO")
    finally:
        _upgrade["a_correr"] = None
    try:                                         # lista de atualizacoes pendentes nova
        sincronizar(cfg, ["atualizacoes"])
    except Exception:  # noqa: BLE001
        pass


def executar_comando(cfg: dict, c: dict) -> None:
    if c.get("tipo") == "upgrade":            # pode demorar: corre a parte (os outros comandos continuam)
        if _upgrade["a_correr"]:
            _cmd_post(cfg, c.get("id"), [f"ja ha um upgrade a correr (pedido #{_upgrade['a_correr']})"], "ERRO")
            return
        threading.Thread(target=upgrade_comando, args=(cfg, c.get("id"), c.get("args") or {}), daemon=True,
                         name=f"upgrade-{c.get('id')}").start()
        return
    if c.get("tipo") == "atualizar":
        atualizar_agente(cfg, c.get("args") or {}, c.get("id"))
    elif c.get("tipo") == "firewall":
        firewall_comando(cfg, c.get("id"), c.get("args") or {})
    elif c.get("tipo") == "ler":
        _cmd_post(cfg, c.get("id"), ["a ler " + str((c.get("args") or {}).get("caminho"))])
        ler_ficheiro(cfg, c.get("id"), c.get("args") or {})
    else:
        _cmd_post(cfg, c.get("id"), [f"comando desconhecido nesta versao ({VERSAO}): {c.get('tipo')}"], "ERRO")


def ciclo_comandos() -> None:
    """Consulta o portal a cada COMANDOS_CADA s (5): comandos (atualizar) e pedidos de dados ficam quase
    imediatos, sem abrir portas no servidor."""
    reportado = False
    while True:
        try:
            cfg = ler_env()
            time.sleep(max(2, int(cfg.get("COMANDOS_CADA") or 5)))
            if not reportado:
                reportado = _reportar_reinicio(cfg)
            r = pedido(cfg, "comandos", {}, timeout=15)
            if r.get("pedidos"):
                r2 = sincronizar(cfg, r["pedidos"])
                if r2.get("pedidos"):               # ex.: ficheiro de log novo -> envia ja as linhas
                    sincronizar(cfg, r2["pedidos"])
            for c in r.get("comandos") or []:
                executar_comando(cfg, c)
        except Exception:  # noqa: BLE001 - portal em baixo: o ciclo principal regista as falhas
            time.sleep(5)


_sync_trava = threading.Lock()


def sincronizar(cfg: dict, pedidos=()) -> dict:
    """Sincronizacao (info + dados devidos/pedidos). Aplica a lista de ficheiros de log que o portal devolve:
    se mudou (ficheiro novo), acrescenta 'ficheiros' aos pedidos para enviar ja as linhas."""
    with _sync_trava:
        r = pedido(cfg, "sync", {"info": recolher(cfg), "extras": extras(cfg, pedidos)})
        if isinstance(r.get("intervalos"), dict):          # Gerir clientes -> Recolha de dados
            for k, v in r["intervalos"].items():
                if k in EXTRAS_CADA and EXTRAS_CADA[k] is not None and isinstance(v, int):
                    EXTRAS_CADA[k] = max(0 if k == "processos" else 60, v)
        if isinstance(r.get("servicos"), list) and r["servicos"] != (_ler_json(SERVICOS_F) or []):
            try:
                with open(SERVICOS_F, "w") as fh:                # proxima sincronizacao ja leva o estado
                    json.dump([x for x in r["servicos"] if isinstance(x, str) and SERV_RE.match(x)][:60], fh)
            except OSError:
                pass
        if isinstance(r.get("ficheiros_log"), list):
            novos = r["ficheiros_log"] != _ext["ficheiros_cfg"]
            _ext["ficheiros_cfg"] = r["ficheiros_log"]
            if novos and r["ficheiros_log"] and "ficheiros" not in (pedidos or ()):
                r.setdefault("pedidos", []).append("ficheiros")
        return r


# ============================================================ fila local (portal indisponivel)
_trava = threading.Lock()


def enfileirar(ev: dict) -> None:
    with _trava:
        fd = os.open(FILA, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(ev) + "\n")


def esvaziar_fila(cfg: dict) -> int:
    with _trava:
        if not os.path.exists(FILA):
            return 0
        with open(FILA) as f:
            evs = [json.loads(l) for l in f if l.strip()][-1000:]
        os.remove(FILA)
    if not evs:
        return 0
    try:
        pedido(cfg, "evento", {"eventos": evs})
        return len(evs)
    except Exception:
        for e in evs:
            enfileirar(e)
        raise


def evento(regra: str, estado: str, mensagem: str, origem: str = "comando", linha: str = "") -> dict:
    return {"regra": regra[:40], "estado": estado if estado in ESTADOS else "info", "mensagem": mensagem[:2000],
            "linha": linha[:2000], "origem": origem, "quando": datetime.datetime.now().isoformat(timespec="seconds")}


# ============================================================ logs das regras
def ultima_linha(caminho: str) -> str:
    with open(caminho, "rb") as f:
        f.seek(0, 2)
        tam = f.tell()
        f.seek(max(0, tam - 8192))
        linhas = [l for l in f.read().decode(errors="replace").splitlines() if l.strip()]
    return linhas[-1].strip() if linhas else ""


def estado_linha(linha: str, regra: dict) -> str:
    for est, chave in (("erro", "padrao_erro"), ("aviso", "padrao_aviso"), ("ok", "padrao_ok")):
        p = regra.get(chave)
        if p:
            try:
                if re.search(p, linha, re.I):
                    return est
            except re.error:
                pass
    if re.search(r"ORA-\d+|RMAN-\d+|\bERRO|\bERROR|\bFAIL|\bFALH", linha, re.I):
        return "erro"
    if re.search(r"\bWARN|\bAVISO", linha, re.I):
        return "aviso"
    return "ok" if regra.get("padrao_ok") is None else "info"


def verificar_logs(regras: list, mem: dict) -> list:
    evs = []
    for r in regras:
        cam = r.get("log")
        if not cam or not ID_RE.match(r.get("id", "")):
            continue
        try:
            st = os.stat(cam)
            assin = f"{st.st_mtime_ns}:{st.st_size}"
            if mem.get(r["id"], {}).get("assin") == assin:
                continue
            l = ultima_linha(cam)
        except OSError as e:
            if mem.get(r["id"], {}).get("erro") != str(e):
                mem[r["id"]] = {"erro": str(e)}
                evs.append(evento(r["id"], "aviso", f"Não consegui ler o log {cam}: {e.strerror}", "log"))
            continue
        ant = mem.get(r["id"], {}).get("linha")
        mem[r["id"]] = {"assin": assin, "linha": l}
        if l and l != ant and ant is not None:
            evs.append(evento(r["id"], estado_linha(l, r), l, "log", l))
        elif ant is None:                      # primeira leitura: so memoriza (nao notifica o passado)
            pass
    return evs


# ============================================================ socket local
def servidor_socket(cfg: dict) -> None:
    d = os.path.dirname(SOCKET)
    os.makedirs(d, exist_ok=True)
    try:
        os.unlink(SOCKET)
    except FileNotFoundError:
        pass
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(SOCKET)
    try:
        gid = grp.getgrnam(GRUPO).gr_gid
        os.chown(d, 0, gid)
        os.chmod(d, 0o750)
        os.chown(SOCKET, 0, gid)
    except KeyError:
        pass
    os.chmod(SOCKET, 0o660)
    s.listen(16)

    def atender(c):
        with c:
            try:
                c.settimeout(5)
                dados = b""
                while not dados.endswith(b"\n") and len(dados) < 8192:
                    b = c.recv(4096)
                    if not b:
                        break
                    dados += b
                m = json.loads(dados.decode())
                if not ID_RE.match(m.get("regra", "")):
                    raise ValueError("id invalido")
                ev = evento(m["regra"], m.get("estado", "info"), str(m.get("mensagem", "")), "comando")
                try:
                    pedido(ler_env(), "evento", {"eventos": [ev]}, timeout=10)
                    c.sendall(b"ok\n")
                except Exception as e:  # noqa: BLE001
                    enfileirar(ev)
                    c.sendall(f"fila: {e}\n".encode())
            except Exception as e:  # noqa: BLE001
                try:
                    c.sendall(f"erro: {e}\n".encode())
                except OSError:
                    pass

    while True:
        c, _ = s.accept()
        threading.Thread(target=atender, args=(c,), daemon=True).start()


# ============================================================ servico Oracle (pedidos do portal)
RPC = {"ativo": False, "erro": "", "pedidos": 0, "inicio": None, "servico": False}
SESSOES = {}           # id -> {"conn", "ultimo", "trava"}
JOBS = {}              # id -> {"estado", "erro", "saida", "inicio", "fim", "sid", "serial"}
_rpc_trava = threading.Lock()
_vistas = {}           # assinaturas ja usadas (5 min): um pedido nao pode ser repetido
IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")
_ora = {"mod": None, "erro": "", "init": False}


def oracledb_mod():
    """O modulo python-oracledb (None se nao estiver instalado). Com ORACLE_LIB usa o modo thick
    (preciso para bases 11g; a 12.1+ funciona no modo thin, sem cliente Oracle)."""
    if _ora["mod"] is None and not _ora["erro"]:
        try:
            import oracledb  # noqa: PLC0415
            _ora["mod"] = oracledb
        except ImportError as e:
            _ora["erro"] = str(e)
    m = _ora["mod"]
    if m is not None and not _ora["init"]:
        _ora["init"] = True
        try:
            lib = ler_env().get("ORACLE_LIB", "")
        except OSError:
            lib = ""
        if lib:
            try:
                m.init_oracle_client(lib_dir=lib)
            except Exception as e:  # noqa: BLE001
                _ora["erro"] = "init_oracle_client: %s" % e
    return m


def tls_fp() -> str:
    try:
        with open(TLS_CRT) as f:
            der = ssl.PEM_cert_to_DER_cert(f.read())
        return hashlib.sha256(der).hexdigest()
    except (OSError, ValueError):
        return ""


def tls_garantir() -> str:
    """Certificado proprio do servico (10 anos). O portal nao o valida por uma CA: confirma a impressao
    digital, que recebe do agente pelo canal autenticado (sincronizacao com o token)."""
    if not (os.path.exists(TLS_CRT) and os.path.exists(TLS_KEY)):
        nome = socket.getfqdn() or "agente-portal"
        r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
                            "-subj", "/CN=" + nome[:60], "-keyout", TLS_KEY, "-out", TLS_CRT],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        if r.returncode != 0:
            raise RuntimeError("openssl falhou: " + r.stdout.strip()[-300:])
        os.chmod(TLS_KEY, 0o600)
        os.chmod(TLS_CRT, 0o644)
    return tls_fp()


def _sonda_rpc(porta: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", porta), timeout=3):
            return True
    except OSError:
        return False


def rpc_info(cfg: dict):
    if cfg.get("ORACLE_RPC") != "1":
        return None
    m = oracledb_mod()
    porta = int(cfg.get("RPC_PORTA") or RPC_PORTA)
    ativo, erro, sonda = RPC["ativo"], RPC["erro"] or _ora["erro"], False
    if not RPC["servico"]:              # outro processo (agente-portal testar): ve se o servico do agente esta a escutar
        sonda, ativo = True, _sonda_rpc(porta)
        erro = "" if ativo else (f"nada a escutar na porta {porta}: o servico do agente nao tem o servico Oracle a correr "
                                 "(systemctl restart agente-portal)")
    elif not ativo and not erro and RPC["inicio"] is None:
        erro = ("ativado depois de o agente arrancar (ou ainda a iniciar): reinicie o servico (systemctl restart agente-portal)"
                if not RPC.get("thread") else "a iniciar")
    return {"ativo": ativo, "erro": erro, "porta": porta, "servico": RPC["servico"], "sonda": sonda,
            "fp": tls_fp(), "oracledb": getattr(m, "__version__", "?") if m else None,
            "modo": ("thick" if cfg.get("ORACLE_LIB") else "thin") if m else None,
            "sessoes": len(SESSOES), "jobs": sum(1 for j in JOBS.values() if j["estado"] == "a correr"),
            "pedidos": RPC["pedidos"], "python": platform.python_version(),
            "origens": [x for x in re.split(r"[,\s]+", cfg.get("RPC_ORIGENS", "")) if x]}


def _enc(v):
    """Valores do Oracle -> JSON (o portal volta a converter datas e decimais)."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, datetime.datetime):
        return {"$dt": v.isoformat()}
    if isinstance(v, datetime.date):
        return {"$d": v.isoformat()}
    if isinstance(v, datetime.timedelta):
        return {"$td": v.total_seconds()}
    if isinstance(v, decimal.Decimal):
        return {"$dec": str(v)}
    if isinstance(v, (bytes, bytearray)):
        return {"$b64": base64.b64encode(bytes(v)).decode()}
    if hasattr(v, "read"):
        return _enc(v.read())
    return str(v)


def _ligar(db: dict):
    m = oracledb_mod()
    if m is None:
        raise RuntimeError("python-oracledb nao instalado no agente (%s)" % _ora["erro"])
    if db.get("service_name"):
        dsn = m.makedsn(db["host"], int(db["port"]), service_name=db["service_name"])
    else:
        dsn = m.makedsn(db["host"], int(db["port"]), sid=db["sid"])
    modo = m.AUTH_MODE_SYSDBA if db.get("role") == "SYSDBA" else m.AUTH_MODE_DEFAULT
    conn = m.connect(user=db["username"], password=db["password"], dsn=dsn, mode=modo)
    pdb = db.get("pdb")
    if pdb:
        if not IDENT_RE.match(pdb):
            conn.close()
            raise ValueError("PDB invalida")
        with conn.cursor() as cur:
            cur.execute("ALTER SESSION SET CONTAINER = " + pdb)
    return conn


def _dbms_output(conn) -> list:
    linhas, bloco = [], 1000
    with conn.cursor() as cur:
        lv, nv = cur.arrayvar(str, bloco, 32767), cur.var(int)
        while True:
            nv.setvalue(0, bloco)
            cur.callproc("dbms_output.get_lines", (lv, nv))
            n = nv.getvalue()
            linhas.extend((l or "") for l in lv.getvalue()[:n])
            if n < bloco:
                break
    return linhas


def _binds(cur, b):
    """Valores simples; {"$var": "str"|"int", "size": N} cria uma variavel de saida."""
    outs = {}

    def conv(k, v):
        if isinstance(v, dict) and "$var" in v:
            var = cur.var(str, int(v.get("size") or 4000)) if v["$var"] == "str" else cur.var(int)
            outs[k] = var
            return var
        if isinstance(v, dict) and "$dt" in v:
            return datetime.datetime.fromisoformat(v["$dt"]) if hasattr(datetime.datetime, "fromisoformat") else v["$dt"]
        return v
    if isinstance(b, list):
        return [conv(i, v) for i, v in enumerate(b)], outs
    if isinstance(b, dict):
        return {k: conv(k, v) for k, v in b.items()}, outs
    return [], outs


def _sessao(sid: str) -> dict:
    s = SESSOES.get(sid)
    if not s:
        raise LookupError("sessao inexistente ou expirada")
    s["ultimo"] = time.time()
    return s


def _job_correr(jid: str, conn, bloco: str) -> None:
    j = JOBS[jid]
    try:
        with conn.cursor() as cur:
            cur.execute(bloco)
        j["estado"] = "ok"
    except Exception as e:  # noqa: BLE001
        j["estado"], j["erro"] = "erro", str(e)
    try:
        j["saida"] = _dbms_output(conn)
    except Exception:  # noqa: BLE001
        pass
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass
    j["fim"] = time.time()
    log("job %s terminou: %s%s" % (jid, j["estado"], (" (" + j["erro"][:120] + ")") if j["erro"] else ""))


def rpc_tratar(caminho: str, d: dict) -> dict:
    if caminho == "/rpc/ping":
        m = oracledb_mod()
        return {"versao": VERSAO, "oracledb": getattr(m, "__version__", "?") if m else None}
    if caminho == "/rpc/abrir":
        conn = _ligar(d["db"])
        sid = secrets.token_hex(12)
        SESSOES[sid] = {"conn": conn, "ultimo": time.time(), "trava": threading.Lock()}
        return {"sessao": sid, "versao": getattr(conn, "version", None)}
    if caminho == "/rpc/fechar":
        s = SESSOES.pop(d.get("sessao", ""), None)
        if s:
            try:
                s["conn"].close()
            except Exception:  # noqa: BLE001
                pass
        return {}
    if caminho == "/rpc/sql":
        s = _sessao(d["sessao"])
        with s["trava"], s["conn"].cursor() as cur:
            binds, outs = _binds(cur, d.get("binds"))
            cur.execute(d["sql"], binds)
            cols, linhas = None, []
            if cur.description:
                cols = [c[0] for c in cur.description]
                maximo = int(d.get("max_linhas") or 100000)
                for r in cur:
                    linhas.append([_enc(v) for v in r])
                    if len(linhas) >= maximo:
                        break
            return {"cols": cols, "linhas": linhas, "rowcount": getattr(cur, "rowcount", -1),
                    "outs": {str(k): _enc(v.getvalue()) for k, v in outs.items()}}
    if caminho == "/rpc/proc":
        s = _sessao(d["sessao"])
        with s["trava"], s["conn"].cursor() as cur:
            cur.callproc(d["nome"], d.get("args") or [])
        return {}
    if caminho == "/rpc/dbms_output":
        s = _sessao(d["sessao"])
        with s["trava"]:
            return {"linhas": _dbms_output(s["conn"])}
    if caminho == "/rpc/job/iniciar":
        conn = _ligar(d["db"])
        try:
            sid = serial = None
            if d.get("sql_sessao"):
                with conn.cursor() as cur:
                    cur.execute(d["sql_sessao"])
                    r = cur.fetchone()
                    sid, serial = (r[0], r[1]) if r else (None, None)
            with conn.cursor() as cur:
                cur.callproc("dbms_output.enable", [None])
        except Exception:
            conn.close()
            raise
        jid = secrets.token_hex(8)
        JOBS[jid] = {"estado": "a correr", "erro": "", "saida": None, "inicio": time.time(), "fim": None,
                     "sid": sid, "serial": serial, "ref": str(d.get("ref", ""))[:40]}
        threading.Thread(target=_job_correr, args=(jid, conn, d["bloco"]), daemon=True).start()
        log("job %s iniciado (%s, sessao %s,%s)" % (jid, d.get("ref", ""), sid, serial))
        return {"job": jid, "sid": sid, "serial": serial}
    if caminho == "/rpc/job/estado":
        j = JOBS.get(d.get("job", ""))
        if not j:
            raise LookupError("execucao desconhecida (o agente foi reiniciado?)")
        return {"estado": j["estado"], "erro": j["erro"], "inicio": j["inicio"], "fim": j["fim"],
                "sid": j["sid"], "serial": j["serial"], "saida": j["saida"] if j["estado"] != "a correr" else None}
    if caminho == "/rpc/job/esquecer":
        j = JOBS.get(d.get("job", ""))
        if j and j["estado"] != "a correr":
            JOBS.pop(d["job"], None)
        return {}
    raise LookupError("pedido desconhecido: " + caminho)


def _origem_ok(ip: str, origens: list) -> bool:
    """IPs ou redes (CIDR, ex. 192.0.2.0/24) autorizados a fazer pedidos ao servico Oracle."""
    import ipaddress  # noqa: PLC0415
    try:
        a = ipaddress.ip_address(ip)
        if getattr(a, "ipv4_mapped", None):
            a = a.ipv4_mapped
    except ValueError:
        return False
    for o in origens:
        try:
            if a in ipaddress.ip_network(o, strict=False):
                return True
        except ValueError:
            continue
    return False


def _autenticar(cab, caminho: str, corpo: bytes, ip: str) -> str:
    cfg = ler_env()
    origens = [x.strip() for x in re.split(r"[,\s]+", cfg.get("RPC_ORIGENS", "")) if x.strip()]
    if origens and not _origem_ok(ip, origens):
        return "origem nao autorizada: " + ip
    ts, nonce, assin = cab.get("X-Ts", ""), cab.get("X-Nonce", ""), cab.get("X-Assinatura", "")
    if not ts.isdigit():
        return "hora do pedido invalida"
    dif = int(time.time()) - int(ts)
    if abs(dif) > 300:
        return (f"hora do pedido invalida: o relogio deste servidor esta {abs(dif)} s "
                f"{'adiantado' if dif > 0 else 'atrasado'} em relacao ao portal (maximo 300 s; ative o NTP: "
                "timedatectl set-ntp true / chronyc tracking)")
    if not re.match(r"^[0-9a-f]{16,64}$", nonce):
        return "pedido sem nonce"
    esperado = hmac.new(cfg["TOKEN"].encode(), ("%s\n%s\n%s\n" % (ts, nonce, caminho)).encode() + corpo,
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(esperado, assin):
        return "assinatura invalida (token diferente?)"
    with _rpc_trava:
        agora = time.time()
        for k in [k for k, t in _vistas.items() if agora - t > 600]:
            del _vistas[k]
        if assin in _vistas:
            return "pedido repetido"
        _vistas[assin] = agora
    return ""


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "agente-portal/" + VERSAO

    def log_message(self, *_a):
        pass

    def _resp(self, codigo: int, dados: dict) -> None:
        b = json.dumps(dados).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._resp(405, {"erro": "use POST"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 20 * 1024 * 1024:
                return self._resp(413, {"erro": "pedido demasiado grande"})
            corpo = self.rfile.read(n)
            erro = _autenticar(self.headers, self.path, corpo, self.client_address[0])
            if erro:
                log("rpc recusado de %s: %s" % (self.client_address[0], erro))
                return self._resp(401, {"erro": erro})
            RPC["pedidos"] += 1
            d = json.loads(corpo.decode() or "{}")
        except Exception as e:  # noqa: BLE001
            return self._resp(400, {"erro": str(e)})
        try:
            self._resp(200, rpc_tratar(self.path, d))
        except LookupError as e:
            self._resp(404, {"erro": str(e)})
        except Exception as e:  # noqa: BLE001 - erros do Oracle (ORA-...) e outros
            m = _ora["mod"]
            oracle = m is not None and isinstance(e, m.Error)
            self._resp(409 if oracle else 500, {"erro": str(e), "oracle": oracle})


class _Servidor(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    ctx = None

    def finish_request(self, request, client_address):
        request.settimeout(30)
        try:
            request = self.ctx.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):
            return
        request.settimeout(120)                 # leitura/escrita; o tempo do Oracle nao conta (pedidos longos ok)
        self.RequestHandlerClass(request, client_address, self)

    def handle_error(self, request, client_address):
        e = sys.exc_info()[1]
        if not isinstance(e, (OSError, ssl.SSLError)):        # ligacoes fechadas pelo portal: normal
            log("rpc: erro com %s: %r" % (client_address[0], e))


def _limpeza() -> None:
    while True:
        time.sleep(60)
        agora = time.time()
        for k, s in list(SESSOES.items()):
            if agora - s["ultimo"] > 900 and not s["trava"].locked():
                SESSOES.pop(k, None)
                try:
                    s["conn"].close()
                except Exception:  # noqa: BLE001
                    pass
        for k, j in list(JOBS.items()):
            if j["fim"] and agora - j["fim"] > 86400:
                JOBS.pop(k, None)


def servidor_rpc(cfg: dict) -> None:
    try:
        tls_garantir()
        if oracledb_mod() is None:
            log("servico Oracle: python-oracledb nao instalado (%s); o portal trata dos pedidos" % _ora["erro"])
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(TLS_CRT, TLS_KEY)
        porta = int(cfg.get("RPC_PORTA") or RPC_PORTA)
        srv = _Servidor((cfg.get("RPC_ESCUTA") or "0.0.0.0", porta), _Handler)
        srv.ctx = ctx
        RPC.update(ativo=True, erro="", inicio=time.time())
        threading.Thread(target=_limpeza, daemon=True).start()
        log("servico Oracle a escutar na porta %s (TLS, impressao %s...)" % (porta, tls_fp()[:16]))
        srv.serve_forever()
    except Exception as e:  # noqa: BLE001
        RPC.update(ativo=False, erro=str(e))
        log("servico Oracle parado: %s" % e)


# ============================================================ comandos
def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def cmd_run(_a):
    cfg = ler_env()
    RPC["servico"] = True
    threading.Thread(target=servidor_socket, args=(cfg,), daemon=True).start()
    if cfg.get("ORACLE_RPC") == "1":
        RPC["thread"] = True
        threading.Thread(target=servidor_rpc, args=(cfg,), daemon=True).start()
    threading.Thread(target=ciclo_comandos, daemon=True).start()
    mem = {}
    try:
        with open(ESTADO) as f:
            mem = json.load(f).get("logs", {})
    except (OSError, ValueError):
        pass
    regras, falhas = [], 0
    _cpu()
    log(f"agente {VERSAO} a correr: portal {cfg['PORTAL_URL']}, id {cfg['CLIENTE_ID']}, intervalo {cfg.get('INTERVALO', 60)} s")
    while True:
        ini = time.time()
        try:
            cfg = ler_env()                    # apanha 'agente-portal intervalo N' sem reiniciar
            evs = verificar_logs(regras, mem)
            for e in evs:
                enfileirar(e)
            r = sincronizar(cfg)
            regras = r.get("regras", [])
            if r.get("pedidos"):                   # o portal pediu dados agora: envia ja
                sincronizar(cfg, r["pedidos"])
            if r.get("atualizar"):                 # depois dos pedidos (o reinicio perdia-os)
                esvaziar_fila(cfg)
                atualizar_agente(cfg, r["atualizar"])
            esvaziar_fila(cfg)
            if falhas:
                log("ligacao ao portal recuperada")
            falhas = 0
        except Exception as e:  # noqa: BLE001
            falhas += 1
            if falhas in (1, 10) or falhas % 60 == 0:
                log(f"falha ao contactar o portal ({falhas}): {e}")
        try:
            with open(ESTADO, "w") as f:
                json.dump({"logs": mem, "ultimo": time.time(), "falhas": falhas}, f)
        except OSError:
            pass
        time.sleep(max(5, int(cfg.get("INTERVALO", 60)) - (time.time() - ini)))


def cmd_configurar(a):
    os.makedirs(BASE, exist_ok=True)
    if not os.path.exists(CHAVE):
        fd = os.open(CHAVE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    cfg = ler_env() if os.path.exists(ENV) else {}
    for k, v in (("PORTAL_URL", a.portal), ("CLIENTE_ID", a.id), ("TOKEN", a.token), ("INTERVALO", a.intervalo),
                 ("UTILIZADOR_SSH", a.utilizador), ("CA_FICHEIRO", a.ca)):
        if v not in (None, ""):
            cfg[k] = str(v)
    if a.inseguro:
        cfg["VERIFICAR_TLS"] = "0"
    if a.oracle:
        cfg["ORACLE_RPC"] = "1"
    if a.sem_oracle:
        cfg["ORACLE_RPC"] = "0"
    if a.gerir_firewall:
        cfg["FIREWALL_GERIR"] = "1"
    if a.sem_gerir_firewall:
        cfg["FIREWALL_GERIR"] = "0"
    for k, v in (("RPC_PORTA", a.porta_rpc), ("RPC_ORIGENS", a.origens), ("ORACLE_LIB", a.oracle_lib)):
        if v is not None:
            cfg[k] = str(v)
    for k in ("PORTAL_URL", "CLIENTE_ID", "TOKEN"):
        if not cfg.get(k):
            sys.exit(f"falta {k} (--portal, --id, --token)")
    cfg.setdefault("INTERVALO", "60")
    cfg.setdefault("VERIFICAR_TLS", "1")
    gravar_env(cfg)
    print(f"configuracao gravada em {ENV} (token cifrado)")


def cmd_intervalo(a):
    if not 10 <= a.segundos <= 3600:
        sys.exit("intervalo entre 10 e 3600 segundos")
    cfg = ler_env()
    cfg["INTERVALO"] = str(a.segundos)
    gravar_env(cfg)
    print(f"intervalo = {a.segundos} s (aplicado no proximo ciclo; o portal mostra-o na proxima sincronizacao)")


def cmd_notificar(a):
    if not ID_RE.match(a.id):
        sys.exit("id invalido (o id da regra, ver no portal)")
    estado, msg = (a.estado, a.mensagem) if a.estado in ESTADOS else ("info", " ".join(filter(None, [a.estado, a.mensagem])))
    if not sys.stdin.isatty() and not msg:
        msg = sys.stdin.read()[-2000:]
    m = json.dumps({"regra": a.id, "estado": estado, "mensagem": msg or ""}) + "\n"
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(15)
        s.connect(SOCKET)
        s.sendall(m.encode())
        r = s.recv(1024).decode().strip()
        s.close()
        print(r)
        sys.exit(0 if r.startswith(("ok", "fila")) else 1)
    except PermissionError:
        sys.exit(f"sem permissao no socket {SOCKET}: acrescente este utilizador ao grupo {GRUPO} "
                 f"(usermod -aG {GRUPO} $USER) e volte a entrar")
    except OSError as e:
        if os.access(ENV, os.R_OK):          # root: envia diretamente (servico parado)
            cfg = ler_env()
            ev = evento(a.id, estado, msg or "", "comando")
            try:
                pedido(cfg, "evento", {"eventos": [ev]})
                print("ok (direto)")
            except Exception as e2:  # noqa: BLE001
                enfileirar(ev)
                print(f"fila: {e2}")
            return
        sys.exit(f"o agente nao esta a correr ({e}): systemctl status agente-portal")


def cmd_testar(_a):
    cfg = ler_env()
    print(f"portal: {cfg['PORTAL_URL']}  id: {cfg['CLIENTE_ID']}  intervalo: {cfg.get('INTERVALO')} s")
    try:
        r = pedido(cfg, "sync", {"info": recolher(cfg), "teste": True})
        print(f"OK: o portal reconhece este cliente como '{r.get('nome')}'; {len(r.get('regras', []))} regra(s)")
        for x in r.get("regras", []):
            print(f"   {x['id']:<14} {x.get('nome', '')}{('  log: ' + x['log']) if x.get('log') else ''}")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"ERRO: {e}")


def cmd_estado(a):
    cfg = ler_env(decifrado=False)
    est = {}
    try:
        with open(ESTADO) as f:
            est = json.load(f)
    except (OSError, ValueError):
        pass
    ult = est.get("ultimo")
    if a.curto:
        print(f"agente {VERSAO} id={cfg.get('CLIENTE_ID')} intervalo={cfg.get('INTERVALO')} "
              f"ultimo={int(time.time() - ult) if ult else '-'}s falhas={est.get('falhas', '-')}")
        return
    print(f"versao       {VERSAO}\nportal       {cfg.get('PORTAL_URL')}\nid           {cfg.get('CLIENTE_ID')}\n"
          f"intervalo    {cfg.get('INTERVALO')} s\ntoken        {'cifrado' if cfg.get('TOKEN', '').startswith('enc:') else 'EM CLARO'}\n"
          f"ultimo ciclo {('ha %d s' % (time.time() - ult)) if ult else '-'} (falhas seguidas: {est.get('falhas', '-')})\n"
          f"socket       {SOCKET} {'(ativo)' if os.path.exists(SOCKET) else '(inexistente)'}\n"
          f"fila         {sum(1 for _ in open(FILA)) if os.path.exists(FILA) else 0} evento(s) por enviar")
    if cfg.get("ORACLE_RPC") == "1":
        m = oracledb_mod()
        print(f"oracle       porta {cfg.get('RPC_PORTA') or RPC_PORTA}, python-oracledb "
              f"{getattr(m, '__version__', '?') if m else 'NAO INSTALADO'} ({'thick: ' + cfg['ORACLE_LIB'] if cfg.get('ORACLE_LIB') else 'thin'}), "
              f"certificado {tls_fp()[:16] or 'em falta'}...")
    else:
        print("oracle       desligado (agente-portal configurar --oracle)")


def cmd_oracle(a):
    """Testa o servico Oracle: python-oracledb, certificado e, com --dsn, uma ligacao."""
    cfg = ler_env()
    m = oracledb_mod()
    print(f"servico      {'ligado' if cfg.get('ORACLE_RPC') == '1' else 'DESLIGADO (configurar --oracle)'}, porta {cfg.get('RPC_PORTA') or RPC_PORTA}")
    print(f"oracledb     {getattr(m, '__version__', '') if m else 'NAO INSTALADO: ' + _ora['erro']}")
    print(f"certificado  {tls_fp() or 'em falta (criado ao arrancar)'}")
    if a.utilizador and m:
        import getpass  # noqa: PLC0415
        host, _, resto = a.dsn.partition(":")
        porta, _, serv = resto.partition("/")
        db = {"host": host, "port": int(porta or 1521), "service_name": serv, "username": a.utilizador,
              "password": getpass.getpass("password: "), "role": "SYSDBA" if a.sysdba else "NORMAL"}
        c = _ligar(db)
        print(f"ligacao      OK (Oracle {c.version})")
        c.close()


def chaves_zabbix(i: dict) -> dict:
    """As metricas do agente com as chaves de um agente Zabbix (o portal usa as mesmas)."""
    m = {"system.hostname": i["hostname"], "system.sw.os": i["so"], "system.uname": i["kernel"], "system.cpu.num": i["cpus"],
         "system.uptime": i["uptime"], "vm.memory.size[total]": i["mem"]["total"], "vm.memory.size[available]": i["mem"]["livre"],
         "vm.memory.utilization": i["mem"]["pct"]}
    for k, v in zip(("avg1", "avg5", "avg15"), i["load"]):
        m[f"system.cpu.load[all,{k}]"] = v
        m[f"system.cpu.load[percpu,{k}]"] = round(v / max(1, i["cpus"] or 1), 2)
    for d in i["discos"]:
        p = d["ponto"]
        m.update({f"vfs.fs.size[{p},total]": d["total"], f"vfs.fs.size[{p},free]": d["livre"],
                  f"vfs.fs.size[{p},used]": d["total"] - d["livre"], f"vfs.fs.size[{p},pused]": d["pct"]})
    return m


def cmd_valor(a):
    """Para um agente Zabbix no mesmo servidor (UserParameter=portal.metrica[*],agente-portal valor "$1")."""
    cfg = {"INTERVALO": 60}
    try:
        cfg = ler_env(decifrado=False)
    except OSError:
        pass
    m = chaves_zabbix(recolher(cfg))
    if a.chave == "todas":
        print(json.dumps(m, indent=1))
    elif a.chave in m:
        print(m[a.chave])
    else:
        sys.exit("chave desconhecida (agente-portal valor todas)")


def main():
    p = argparse.ArgumentParser(prog="agente-portal", description="Agente cliente do portal")
    sp = p.add_subparsers(dest="cmd")
    sp.add_parser("run", help="corre o agente (servico)").set_defaults(f=cmd_run)
    c = sp.add_parser("configurar", help="grava o .env (cifra o token)")
    c.add_argument("--portal"), c.add_argument("--id"), c.add_argument("--token"), c.add_argument("--intervalo", type=int)
    c.add_argument("--utilizador"), c.add_argument("--ca"), c.add_argument("--inseguro", action="store_true")
    c.add_argument("--oracle", action="store_true", help="liga o servico Oracle (pedidos do portal)")
    c.add_argument("--sem-oracle", action="store_true"), c.add_argument("--porta-rpc", type=int)
    c.add_argument("--gerir-firewall", action="store_true", help="permite alterar a firewall (firewalld/ufw) pelo portal")
    c.add_argument("--sem-gerir-firewall", action="store_true")
    c.add_argument("--origens", help="IPs ou redes (CIDR) do portal autorizados (vazio = qualquer, com assinatura)")
    c.add_argument("--oracle-lib", help="pasta das bibliotecas do cliente Oracle (modo thick, bases 11g)")
    c.set_defaults(f=cmd_configurar)
    n = sp.add_parser("notificar", help="envia uma notificacao ao portal: notificar <ID> [ok|aviso|erro|info] mensagem")
    n.add_argument("id"), n.add_argument("estado", nargs="?", default="info"), n.add_argument("mensagem", nargs="?", default="")
    n.set_defaults(f=cmd_notificar)
    sp.add_parser("testar", help="testa a ligacao ao portal").set_defaults(f=cmd_testar)
    e = sp.add_parser("estado", help="estado do agente")
    e.add_argument("--curto", action="store_true"), e.set_defaults(f=cmd_estado)
    i = sp.add_parser("intervalo", help="muda o intervalo de sincronizacao (s)")
    i.add_argument("segundos", type=int), i.set_defaults(f=cmd_intervalo)
    v = sp.add_parser("valor", help="valor de uma metrica com a chave Zabbix (ou 'todas')")
    v.add_argument("chave"), v.set_defaults(f=cmd_valor)
    o = sp.add_parser("oracle", help="testa o servico Oracle (e uma ligacao com --dsn host:porta/servico --utilizador U)")
    o.add_argument("--dsn", default=""), o.add_argument("--utilizador"), o.add_argument("--sysdba", action="store_true")
    o.set_defaults(f=cmd_oracle)
    sp.add_parser("versao").set_defaults(f=lambda _a: print(VERSAO))
    a = p.parse_args()
    if not getattr(a, "f", None):
        p.print_help()
        sys.exit(2)
    a.f(a)


if __name__ == "__main__":
    main()
