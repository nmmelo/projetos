"""Execucao do shrink em segundo plano (pode demorar minutos/horas)."""
import secrets
import threading

import oracledb

from ... import crypto, store
from . import bd as oracle

POLL_SECONDS = 2
MAX_LIVE_LINES = 20000            # limite do output em direto guardado na BD (o final vem completo)


def _monitor(job_id, cfg, password, sid, serial, pipe, stop: threading.Event):
    """Segunda ligacao: passo atual (V$SESSION.ACTION), percentagem (V$SESSION_LONGOPS) e linhas do
    output em direto (DBMS_PIPE). Grava no job a cada POLL_SECONDS para a pagina mostrar."""
    try:
        mon = oracle.connect(cfg, password)
    except oracledb.Error:
        return
    live, pipe_ok, prog_ok = [], True, True
    try:
        while True:
            parado = stop.wait(POLL_SECONDS)
            fields = {}
            try:
                act = oracle.current_action(mon, sid, serial)
                if act:
                    fields["current_action"] = act
            except oracledb.Error:
                break
            if prog_ok:
                try:
                    pr = oracle.current_progress(mon, sid, serial)
                    if pr is not None:
                        fields["progress"] = pr
                except oracledb.Error:
                    prog_ok = False                       # sem acesso a V$SESSION_LONGOPS
            if pipe_ok:
                try:
                    novas = oracle.read_pipe(mon, pipe)
                    if novas:
                        live.extend(novas)
                        del live[:-MAX_LIVE_LINES]
                        fields["output"] = "\n".join(live)
                except oracledb.Error:
                    pipe_ok = False                       # sem EXECUTE em DBMS_PIPE: so no fim
            if parado:
                break                                     # o _run grava o resultado final
            if fields:
                store.update_job(job_id, **fields)
        if pipe_ok:
            try:
                oracle.purge_pipe(mon, pipe)
            except oracledb.Error:
                pass
    finally:
        mon.close()


def _run(job_id: int, cfg: dict, params: dict):
    stop = threading.Event()
    conn = None
    monitor = None
    pipe = f"PORTAL_SHRINK_{job_id}_{secrets.token_hex(4).upper()}"
    try:
        password = crypto.decrypt(cfg["password_enc"])
        conn = oracle.connect(cfg, password)
        sid, serial = oracle.session_id(conn)
        store.update_job(job_id, ora_sid=sid, ora_serial=serial, current_action="a iniciar", progress=0)
        if sid:
            monitor = threading.Thread(target=_monitor, args=(job_id, cfg, password, sid, serial, pipe, stop),
                                       daemon=True)
            monitor.start()

        lines = oracle.run_shrink(conn, params, pipe)
        _parar(stop, monitor)
        out = "\n".join(lines)
        has_err = "-- ERRO" in out
        store.update_job(job_id, status="ERRO" if has_err else "OK", output=out, progress=1000,
                         finished_at=store.now(), current_action="fim")
    except Exception as exc:  # noqa: BLE001 - qualquer falha fica registada no job
        _parar(stop, monitor)
        partial = ""
        if conn is not None:
            try:
                partial = "\n".join(oracle.read_dbms_output(conn))
            except Exception:  # noqa: BLE001
                pass
        job = store.get_job(job_id)
        if not partial and job:
            partial = job.get("output") or ""             # o que chegou em direto
        status = "CANCELADO" if job and job["status"] == "CANCELADO" else "ERRO"
        store.update_job(job_id, status=status, error=str(exc), output=partial,
                         finished_at=store.now())
    finally:
        stop.set()
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _parar(stop: threading.Event, monitor) -> None:
    """Para o monitor e espera que acabe, para nao sobrepor o output final com o de direto."""
    stop.set()
    if monitor is not None:
        monitor.join(timeout=15)


def start(db_id: int, params: dict, user: str) -> int:
    cfg = store.get_db(db_id)
    if not cfg:
        raise oracle.ValidationError("Base de dados nao encontrada.")
    job_id = store.create_job(cfg, params, user)
    threading.Thread(target=_run, args=(job_id, cfg, params), daemon=True).start()
    return job_id


def cancel(job_id: int) -> None:
    job = store.get_job(job_id)
    if not job or job["status"] != "EM CURSO":
        raise oracle.ValidationError("A execucao ja terminou.")
    if not job["ora_sid"]:
        raise oracle.ValidationError("Sessao Oracle ainda nao identificada; tente daqui a uns segundos.")
    cfg = store.get_db(job["db_id"])
    store.update_job(job_id, status="CANCELADO")
    conn = oracle.connect(cfg, crypto.decrypt(cfg["password_enc"]))
    try:
        oracle.kill_session(conn, job["ora_sid"], job["ora_serial"])
    finally:
        conn.close()
