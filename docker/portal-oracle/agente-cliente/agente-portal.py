#!/usr/bin/env python3
"""Agente cliente do Portal (estilo agente Zabbix).

Corre como servico (systemd) no servidor cliente e, a cada INTERVALO segundos:
  - envia ao portal o estado do servidor (SO, CPU, memoria, discos, uptime);
  - recebe as regras de notificacao (id, nome, ficheiro de log) definidas no portal;
  - le a ultima linha do log de cada regra e, se mudou, envia-a;
  - envia as notificacoes recebidas pelo socket local (de outros scripts).

Outros scripts (rman, shrink, ...) notificam o portal com:
  agente-portal notificar <ID> [ok|aviso|erro|info] "mensagem"

So usa a biblioteca padrao do Python 3 (3.6+). O .env tem os valores sensiveis cifrados com uma
chave local (.chave, so o root le) ligada a esta maquina (/etc/machine-id).

Comandos: run | configurar | notificar | testar | estado | intervalo | versao
"""
import argparse
import base64
import datetime
import grp
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

VERSAO = "1.0.0"
BASE = os.environ.get("AGENTE_DIR", "/opt/agente-portal")
ENV = os.path.join(BASE, ".env")
CHAVE = os.path.join(BASE, ".chave")
FILA = os.path.join(BASE, "fila.jsonl")
ESTADO = os.path.join(BASE, "estado.json")
SOCKET = os.environ.get("AGENTE_SOCKET", "/run/agente-portal/agente.sock")
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
    for k in ("PORTAL_URL", "CLIENTE_ID", "TOKEN", "INTERVALO", "VERIFICAR_TLS", "CA_FICHEIRO", "UTILIZADOR_SSH"):
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
    }


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


# ============================================================ comandos
def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def cmd_run(_a):
    cfg = ler_env()
    threading.Thread(target=servidor_socket, args=(cfg,), daemon=True).start()
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
            r = pedido(cfg, "sync", {"info": recolher(cfg)})
            regras = r.get("regras", [])
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


def main():
    p = argparse.ArgumentParser(prog="agente-portal", description="Agente cliente do portal")
    sp = p.add_subparsers(dest="cmd")
    sp.add_parser("run", help="corre o agente (servico)").set_defaults(f=cmd_run)
    c = sp.add_parser("configurar", help="grava o .env (cifra o token)")
    c.add_argument("--portal"), c.add_argument("--id"), c.add_argument("--token"), c.add_argument("--intervalo", type=int)
    c.add_argument("--utilizador"), c.add_argument("--ca"), c.add_argument("--inseguro", action="store_true")
    c.set_defaults(f=cmd_configurar)
    n = sp.add_parser("notificar", help="envia uma notificacao ao portal: notificar <ID> [ok|aviso|erro|info] mensagem")
    n.add_argument("id"), n.add_argument("estado", nargs="?", default="info"), n.add_argument("mensagem", nargs="?", default="")
    n.set_defaults(f=cmd_notificar)
    sp.add_parser("testar", help="testa a ligacao ao portal").set_defaults(f=cmd_testar)
    e = sp.add_parser("estado", help="estado do agente")
    e.add_argument("--curto", action="store_true"), e.set_defaults(f=cmd_estado)
    i = sp.add_parser("intervalo", help="muda o intervalo de sincronizacao (s)")
    i.add_argument("segundos", type=int), i.set_defaults(f=cmd_intervalo)
    sp.add_parser("versao").set_defaults(f=lambda _a: print(VERSAO))
    a = p.parse_args()
    if not getattr(a, "f", None):
        p.print_help()
        sys.exit(2)
    a.f(a)


if __name__ == "__main__":
    main()
