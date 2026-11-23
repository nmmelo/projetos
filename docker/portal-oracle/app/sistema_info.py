"""Informacao do servidor (CPU, RAM, disco, sistema) e controlo de servicos.

Le diretamente de /proc (sem dependencias). Usado pelo agente (a cada 2 s) e, se o agente estiver
parado, pelo proprio portal (leitura pontual).

Servicos: systemd (systemctl) numa instalacao em servidor, ou supervisord (supervisorctl) no
contentor Docker. Ver: servicos_backend(), estado_servicos(), acao_servico().
"""
import os
import platform
import re
import shutil
import socket
import subprocess
import time

UNIT_RE = re.compile(r"^[A-Za-z0-9@._:-]{1,100}$")
ACOES = ("start", "stop", "restart")
FS_IGNORAR = {"tmpfs", "devtmpfs", "proc", "sysfs", "cgroup", "cgroup2", "overlay", "squashfs", "devpts",
              "securityfs", "pstore", "debugfs", "tracefs", "mqueue", "hugetlbfs", "fusectl", "configfs",
              "bpf", "autofs", "binfmt_misc", "nsfs", "ramfs", "efivarfs", "rpc_pipefs", "fuse.lxcfs"}


# ============================================================================ CPU / RAM / disco
def _ler(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def cpu_tempos() -> tuple[int, int]:
    """(ocioso, total) acumulados em /proc/stat (para calcular a % entre duas leituras)."""
    linha = _ler("/proc/stat").split("\n", 1)[0].split()
    if not linha or linha[0] != "cpu":
        return 0, 0
    v = [int(x) for x in linha[1:]]
    ocioso = v[3] + (v[4] if len(v) > 4 else 0)          # idle + iowait
    return ocioso, sum(v[:8])


def cpu_percent(antes: tuple[int, int], depois: tuple[int, int]) -> float | None:
    d_total = depois[1] - antes[1]
    if d_total <= 0:
        return None
    return round(100.0 * (1 - (depois[0] - antes[0]) / d_total), 1)


def cpu_modelo() -> str:
    m = re.search(r"^model name\s*:\s*(.+)$", _ler("/proc/cpuinfo"), re.M)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else platform.processor() or platform.machine()


def memoria() -> dict:
    info = {}
    for linha in _ler("/proc/meminfo").splitlines():
        k, _, v = linha.partition(":")
        if v.strip():
            info[k] = int(v.split()[0]) * 1024
    total = info.get("MemTotal", 0)
    livre = info.get("MemAvailable", info.get("MemFree", 0))
    sw_total = info.get("SwapTotal", 0)
    sw_livre = info.get("SwapFree", 0)
    return {"total": total, "livre": livre, "usado": total - livre,
            "pct": round(100.0 * (total - livre) / total, 1) if total else None,
            "swap_total": sw_total, "swap_usado": sw_total - sw_livre,
            "swap_pct": round(100.0 * (sw_total - sw_livre) / sw_total, 1) if sw_total else None}


def discos(extra: list[str] | None = None) -> list[dict]:
    """Sistemas de ficheiros reais (sem tmpfs, proc, ...), mais os caminhos em `extra` (ex. pasta do portal)."""
    vistos, out = set(), []
    pontos = []
    for linha in _ler("/proc/mounts").splitlines():
        p = linha.split()
        if len(p) >= 3 and p[2] not in FS_IGNORAR and not p[1].startswith(("/proc", "/sys", "/dev", "/run", "/snap")):
            pontos.append((p[1].replace("\\040", " "), p[0], p[2]))
    if not any(pt == "/" for pt, _, _ in pontos):
        pontos.insert(0, ("/", "", ""))                    # contentor: raiz em overlay
    for pt in extra or []:
        if pt and os.path.exists(pt):
            pontos.append((pt, "", ""))
    for pt, dev, fs in pontos:
        try:
            st = os.statvfs(pt)
        except OSError:
            continue
        chave = (st.f_blocks, st.f_bfree, st.f_bsize)
        if chave in vistos or not st.f_blocks:
            continue
        vistos.add(chave)
        total = st.f_blocks * st.f_frsize
        livre = st.f_bavail * st.f_frsize
        usado = (st.f_blocks - st.f_bfree) * st.f_frsize
        out.append({"ponto": pt, "dispositivo": dev, "fs": fs, "total": total, "livre": livre, "usado": usado,
                    "pct": round(100.0 * usado / (usado + livre), 1) if usado + livre else None})
    return out


def sistema() -> dict:
    os_nome = ""
    m = re.search(r'^PRETTY_NAME="?([^"\n]+)', _ler("/etc/os-release"), re.M)
    if m:
        os_nome = m.group(1)
    up = _ler("/proc/uptime").split()
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)
    try:
        procs = sum(1 for d in os.listdir("/proc") if d.isdigit())
    except OSError:
        procs = None
    return {"host": socket.gethostname(), "os": os_nome or platform.platform(), "kernel": platform.release(),
            "cpu_modelo": cpu_modelo(), "cpus": os.cpu_count(), "uptime": int(float(up[0])) if up else None,
            "load": [round(x, 2) for x in load], "processos": procs, "python": platform.python_version()}


