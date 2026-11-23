"""Execucao do shrink em segundo plano (pode demorar minutos/horas)."""
import secrets
import threading
import time

import oracledb

from ... import agente_info, agente_rpc, crypto, store
from . import bd as oracle

POLL_SECONDS = 2
MAX_LIVE_LINES = 20000            # limite do output em direto guardado na BD (o final vem completo)


def _monitor(job_id, cfg, password, sid, serial, pipe, stop: threading.Event, direto: bool = False):
    """Segunda ligacao: passo atual (V$SESSION.ACTION), percentagem (V$SESSION_LONGOPS) e linhas do
    output em direto (DBMS_PIPE). Grava no job a cada POLL_SECONDS para a pagina mostrar.
    Com o agente cliente, esta ligacao tambem vai pelo agente."""
    try:
        mon = oracle.connect(cfg, password, direto=direto)
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


SQL_SESSAO = "SELECT sid, serial# FROM v$session WHERE sid = SYS_CONTEXT('USERENV', 'SID')"


def _run(job_id: int, cfg: dict, params: dict):
    pipe = f"PORTAL_SHRINK_{job_id}_{secrets.token_hex(4).upper()}"
    motivo = ""
    try:
        password = crypto.decrypt(cfg["password_enc"])
        a, motivo = agente_rpc.alvo(cfg)
        if a:
            try:
                r = agente_rpc.chamar(a, "/rpc/job/iniciar", {"db": agente_rpc._db(cfg, password), "sql_sessao": SQL_SESSAO,
                                                              "bloco": oracle.build_block(params, pipe), "ref": f"job {job_id}"},
                                      timeout=120)
            except agente_rpc.Indisponivel as e:
                agente_rpc.falhou(a, str(e))
                a, motivo = None, str(e)
            if a:
                store.update_job(job_id, via=f"agente cliente {a['nome']}", remoto=f"{a['cliente_id']}:{r['job']}:{pipe}",
                                 ora_sid=r.get("sid"), ora_serial=r.get("serial"), current_action="a iniciar (agente)",
                                 progress=0)
                agente_rpc.registar(cfg, "agente", a["nome"])
                _acompanhar(job_id, cfg, password, a, r["job"], pipe, r.get("sid"), r.get("serial"))
                return
        if agente_rpc.modo(cfg) == "obrigatorio":
            raise agente_rpc.ErroOracleRemoto(f"Base de dados só acessível pelo agente cliente, indisponível: {motivo}")
    except Exception as exc:  # noqa: BLE001
        store.update_job(job_id, status="ERRO", error=str(exc), finished_at=store.now())
        _notificar(job_id)
        return
    store.update_job(job_id, via="portal" + (f" ({motivo})" if motivo else ""))
    agente_rpc.registar(cfg, "portal", motivo)
    _run_direto(job_id, cfg, params, password, pipe)


def _acompanhar(job_id: int, cfg: dict, password: str, a: dict, rjob: str, pipe: str, sid, serial) -> None:
    """A execucao corre no agente cliente: o portal acompanha (passo, %, output em direto pelo monitor,
    que tambem vai pelo agente) e, no fim, grava o resultado. Se o portal/agente do portal reiniciar,
    retomar_remotos volta a chamar esta funcao."""
    stop = threading.Event()
    monitor = None
    if sid:
        monitor = threading.Thread(target=_monitor, args=(job_id, cfg, password, sid, serial, pipe, stop), daemon=True)
        monitor.start()
    d, falhas_desde = {}, None
    try:
        while True:
            try:
                d = agente_rpc.chamar(a, "/rpc/job/estado", {"job": rjob}, timeout=30)
                falhas_desde = None
            except agente_rpc.Indisponivel as e:
                falhas_desde = falhas_desde or time.time()
                if time.time() - falhas_desde > 1800:            # 30 min sem contacto com o agente
                    d = {"estado": "perdido", "erro": f"sem contacto com o agente {a['nome']} há 30 min: {e}"}
                    break
                store.update_job(job_id, current_action=f"sem contacto com o agente {a['nome']}; a tentar de novo")
            except LookupError as e:
                d = {"estado": "perdido", "erro": f"o agente {a['nome']} já não tem esta execução ({e})"}
                break
            if d.get("estado") and d["estado"] != "a correr":
                break
            time.sleep(POLL_SECONDS)
    finally:
        _parar(stop, monitor)
    job = store.get_job(job_id)
    linhas = d.get("saida")
    out = "\n".join(linhas) if linhas else (job.get("output") or "" if job else "")
    n_err, n_av = store.contar(out)
    if d.get("estado") == "ok" and not (job and job["status"] == "CANCELADO"):
        store.update_job(job_id, status="ERRO" if n_err else "OK", output=out, progress=1000, finished_at=store.now(),
                         current_action="fim", n_erros=n_err, n_avisos=n_av)
    else:
        status = "CANCELADO" if job and job["status"] == "CANCELADO" else \
            ("INTERROMPIDO" if d.get("estado") == "perdido" else "ERRO")
        store.update_job(job_id, status=status, error=d.get("erro") or ("cancelada" if status == "CANCELADO" else "erro no agente"), output=out,
                         finished_at=store.now(), n_erros=n_err, n_avisos=n_av)
    try:
        agente_rpc.chamar(a, "/rpc/job/esquecer", {"job": rjob}, timeout=15)
    except Exception:  # noqa: BLE001
        pass
    _notificar(job_id)


