"""Agentes do portal, os seus parametros e as metricas dos processos do portal.

  agente          (app/agente.py)         metricas, servicos, email, agendamentos, eventos; corre as execucoes
                                          Oracle so quando o agente Oracle esta parado
  agente_oracle   (app/agente_oracle.py)  execucoes Oracle em fila e agendadas (shrink), com N em simultaneo

Parametros (definicoes "<agente>.<chave>") alteraveis em Sistema > Agentes: os de "imediato" sao lidos a cada
ciclo; os de "reinicio" quando o agente arranca. Os limites de CPU/memoria do servico (systemd) aplicam-se com
'instalar.sh --agente-recursos' (precisam de root).

Metricas: o agente do portal mede a cada 30 s os processos do portal (web/Gunicorn, agentes) e o sistema e
guarda-as na tabela 'metricas' (graficos em Sistema > Agentes e Analise, ate 35 dias)."""
import json
import os
import time

from . import settings, store

AGENTES = {
    "agente": {
        "nome": "Agente do portal", "modulo": "app.agente", "unit": "-agente", "supervisor": "agente",
        "descricao": "Métricas do servidor, serviços (iniciar/parar), email, tarefas agendadas e eventos. "
                     "Corre as execuções Oracle só quando o agente Oracle está parado.",
        "params": [
            {"chave": "ciclo", "rotulo": "Ciclo (s)", "min": 1, "max": 30, "padrao": 1, "aplica": "imediato",
             "ajuda": "Intervalo entre verificações da fila de ações, email e agendamentos."},
            {"chave": "metricas", "rotulo": "Métricas do servidor a cada (s)", "min": 2, "max": 60, "padrao": 2,
             "aplica": "imediato", "ajuda": "Atualização do painel Sistema (CPU, memória, discos, serviços)."},
            {"chave": "execucoes", "rotulo": "Execuções Oracle em simultâneo (sem agente Oracle)", "min": 1, "max": 16,
             "padrao": 2, "aplica": "imediato", "ajuda": "Só conta quando o agente Oracle está parado."},
            {"chave": "nice", "rotulo": "Prioridade (nice 0–19)", "min": 0, "max": 19, "padrao": 0, "aplica": "reinicio",
             "ajuda": "Mais alto = menos prioridade de CPU para o agente."},
        ],
    },
    "agente_oracle": {
        "nome": "Agente Oracle", "modulo": "app.agente_oracle", "unit": "-agente-oracle", "supervisor": "agente-oracle",
        "descricao": "Execuções Oracle (shrink) em fila e agendadas, isoladas do resto do portal: várias em "
                     "simultâneo, cada uma na sua sessão Oracle.",
        "params": [
            {"chave": "execucoes", "rotulo": "Execuções em simultâneo", "min": 1, "max": 16, "padrao": 4,
             "aplica": "imediato", "ajuda": "Shrinks a correr ao mesmo tempo (bases ou tablespaces diferentes)."},
            {"chave": "dop", "rotulo": "Paralelismo por omissão do MOVE/REBUILD", "min": 1, "max": 32, "padrao": 1,
             "aplica": "imediato", "ajuda": "Valor inicial do campo Paralelismo no Shrink (1 = em série). Na base "
                                            "de dados: PARALLEL n (Enterprise Edition), mais CPU e I/O."},
            {"chave": "ciclo", "rotulo": "Ciclo (s)", "min": 1, "max": 30, "padrao": 2, "aplica": "imediato",
             "ajuda": "Intervalo entre verificações da fila e das execuções agendadas."},
            {"chave": "nice", "rotulo": "Prioridade (nice 0–19)", "min": 0, "max": 19, "padrao": 0, "aplica": "reinicio",
             "ajuda": "Mais alto = menos prioridade de CPU no servidor do portal (o trabalho pesado é na BD)."},
        ],
    },
}
COMPONENTES = {"web": "Portal (Gunicorn)", "agente": "Agente do portal", "agente_oracle": "Agente Oracle",
               "outro": "Outros processos do portal", "sistema": "Servidor (total)"}
RETENCAO_DIAS = 35


def param(agente: str, chave: str) -> int:
    p = next((x for x in AGENTES[agente]["params"] if x["chave"] == chave), None)
    if not p:
        raise KeyError(chave)
    try:
        v = int(store.get_setting(f"{agente}.{chave}") or p["padrao"])
    except (TypeError, ValueError):
        v = p["padrao"]
    return max(p["min"], min(p["max"], v))