def amostra(cpu_antes: tuple[int, int] | None = None, extra_discos=None) -> tuple[dict, tuple[int, int]]:
    """Uma leitura completa. Sem `cpu_antes`, mede a CPU durante 0,25 s."""
    if cpu_antes is None:
        cpu_antes = cpu_tempos()
        time.sleep(0.25)
    agora = cpu_tempos()
    s = sistema()
    return {"ts": time.time(), "cpu": cpu_percent(cpu_antes, agora), "mem": memoria(),
            "discos": discos(extra_discos), **s}, agora


# ============================================================================ servicos
def servicos_backend() -> str:
    """'systemd', 'supervisor' ou '' (sem gestor de servicos acessivel)."""
    if shutil.which("systemctl") and os.path.isdir("/run/systemd/system"):
        return "systemd"
    if shutil.which("supervisorctl"):
        return "supervisor"
    return ""


def _run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 255, str(exc)


SYSTEMD_PROPS = ("Id", "Description", "LoadState", "ActiveState", "SubState", "MainPID", "MemoryCurrent",
                 "CPUUsageNSec", "ActiveEnterTimestamp", "UnitFileState", "NRestarts", "TasksCurrent")


def estado_servicos(units: list[str]) -> list[dict]:
    units = [u for u in units if UNIT_RE.match(u)]
    backend = servicos_backend()
    if not units or not backend:
        return [{"unit": u, "estado": "desconhecido", "descricao": "sem systemd/supervisord acessivel"} for u in units]
    if backend == "systemd":
        rc, out = _run(["systemctl", "show", *units, "--no-pager", "-p", ",".join(SYSTEMD_PROPS)])
        blocos = [b for b in out.split("\n\n") if b.strip()]
        res = []
        for u, bloco in zip(units, blocos + [""] * (len(units) - len(blocos))):
            d = dict(l.split("=", 1) for l in bloco.splitlines() if "=" in l)
            mem = d.get("MemoryCurrent", "")
            res.append({
                "unit": u, "descricao": d.get("Description", ""),
                "existe": d.get("LoadState") == "loaded",
                "estado": d.get("ActiveState", "desconhecido"), "sub": d.get("SubState", ""),
                "pid": int(d["MainPID"]) if d.get("MainPID", "0").isdigit() and d.get("MainPID") != "0" else None,
                "memoria": int(mem) if mem.isdigit() and int(mem) < 2**60 else None,
                "cpu_ns": int(d["CPUUsageNSec"]) if d.get("CPUUsageNSec", "").isdigit() else None,
                "desde": d.get("ActiveEnterTimestamp") or "", "arranque": d.get("UnitFileState", ""),
                "reinicios": int(d["NRestarts"]) if d.get("NRestarts", "").isdigit() else None,
                "tarefas": int(d["TasksCurrent"]) if d.get("TasksCurrent", "").isdigit() and int(d["TasksCurrent"]) < 2**60 else None,
            })
        return res
    # supervisord: "nome   RUNNING   pid 12, uptime 1:02:03"
    rc, out = _run(["supervisorctl", "status", *units])
    linhas = {l.split()[0]: l for l in out.splitlines() if l.strip()}
    res = []
    for u in units:
        l = linhas.get(u, "")
        p = l.split(None, 2)
        st = p[1] if len(p) > 1 else "desconhecido"
        pid = re.search(r"pid (\d+)", l)
        up = re.search(r"uptime ([\d:]+(?: days?, [\d:]+)?)", l)
        res.append({"unit": u, "descricao": "programa do supervisord", "existe": bool(l) and "no such process" not in l.lower(),
                    "estado": {"RUNNING": "active", "STOPPED": "inactive", "EXITED": "inactive", "FATAL": "failed",
                               "BACKOFF": "failed", "STARTING": "activating", "STOPPING": "deactivating"}.get(st, st.lower()),
                    "sub": st.lower(), "pid": int(pid.group(1)) if pid else None, "memoria": _mem_pid(pid.group(1)) if pid else None,
                    "desde": up.group(1) if up else "", "arranque": "supervisord"})
    return res


