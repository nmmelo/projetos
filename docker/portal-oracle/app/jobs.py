"""Execucao do shrink em segundo plano (pode demorar minutos/horas)."""
import threading

import oracledb

from . import crypto, oracle, store

POLL_SECONDS = 5


def _monitor(job_id, cfg, password, sid, serial, stop: threading.Event):
    """Segunda ligacao que le V$SESSION.ACTION para mostrar o passo em curso."""
    try:
        mon = oracle.connect(cfg, password)
    except oracledb.Error:
        return
    try:
        while not stop.wait(POLL_SECONDS):
            try:
                act = oracle.current_action(mon, sid, serial)
                if act:
                    store.update_job(job_id, current_action=act)
            except oracledb.Error:
                break
    finally:
        mon.close()


def _run(job_id: int, cfg: dict, params: dict):
    stop = threading.Event()
    conn = None
    try:
        password = crypto.decrypt(cfg["password_enc"])
        conn = oracle.connect(cfg, password)
        sid, serial = oracle.session_id(conn)
        store.update_job(job_id, ora_sid=sid, ora_serial=serial, current_action="a iniciar")
        if sid:
            threading.Thread(target=_monitor, args=(job_id, cfg, password, sid, serial, stop),
                             daemon=True).start()

        lines = oracle.run_shrink(conn, params)
        out = "\n".join(lines)
        has_err = "-- ERRO" in out
        store.update_job(job_id, status="ERRO" if has_err else "OK", output=out,
                           finished_at=store.now(), current_action="fim")
    except Exception as exc:  # noqa: BLE001 - qualquer falha fica registada no job
        partial = ""
        if conn is not None:
            try:
                partial = "\n".join(oracle.read_dbms_output(conn))
            except Exception:  # noqa: BLE001
                pass
        job = store.get_job(job_id)
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