def guardar_params(agente: str, form) -> list[str]:
    erros, novos = [], {}
    for p in AGENTES[agente]["params"]:
        v = (form.get(p["chave"]) or "").strip()
        if not v:
            continue
        try:
            n = int(v)
            if not p["min"] <= n <= p["max"]:
                raise ValueError
            novos[f"{agente}.{p['chave']}"] = str(n)
        except ValueError:
            erros.append(f"{p['rotulo']}: entre {p['min']} e {p['max']}.")
    if not erros:
        store.save_settings(novos)
    return erros


def unit(agente: str) -> str:
    from . import sistema_info
    a = AGENTES[agente]
    if sistema_info.servicos_backend() == "supervisor":
        return a["supervisor"]
    return (sistema_info.instancia() or "portal-oracle") + a["unit"]


def aplicar_nice(agente: str) -> int:
    n = param(agente, "nice")
    try:
        atual = os.nice(0)
        if n > atual:
            os.nice(n - atual)
        return os.nice(0)
    except OSError:
        return 0


# ============================================================ processos do portal
def componente(cmd: list[str]) -> str | None:
    c = " ".join(cmd)
    if "app.agente_oracle" in c:
        return "agente_oracle"
    if "app.agente" in c:
        return "agente"
    if "gunicorn" in c or "wsgi:app" in c:
        return "web"
    if "python" in c:                       # outros processos Python na pasta do portal (manage.py, ...)
        return "outro"
    return None


class Medidor:
    """Mede os processos do portal (mesma pasta de trabalho) com psutil; mantem os objetos entre amostras para
    o CPU% ser calculado entre leituras."""

    def __init__(self):
        self.procs: dict[int, object] = {}
        try:
            import psutil
            self.ps = psutil
        except ImportError:
            self.ps = None
        self.base = os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.eu = os.getuid() if hasattr(os, "getuid") else None

    def amostra(self) -> dict:
        """{"ts", "processos": [...], "por": {comp: {cpu, rss, threads, procs}}, "sistema": {...}}"""
        if not self.ps:
            return {"ts": time.time(), "processos": [], "por": {}, "sistema": {}, "erro": "psutil não instalado"}
        ps, vistos, lista = self.ps, set(), []
        for p in ps.process_iter(["pid", "cmdline", "username", "uids", "create_time", "num_threads", "status"]):
            try:
                cmd = p.info.get("cmdline") or []
                comp = componente(cmd)
                if not comp:
                    continue
                if self.eu is not None and p.info.get("uids") and p.info["uids"].real != self.eu:
                    continue
                try:
                    if os.path.realpath(p.cwd()) != self.base:
                        continue
                except (ps.AccessDenied, ps.ZombieProcess, FileNotFoundError):
                    continue
                pid = p.info["pid"]
                vistos.add(pid)
                obj = self.procs.get(pid)
                if obj is None:
                    obj = p
                    self.procs[pid] = obj
                    cpu = obj.cpu_percent(None)          # primeira leitura: 0 (base para a seguinte)
                else:
                    cpu = obj.cpu_percent(None)
                mi = obj.memory_info()
                lista.append({"pid": pid, "ppid": obj.ppid(), "comp": comp, "cpu": round(cpu, 1), "rss": mi.rss,
                              "threads": p.info.get("num_threads") or 0, "status": p.info.get("status"),
                              "inicio": int(p.info.get("create_time") or 0), "user": p.info.get("username") or "",
                              "cmd": " ".join(cmd)[:300]})
            except (ps.NoSuchProcess, ps.AccessDenied, ps.ZombieProcess):
                continue
        for pid in list(self.procs):
            if pid not in vistos:
                del self.procs[pid]
        por: dict[str, dict] = {}
        for x in lista:
            d = por.setdefault(x["comp"], {"cpu": 0.0, "rss": 0, "threads": 0, "procs": 0})
            d["cpu"] += x["cpu"]
            d["rss"] += x["rss"]
            d["threads"] += x["threads"]
            d["procs"] += 1
        vm = ps.virtual_memory()
        sistema = {"cpu": ps.cpu_percent(None), "mem_pct": vm.percent, "mem_usada": vm.total - vm.available,
                   "mem_total": vm.total, "ncpu": ps.cpu_count() or 1, "load": list(os.getloadavg())}
        return {"ts": time.time(), "processos": sorted(lista, key=lambda x: (x["comp"], x["pid"])), "por": por,
                "sistema": sistema}