def retomar_remotos(runner: str) -> list[threading.Thread]:
    """Depois de reiniciar: volta a acompanhar as execucoes deste runner que correm num agente cliente."""
    ts = []
    for j in store.remote_jobs():
        if (j.get("runner") or "portal") != runner:
            continue
        try:
            cid, rjob, pipe = j["remoto"].split(":", 2)
            cfg = store.get_db(j["db_id"])
            if not cfg:
                raise ValueError("base de dados apagada")
            cfg = {**cfg, "cliente_id": int(cid), "agente_modo": cfg.get("agente_modo") or "auto"}
            a, motivo = agente_rpc.alvo(cfg)
            if not a:                       # o alvo pode estar em pausa por falha: tenta na mesma
                from ... import clientes
                c = clientes.obter(int(cid))
                rpc = ((c or {}).get("info") or {}).get("rpc") or {}
                if not c or not rpc.get("fp"):
                    raise ValueError(motivo or "agente cliente desconhecido")
                a = {"cliente_id": c["id"], "nome": c["nome"], "host": c["endereco"], "porta": int(rpc.get("porta") or 8795),
                     "fp": rpc["fp"], "token": clientes.token(c)}
            password = crypto.decrypt(cfg["password_enc"])
        except Exception as exc:  # noqa: BLE001
            store.update_job(j["id"], status="INTERROMPIDO", error=f"não foi possível retomar: {exc}", finished_at=store.now())
            continue
        store.update_job(j["id"], current_action="retomada após reinício (agente cliente)")
        t = threading.Thread(target=_acompanhar, args=(j["id"], cfg, password, a, rjob, pipe, j.get("ora_sid"),
                                                        j.get("ora_serial")), daemon=True, name=f"job-{j['id']}")
        t.start()
        ts.append((j["id"], t))
    return ts


def _run_direto(job_id: int, cfg: dict, params: dict, password: str, pipe: str):
    stop = threading.Event()
    conn = None
    monitor = None
    try:
        conn = oracle.connect(cfg, password, direto=True)
        sid, serial = oracle.session_id(conn)
        store.update_job(job_id, ora_sid=sid, ora_serial=serial, current_action="a iniciar", progress=0)
        if sid:
            monitor = threading.Thread(target=_monitor, args=(job_id, cfg, password, sid, serial, pipe, stop, True),
                                       daemon=True)
            monitor.start()

        lines = oracle.run_shrink(conn, params, pipe)
        _parar(stop, monitor)
        out = "\n".join(lines)
        n_err, n_av = store.contar(out)
        store.update_job(job_id, status="ERRO" if n_err else "OK", output=out, progress=1000,
                         finished_at=store.now(), current_action="fim", n_erros=n_err, n_avisos=n_av)
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
        n_err, n_av = store.contar(partial)
        store.update_job(job_id, status=status, error=str(exc), output=partial,
                         finished_at=store.now(), n_erros=n_err, n_avisos=n_av)
    finally:
        stop.set()
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        _notificar(job_id)


ROTULO = {"sucesso": "terminou com sucesso", "aviso": "terminou com avisos", "erro": "terminou com erros",
          "falhado": "falhou"}


def _notificar(job_id: int) -> None:
    """Evento 'oracle.execucao' (regras em SMTP > Regras / Notificacoes)."""
    from ... import eventos
    j = store.get_job(job_id)
    if not j or j["resultado"] not in ROTULO:
        return
    p = j["params"]
    titulo = f"Shrink {p.get('modo')} de {p.get('ts')} em {j['db_name']} {ROTULO[j['resultado']]} (#{j['id']})"
    linhas = [f"Base de dados : {j['db_name']}", f"Tablespace    : {p.get('ts')}", f"Modo          : {p.get('modo')}",
              f"Estado        : {j['status']} ({j['resultado']})", f"Início / fim  : {j['started_at']} -> {j['finished_at']}",
              f"Lançado por   : {j['created_by']}"]
    if j.get("n_erros") or j.get("n_avisos"):
        linhas.append(f"Linhas        : {j.get('n_erros') or 0} com erro, {j.get('n_avisos') or 0} com aviso")
    if j.get("error"):
        linhas.append(f"Erro          : {j['error']}")
    out = j.get("output") or ""
    erros = [l for l in out.splitlines() if "-- ERRO" in l or "ORA-" in l][:10]
    if erros:
        linhas += ["", "Linhas com erro:"] + [f"  {l.strip()}" for l in erros]
    eventos.emitir("oracle.execucao", j["resultado"], titulo, "\n".join(linhas),
                   f"/oracle/execucoes/{j['id']}", autor=j["created_by"])


