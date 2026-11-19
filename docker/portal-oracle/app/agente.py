"""Agente do portal: processo separado do Gunicorn (servico <nome>-agente, ou programa 'agente' do
supervisord no Docker).

  python -m app.agente            (na pasta do portal, com o utilizador de servico)

Faz, em ciclo:
  - execucoes Oracle: corre as que estao na fila (PENDENTE). Continuam mesmo que o portal seja
    reiniciado ou atualizado (o browser tambem pode ser fechado);
  - metricas: CPU, RAM, discos, sistema e estado dos servicos, publicadas a cada 2 s na base de dados
    do portal (definicao 'sistema_metricas'), lidas pelo modulo Sistema;
  - servicos: executa os pedidos de start/stop/restart feitos no modulo Sistema;
  - sinal de vida (definicao 'agente_estado'): o portal so manda execucoes para a fila se o agente
    tiver dado sinal nos ultimos 15 s.
"""
import json
import logging
import os
import signal
import socket
import threading
import time

from . import crypto, settings, sistema_info, store

VERSAO = "1.0"
CICLO = 1.0                 # segundos entre verificacoes da fila
METRICAS_CADA = 2.0         # segundos entre publicacoes das metricas
HISTORICO = 90              # pontos guardados (CPU/RAM) para os graficos
MAX_EXECUCOES = 4           # execucoes Oracle em simultaneo

log = logging.getLogger("agente")


class Agente:
    def __init__(self):
        self.parar = threading.Event()
        self.inicio = time.time()
        self.execucoes: dict[int, threading.Thread] = {}
        self.hist: list[list] = []          # [ts, cpu, mem]
        self.cpu_antes = sistema_info.cpu_tempos()
        self.cpu_serv: dict[str, tuple] = {}

    # ------------------------------------------------------------------ execucoes
    def fila(self) -> None:
        from .modules.oracle import jobs      # importa oracledb so aqui
        for jid in list(self.execucoes):
            if not self.execucoes[jid].is_alive():
                del self.execucoes[jid]
        if len(self.execucoes) >= MAX_EXECUCOES:
            return
        for jid in store.pending_jobs():
            if len(self.execucoes) >= MAX_EXECUCOES:
                break
            t = threading.Thread(target=self._executar, args=(jobs, jid), daemon=True, name=f"job-{jid}")
            self.execucoes[jid] = t
            t.start()

    @staticmethod
    def _executar(jobs, jid: int) -> None:
        try:
            if jobs.executar(jid):
                log.info("execucao #%s terminada", jid)
        except Exception:  # noqa: BLE001
            log.exception("execucao #%s falhou", jid)

    # ------------------------------------------------------------------ servicos
    def acoes(self) -> None:
        permitidos = set(self.units())
        for a in store.pending_actions():
            if a["unit"] not in permitidos:
                store.finish_action(a["id"], "ERRO", "Serviço não está na lista do módulo Sistema.")
                continue
            if sistema_info.papel(a["unit"]) == "agente" and a["action"] in ("stop", "restart"):
                # o proprio agente: regista antes, porque o processo termina com a acao
                store.finish_action(a["id"], "OK", f"{a['action']} pedido ao proprio agente")
                self.publicar_estado()
                ok, out = sistema_info.acao_servico(a["unit"], a["action"])
                if not ok:
                    store.finish_action(a["id"], "ERRO", out)
                continue
            log.info("servico %s: %s (pedido por %s)", a["unit"], a["action"], a["requested_by"])
            ok, out = sistema_info.acao_servico(a["unit"], a["action"])
            store.finish_action(a["id"], "OK" if ok else "ERRO", out or "OK")

    def units(self) -> list[str]:
        return sistema_info.servicos_configurados(store.get_setting("sistema_servicos"))

    # ------------------------------------------------------------------ metricas e sinal de vida
    def metricas(self) -> None:
        a, self.cpu_antes = sistema_info.amostra(self.cpu_antes, [settings.DATA_DIR])
        self.hist.append([round(a["ts"]), a["cpu"], a["mem"]["pct"]])
        del self.hist[:-HISTORICO]
        servs = sistema_info.estado_servicos(self.units())
        agora = time.time()
        for s in servs:                       # CPU de cada servico (systemd: CPUUsageNSec)
            ns = s.get("cpu_ns")
            ant = self.cpu_serv.get(s["unit"])
            if ns is not None and ant and agora > ant[1]:
                s["cpu_pct"] = round(100.0 * (ns - ant[0]) / 1e9 / (agora - ant[1]), 1)
            if ns is not None:
                self.cpu_serv[s["unit"]] = (ns, agora)
            s["papel"] = sistema_info.papel(s["unit"])
        a.update(historico=self.hist, servicos=servs, backend=sistema_info.servicos_backend(),
                 origem="agente")
        store.save_settings({"sistema_metricas": json.dumps(a, separators=(",", ":"))})
        self.publicar_estado()

    def publicar_estado(self) -> None:
        estado = {"heartbeat": time.time(), "pid": os.getpid(), "host": socket.gethostname(),
                  "versao": VERSAO, "inicio": self.inicio, "instancia": sistema_info.instancia(),
                  "execucoes": sorted(self.execucoes), "user": _utilizador()}
        store.save_settings({"agente_estado": json.dumps(estado)})

    # ------------------------------------------------------------------ ciclo
    def correr(self) -> None:
        store.interrupt_running_jobs("agente")      # execucoes de um agente anterior que morreu
        log.info("agente %s a correr (pid %s, dados em %s)", VERSAO, os.getpid(), store.get().describe())
        prox_metricas = 0.0
        while not self.parar.is_set():
            for passo in (self.fila, self.acoes):
                try:
                    passo()
                except Exception:  # noqa: BLE001 - BD momentaneamente indisponivel, etc.
                    log.exception("erro em %s", passo.__name__)
            if time.time() >= prox_metricas:
                prox_metricas = time.time() + METRICAS_CADA
                try:
                    self.metricas()
                except Exception:  # noqa: BLE001
                    log.exception("erro nas metricas")
            self.parar.wait(CICLO)
        log.info("agente a terminar")
        if self.execucoes:
            log.warning("execucoes ainda em curso serao marcadas INTERROMPIDO: %s", sorted(self.execucoes))
            store.interrupt_running_jobs("agente")
        try:
            store.save_settings({"agente_estado": json.dumps({"heartbeat": 0, "parado": time.time()})})
        except Exception:  # noqa: BLE001
            pass


def _utilizador() -> str:
    try:
        import pwd
        return pwd.getpwuid(os.geteuid()).pw_name
    except (ImportError, KeyError):
        return str(os.geteuid())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    crypto.init(settings.DATA_DIR)
    store.get()                                     # cria/atualiza o esquema
    ag = Agente()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: ag.parar.set())
    ag.correr()


if __name__ == "__main__":
    main()