def gravar(a: dict) -> None:
    """Guarda a amostra na tabela metricas (um registo por componente + sistema)."""
    ts = int(a["ts"])
    with store.tx() as t:
        for comp, d in a["por"].items():
            t.insert("metricas", {"ts": ts, "componente": comp, "cpu": round(d["cpu"], 1), "mem": d["rss"],
                                  "threads": d["threads"], "procs": d["procs"], "extra": None})
        s = a.get("sistema") or {}
        if s:
            t.insert("metricas", {"ts": ts, "componente": "sistema", "cpu": s.get("cpu"), "mem": s.get("mem_usada"),
                                  "threads": None, "procs": None, "extra": json.dumps({"mem_pct": s.get("mem_pct"),
                                                                                       "load1": (s.get("load") or [None])[0]})})


def limpar() -> int:
    with store.tx() as t:
        return t.run("DELETE FROM metricas WHERE ts < ?", (int(time.time()) - RETENCAO_DIAS * 86400,))


INTERVALOS = {"1h": 3600, "5h": 5 * 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}


def serie(componentes: list[str], desde: int, ate: int, pontos: int = 240) -> dict:
    """Series agregadas por intervalo (media e maximo do CPU, media da memoria) para os graficos."""
    bucket = max(30, ((ate - desde) // pontos) // 30 * 30 or 30)
    comps = [c for c in componentes if c in COMPONENTES]
    if not comps:
        return {"bucket": bucket, "series": {}}
    marks = ",".join("?" * len(comps))
    with store.tx() as t:
        rows = t.all(f"SELECT componente, (ts - ts % {bucket}) AS b, AVG(cpu) AS cpu, MAX(cpu) AS cpu_max, AVG(mem) AS mem, "
                     f"AVG(threads) AS threads FROM metricas WHERE ts >= ? AND ts <= ? AND componente IN ({marks}) "
                     f"GROUP BY componente, (ts - ts % {bucket}) ORDER BY 2", (desde, ate, *comps))
    out: dict[str, list] = {c: [] for c in comps}
    for r in rows:
        out[r["componente"]].append([int(r["b"]), _r(r["cpu"]), _r(r["cpu_max"]), int(r["mem"] or 0),
                                     _r(r["threads"])])
    return {"bucket": bucket, "series": out, "desde": desde, "ate": ate}


def _r(v):
    return round(float(v), 1) if v is not None else None


def processos_atuais() -> dict:
    try:
        return json.loads(store.get_setting("processos_portal") or "{}")
    except (TypeError, ValueError):
        return {}


def recursos_servico(agente: str) -> dict:
    """Limites do servico systemd (CPUQuota, MemoryMax, Nice) e o comando para os alterar."""
    import subprocess
    from . import sistema_info
    u = unit(agente)
    if sistema_info.servicos_backend() != "systemd":
        return {"unit": u, "backend": sistema_info.servicos_backend()}
    try:
        out = subprocess.run(["systemctl", "show", u, "-p", "CPUQuotaPerSecUSec,MemoryMax,MemoryCurrent,Nice,TasksMax,"
                              "ActiveEnterTimestamp,MainPID"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    d = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    cpu = d.get("CPUQuotaPerSecUSec", "infinity")
    if cpu not in ("", "infinity"):
        try:
            v = float(cpu.rstrip("s").replace("ms", "")) * (1 if cpu.endswith("ms") else 1000)
            cpu = f"{round(v / 10)}%"
        except ValueError:
            pass
    mem = d.get("MemoryMax", "infinity")
    if mem.isdigit():
        mem = f"{int(mem) / 1073741824:.1f} GB"
    return {"unit": u, "backend": "systemd", "cpu": "sem limite" if cpu in ("", "infinity") else cpu,
            "mem": "sem limite" if mem in ("", "infinity") else mem, "nice": d.get("Nice", "0"),
            "tasks": d.get("TasksMax", ""), "desde": d.get("ActiveEnterTimestamp", ""), "pid": d.get("MainPID", "")}