def _parar(stop: threading.Event, monitor) -> None:
    """Para o monitor e espera que acabe, para nao sobrepor o output final com o de direto."""
    stop.set()
    if monitor is not None:
        monitor.join(timeout=15)


def start(db_id: int, params: dict, user: str, agendado_para: str | None = None) -> int:
    """Com o agente ativo, a execucao fica na fila (PENDENTE) e e o agente que a corre: continua
    mesmo que o portal seja reiniciado. Sem agente, corre numa thread do portal (como antes).
    agendado_para ('AAAA-MM-DD HH:MM:SS'): fica AGENDADO e o agente corre-a a essa hora."""
    cfg = store.get_db(db_id)
    if not cfg:
        raise oracle.ValidationError("Base de dados nao encontrada.")
    if agendado_para:
        if not agente_info.oracle_ativo():
            raise oracle.ValidationError("Para agendar é preciso o agente Oracle (ou o agente do portal) ativo: "
                                         "é ele que corre a execução à hora marcada.")
        return store.create_job(cfg, params, user, status="AGENDADO", runner="agente", agendado_para=agendado_para)
    if agente_info.oracle_ativo():
        return store.create_job(cfg, params, user, status="PENDENTE", runner="agente")
    job_id = store.create_job(cfg, params, user, runner="portal")
    threading.Thread(target=_run, args=(job_id, cfg, params), daemon=True).start()
    return job_id


def executar(job_id: int, runner: str = "agente") -> bool:
    """Usado pelos agentes: fica com a execucao PENDENTE e corre-a (bloqueia ate terminar)."""
    if not store.claim_job(job_id, runner):
        return False
    job = store.get_job(job_id)
    cfg = store.get_db(job["db_id"]) if job else None
    if not cfg:
        store.update_job(job_id, status="ERRO", error="Base de dados ja nao existe.", finished_at=store.now())
        return True
    _run(job_id, cfg, job["params"])
    return True


def cancel(job_id: int) -> str:
    """Cancela: na fila/agendada sai logo ("fila"); em curso termina a sessao Oracle ("kill") ou fica marcada
    para terminar ("marcada", ORA-00031: o Oracle acaba-a depois de desfazer o trabalho em curso)."""
    job = store.get_job(job_id)
    if job and job["status"] in ("PENDENTE", "AGENDADO"):
        store.update_job(job_id, status="CANCELADO", finished_at=store.now(),
                         current_action="cancelada na fila" if job["status"] == "PENDENTE" else "agendamento cancelado")
        return "fila"
    if not job or job["status"] != "EM CURSO":
        raise oracle.ValidationError("A execucao ja terminou.")
    if not job["ora_sid"]:
        raise oracle.ValidationError("Sessao Oracle ainda nao identificada; tente daqui a uns segundos.")
    cfg = store.get_db(job["db_id"])
    store.update_job(job_id, status="CANCELADO", current_action="a cancelar (KILL SESSION)")
    conn = None
    try:
        conn = oracle.connect(cfg, crypto.decrypt(cfg["password_enc"]))
        oracle.kill_session(conn, job["ora_sid"], job["ora_serial"])
        return "kill"
    except oracledb.Error as exc:
        msg = str(exc)
        if "ORA-00031" in msg:                  # sessao marcada para terminar: o pedido foi aceite
            return "marcada"
        if "ORA-00030" in msg:                  # a sessao ja nao existe: a execucao acabou entretanto
            return "kill"
        if "DPY-4011" in msg and "ORA-00031" not in msg:
            # a ligacao caiu durante o KILL: confirma se a sessao ainda existe
            try:
                c2 = oracle.connect(cfg, crypto.decrypt(cfg["password_enc"]))
                try:
                    with c2.cursor() as cur:
                        cur.execute("SELECT COUNT(*) FROM v$session WHERE sid = :1 AND serial# = :2",
                                    [job["ora_sid"], job["ora_serial"]])
                        if cur.fetchone()[0] == 0:
                            return "kill"
                finally:
                    c2.close()
            except oracledb.Error:
                pass
        store.update_job(job_id, status="EM CURSO", current_action=job.get("current_action"))
        raise
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
