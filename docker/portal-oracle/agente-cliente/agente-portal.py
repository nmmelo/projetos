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
import grp
import http.server
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

VERSAO = "1.2.0"
BASE = os.environ.get("AGENTE_DIR", "/opt/agente-portal")
ENV = os.path.join(BASE, ".env")
CHAVE = os.path.join(BASE, ".chave")
FILA = os.path.join(BASE, "fila.jsonl")
ESTADO = os.path.join(BASE, "estado.json")
SOCKET = os.environ.get("AGENTE_SOCKET", "/run/agente-portal/agente.sock")
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
    for k in ("PORTAL_URL", "CLIENTE_ID", "TOKEN", "INTERVALO", "VERIFICAR_TLS", "CA_FICHEIRO", "UTILIZADOR_SSH",
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


# ============================================================ servico Oracle (pedidos do portal)
RPC = {"ativo": False, "erro": "", "pedidos": 0, "inicio": None}
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


def rpc_info(cfg: dict):
    if cfg.get("ORACLE_RPC") != "1":
        return None
    m = oracledb_mod()
    return {"ativo": RPC["ativo"], "erro": RPC["erro"] or _ora["erro"], "porta": int(cfg.get("RPC_PORTA") or RPC_PORTA),
            "fp": tls_fp(), "oracledb": getattr(m, "__version__", "?") if m else None,
            "modo": ("thick" if cfg.get("ORACLE_LIB") else "thin") if m else None,
            "sessoes": len(SESSOES), "jobs": sum(1 for j in JOBS.values() if j["estado"] == "a correr"),
            "pedidos": RPC["pedidos"], "python": platform.python_version()}


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


def _autenticar(cab, caminho: str, corpo: bytes, ip: str) -> str:
    cfg = ler_env()
    origens = [x.strip() for x in re.split(r"[,\s]+", cfg.get("RPC_ORIGENS", "")) if x.strip()]
    if origens and ip not in origens:
        return "origem nao autorizada: " + ip
    ts, nonce, assin = cab.get("X-Ts", ""), cab.get("X-Nonce", ""), cab.get("X-Assinatura", "")
    if not ts.isdigit() or abs(time.time() - int(ts)) > 300:
        return "hora do pedido invalida (relogios do portal e do agente diferentes?)"
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
    threading.Thread(target=servidor_socket, args=(cfg,), daemon=True).start()
    if cfg.get("ORACLE_RPC") == "1":
        threading.Thread(target=servidor_rpc, args=(cfg,), daemon=True).start()
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
    if a.oracle:
        cfg["ORACLE_RPC"] = "1"
    if a.sem_oracle:
        cfg["ORACLE_RPC"] = "0"
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
    c.add_argument("--origens", help="IPs do portal autorizados (vazio = qualquer, com assinatura)")
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
