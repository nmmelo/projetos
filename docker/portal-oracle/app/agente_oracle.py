"""Agente Oracle: processo separado (servico <nome>-agente-oracle, ou programa 'agente-oracle' do supervisord
no Docker) que corre as execucoes Oracle (shrink) em fila e agendadas.

  python -m app.agente_oracle            (na pasta do portal, com o utilizador de servico)

Isola o trabalho Oracle (que pode demorar dezenas de minutos) do agente do portal (metricas, servicos, email,
agendamentos) e corre varias execucoes em simultaneo, cada uma na sua sessao Oracle. Parametros em
Sistema > Agentes > Agente Oracle (execucoes em simultaneo, paralelismo por omissao, ciclo, prioridade).
Enquanto este agente estiver ativo, o agente do portal deixa de pegar em execucoes Oracle.
"""
import json
import logging
import os
import signal
import socket
import threading
import time

from . import agentes, crypto, modulos, settings, store

VERSAO = "1.0"
NOME = "agente_oracle"
log = logging.getLogger("agente-oracle")


class AgenteOracle:
    def __init__(self):
        self.parar = threading.Event()
        self.inicio = time.time()
        self.execucoes: dict[int, threading.Thread] = {}
        self.feitas = 0
        self.nice = 0

    def fila(self) -> None:
        from .modules.oracle import jobs
        for jid in list(self.execucoes):
            if not self.execucoes[jid].is_alive():
                del self.execucoes[jid]
                self.feitas += 1
        maximo = agentes.param(NOME, "execucoes")
        if len(self.execucoes) >= maximo:
            return
        for jid in store.pending_jobs():
            if len(self.execucoes) >= maximo:
                break
            if jid in self.execucoes:
                continue
            t = threading.Thread(target=self._executar, args=(jobs, jid), daemon=True, name=f"job-{jid}")
            self.execucoes[jid] = t
            t.start()

    @staticmethod
    def _executar(jobs, jid: int) -> None:
        try:
            if jobs.executar(jid, NOME):
                log.info("execucao #%s terminada", jid)
        except Exception:  # noqa: BLE001
            log.exception("execucao #%s falhou", jid)

    def publicar_estado(self) -> None:
        estado = {"heartbeat": time.time(), "pid": os.getpid(), "host": socket.gethostname(), "versao": VERSAO,
                  "inicio": self.inicio, "execucoes": sorted(self.execucoes), "feitas": self.feitas,
                  "maximo": agentes.param(NOME, "execucoes"), "nice": self.nice, "threads": threading.active_count(),
                  "user": _utilizador()}
        store.save_settings({f"{NOME}_estado": json.dumps(estado)})

    def correr(self) -> None:
        store.interrupt_running_jobs("agente_oracle")      # execucoes de um agente Oracle anterior que morreu
        self.nice = agentes.aplicar_nice(NOME)
        log.info("agente Oracle %s a correr (pid %s, nice %s, dados em %s)", VERSAO, os.getpid(), self.nice,
                 store.get().describe())
        prox_estado = 0.0
        while not self.parar.is_set():
            try:
                self.fila()
            except Exception:  # noqa: BLE001 - BD momentaneamente indisponivel, etc.
                log.exception("erro na fila")
            if time.time() >= prox_estado:
                prox_estado = time.time() + 2
                try:
                    self.publicar_estado()
                except Exception:  # noqa: BLE001
                    log.exception("erro ao publicar o estado")
            self.parar.wait(agentes.param(NOME, "ciclo"))
        log.info("agente Oracle a terminar")
        if self.execucoes:
            log.warning("execucoes ainda em curso serao marcadas INTERROMPIDO: %s", sorted(self.execucoes))
            store.interrupt_running_jobs("agente_oracle")
        try:
            store.save_settings({f"{NOME}_estado": json.dumps({"heartbeat": 0, "parado": time.time()})})
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
    store.get()
    modulos.registar_todos()
    ag = AgenteOracle()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: ag.parar.set())
    ag.correr()


if __name__ == "__main__":
    main()