def _mem_pid(pid: str) -> int | None:
    m = re.search(r"^VmRSS:\s+(\d+)", _ler(f"/proc/{pid}/status"), re.M)
    return int(m.group(1)) * 1024 if m else None


def acao_servico(unit: str, acao: str) -> tuple[bool, str]:
    """start/stop/restart. Em systemd, sem ser root, usa 'sudo -n' (regra em /etc/sudoers.d/)."""
    if acao not in ACOES or not UNIT_RE.match(unit):
        return False, "Ação ou serviço inválido."
    backend = servicos_backend()
    if backend == "systemd":
        cmd = ["systemctl", acao, unit]
        if os.geteuid() != 0:
            cmd = ["sudo", "-n", *cmd]
        rc, out = _run(cmd, timeout=90)
        if rc != 0 and "password is required" in out.lower():
            out = "sem permissão (sudoers): volte a correr 'instalar.sh --reaplicar' para atualizar a regra. " + out
        return rc == 0, out or ("OK" if rc == 0 else f"código {rc}")
    if backend == "supervisor":
        rc, out = _run(["supervisorctl", acao, unit], timeout=90)
        ok = rc == 0 and "ERROR" not in out
        return ok, out
    return False, "Sem systemd nem supervisord neste servidor."


_AUT = {"t": 0.0, "v": None}


def autorizados() -> set[str] | None:
    """Servicos que este utilizador pode iniciar/parar/reiniciar (sudoers), ou None = sem restricao
    (root, ou supervisord no Docker). Lido de 'sudo -n -l' e guardado 60 s."""
    if servicos_backend() != "systemd" or os.geteuid() == 0:
        return None
    if time.time() - _AUT["t"] < 60 and _AUT["v"] is not None:
        return _AUT["v"]
    rc, out = _run(["sudo", "-n", "-l"], timeout=10)
    ok = set()
    if rc == 0:
        for m in re.finditer(r"systemctl\s+(start|stop|restart)\s+([A-Za-z0-9@._:-]+)", out):
            ok.add(m.group(2))
    _AUT.update(t=time.time(), v=ok)
    return ok


UTEIS = re.compile(r"^(firewalld|ufw|nftables|iptables|chronyd|chrony|ntpd?|crond?|sshd?|rsyslog|auditd|NetworkManager|"
                   r"docker|containerd|postfix|nginx|httpd|apache2|mariadb|mysqld?|postgresql(@.*|-[0-9.]+)?|"
                   r"oracle.*|ohasd|dbora|tuned|fail2ban|zabbix-agent2?|node_exporter|snmpd)$")


def servicos_instalados() -> list[str]:
    """Servicos uteis instalados (firewalld, chronyd, bases de dados, ...), para sugerir nas Definicoes."""
    b = servicos_backend()
    if b == "supervisor":
        rc, out = _run(["supervisorctl", "status"], timeout=10)
        return sorted({l.split()[0] for l in out.splitlines() if l.strip()})
    if b != "systemd":
        return []
    rc, out = _run(["systemctl", "list-unit-files", "--type=service", "--no-legend", "--no-pager"], timeout=15)
    nomes = {l.split()[0].removesuffix(".service") for l in out.splitlines() if l.strip()}
    return sorted(n for n in nomes if UTEIS.match(n) and "@" not in n.rstrip("@"))


def instancia() -> str:
    """Nome da instancia (servico systemd do portal). Definido no servico por PORTAL_INSTANCIA."""
    return os.environ.get("PORTAL_INSTANCIA", "")


def servicos_padrao() -> list[str]:
    """Servicos mostrados por omissao: os do portal (Nginx, portal, agente)."""
    env = os.environ.get("PORTAL_SERVICOS", "").split()
    if env:
        return env
    if servicos_backend() == "supervisor":
        return ["nginx", "gunicorn", "agente", "agente-oracle", "sshd", "cron"]
    nome = instancia() or "portal-oracle"
    return ["nginx", nome, f"{nome}-agente", f"{nome}-agente-oracle"]


def servicos_configurados(settings_val: str | None) -> list[str]:
    lista = [u for u in re.split(r"[\s,]+", settings_val or "") if UNIT_RE.match(u)]
    return lista or servicos_padrao()


def papel(unit: str) -> str:
    """'portal', 'agente', 'nginx' ou '' (para o icone e os avisos no cartao)."""
    nome = instancia()
    if unit in (nome, "gunicorn") or (not nome and unit == "portal-oracle"):
        return "portal"
    if unit in (f"{nome}-agente", "agente") or unit.endswith("-agente"):
        return "agente"
    if unit in (f"{nome}-agente-oracle", "agente-oracle") or unit.endswith("-agente-oracle"):
        return "agente_oracle"
    if unit.startswith("nginx"):
        return "nginx"
    return ""
