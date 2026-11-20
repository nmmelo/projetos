"""Agendamentos: tarefas periodicas dos modulos, executadas pelo agente.

Cada modulo declara as suas tarefas em Modulo.tarefas; o portal tem as suas (backup, limpeza).
A configuracao (ativo, frequencia, notificacao, parametros) fica na tabela 'agendamentos';
cada execucao fica em 'agendamento_execucoes'.

Frequencias: 'minutos' (a cada N minutos), 'horas' (a cada N horas), 'diario' (HH:MM),
'semanal' (dias 1-7, segunda = 1, as HH:MM).
Notificacao: 'nunca', 'erro' (so se falhar) ou 'sempre' -> evento portal.agendamento
(situacoes sucesso / erro), entregue pelas regras de SMTP > Notificacoes e SMTP > Regras.
"""
import datetime as dt
import json
import logging
import threading
import time
import traceback

from . import modulos, store

log = logging.getLogger("agendamento")
FREQS = {"minutos": "A cada N minutos", "horas": "A cada N horas", "diario": "Todos os dias",
         "semanal": "Dias da semana"}
DIAS = {1: "Seg", 2: "Ter", 3: "Qua", 4: "Qui", 5: "Sex", 6: "Sáb", 7: "Dom"}
NOTIFICAR = {"nunca": "Não", "erro": "Só se falhar", "sempre": "Sempre"}
EVENTO = "portal.agendamento"
EVENTOS_PORTAL = {EVENTO: {"nome": "Tarefa agendada terminada",
                           "situacoes": {"sucesso": "Sucesso", "aviso": "Com avisos", "erro": "Falhou"}}}
_a_correr: set[str] = set()
_lock = threading.Lock()


# ============================================================================ tarefas do portal
def _t_backup(par: dict) -> tuple[str, str]:
    from . import backup
    bid = backup.create("agendamento", "agendado", "backup agendado")
    keep = int(par.get("manter") or 30)
    apagados = backup.prune(keep, "agendado")
    return "sucesso", f"Backup #{bid} criado; {apagados} antigo(s) removido(s) (mantidos {keep})."


def _t_limpeza(par: dict) -> tuple[str, str]:
    dias = int(par.get("dias") or 90)
    limite = store.ago(days=dias)
    with store.tx() as t:
        n1 = t.run("DELETE FROM notifications WHERE lida = 1 AND created_at < ?", (limite,))
        n2 = t.run("DELETE FROM email_queue WHERE status IN ('ENVIADO', 'ERRO') AND created_at < ?", (limite,))
        n3 = t.run("DELETE FROM agendamento_execucoes WHERE inicio < ?", (limite,))
        n4 = t.run("DELETE FROM system_actions WHERE requested_at < ?", (limite,))
        n5 = t.run("DELETE FROM auth_log WHERE quando < ?", (limite,))
    from . import tarefas_bg
    n6 = tarefas_bg.limpar(min(dias, 7))
    total = sum(max(x or 0, 0) for x in (n1, n2, n3, n4, n5, n6))
    return "sucesso", (f"Removidos registos com mais de {dias} dias: notificações lidas, emails, execuções de "
                       f"agendamentos, ações sobre serviços, acessos e dados em cache antigos ({total} no total).")


TAREFAS_PORTAL = {
    "portal.backup": {"nome": "Backup do portal", "funcao": _t_backup,
                      "descricao": "Cópia da configuração do portal (BDs, utilizadores, perfis, definições), "
                                   "como em Configuração → Backups. Mantém os N mais recentes.",
                      "padrao": {"ativo": False, "freq": "diario", "hora": "02:00", "notificar": "erro"},
                      "parametros": [{"chave": "manter", "rotulo": "Backups a manter", "tipo": "int", "padrao": 30,
                                      "min": 1, "max": 365}]},
    "portal.limpeza": {"nome": "Limpeza do histórico", "funcao": _t_limpeza,
                       "descricao": "Apaga notificações lidas, emails enviados ou com erro, execuções de agendamentos "
                                    "e ações sobre serviços mais antigos que N dias.",
                       "padrao": {"ativo": True, "freq": "diario", "hora": "03:30", "notificar": "erro"},
                       "parametros": [{"chave": "dias", "rotulo": "Dias a manter", "tipo": "int", "padrao": 90,
                                       "min": 7, "max": 3650}]},
}


# ============================================================================ registo
def definicoes() -> dict[str, dict]:
    """id -> definicao (com 'modulo' e 'chave_modulo'); as do portal primeiro."""
    out = {k: {**v, "modulo": "Portal", "chave_modulo": "portal"} for k, v in TAREFAS_PORTAL.items()}
    for k in modulos.INSTALADOS:
        m = modulos.REGISTO.get(k)
        if m:
            for tid, d in getattr(m, "tarefas", {}).items():
                out[tid] = {**d, "modulo": m.nome, "chave_modulo": m.chave}
    return out


def ativo_global() -> bool:
    return store.get_setting("agendamento_ativo") != "0"


def _config(tid: str, d: dict, row: dict | None) -> dict:
    p = d.get("padrao", {})
    par = {x["chave"]: x.get("padrao") for x in d.get("parametros", [])}
    if row and row.get("parametros"):
        try:
            par.update(json.loads(row["parametros"]))
        except ValueError:
            pass
    c = {"id": tid, "ativo": bool(row["ativo"]) if row else bool(p.get("ativo")),
         "freq": (row or {}).get("freq") or p.get("freq", "diario"),
         "hora": (row or {}).get("hora") or p.get("hora", "02:00"),
         "intervalo": (row or {}).get("intervalo") or p.get("intervalo", 60),
         "dias": (row or {}).get("dias") or p.get("dias", "1,2,3,4,5"),
         "notificar": (row or {}).get("notificar") or p.get("notificar", "erro"), "parametros": par}
    for k in ("proxima", "ultima_inicio", "ultima_fim", "ultimo_estado", "ultima_msg", "updated_by"):
        c[k] = (row or {}).get(k)
    c["a_correr"] = tid in _a_correr or c.get("ultimo_estado") == "A CORRER"
    return c


def listar(chave_modulo: str | None = None) -> list[dict]:
    """Tarefas (definicao + configuracao + modulo ativo?), opcionalmente so de um modulo."""
    ativos = set(modulos.ativos()) | {"portal"}
    with store.tx() as t:
        rows = {r["id"]: r for r in t.all("SELECT * FROM agendamentos")}
    out = []
    for tid, d in definicoes().items():
        if chave_modulo and d["chave_modulo"] != chave_modulo:
            continue
        c = _config(tid, d, rows.get(tid))
        c.update(nome=d["nome"], descricao=d.get("descricao", ""), modulo=d["modulo"], chave_modulo=d["chave_modulo"],
                 definicao_parametros=d.get("parametros", []), modulo_ativo=d["chave_modulo"] in ativos)
        c["resumo"] = resumo(c)
        out.append(c)
    return out


def obter(tid: str) -> dict | None:
    return next((x for x in listar() if x["id"] == tid), None)


def resumo(c: dict) -> str:
    f = c["freq"]
    if f == "minutos":
        return f"A cada {c['intervalo']} min"
    if f == "horas":
        return f"A cada {c['intervalo']} h"
    if f == "semanal":
        dias = ", ".join(DIAS.get(int(x), x) for x in str(c["dias"]).split(",") if x.strip().isdigit())
        return f"{dias} às {c['hora']}"
    return f"Todos os dias às {c['hora']}"


def proxima(c: dict, agora: dt.datetime | None = None) -> dt.datetime:
    agora = agora or dt.datetime.now()
    f = c["freq"]
    if f in ("minutos", "horas"):
        passo = dt.timedelta(minutes=max(1, int(c["intervalo"] or 60)) * (60 if f == "horas" else 1))
        ult = _dt(c.get("ultima_inicio"))
        return max(agora, ult + passo) if ult else agora + dt.timedelta(minutes=1)
    hh, mm = (int(x) for x in (c["hora"] or "02:00").split(":"))
    dias = {int(x) for x in str(c.get("dias") or "").split(",") if x.strip().isdigit()} if f == "semanal" else set(range(1, 8))
    dias = dias or set(range(1, 8))
    for i in range(0, 8):
        d = (agora + dt.timedelta(days=i)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if d > agora and d.isoweekday() in dias:
            return d
    return agora + dt.timedelta(days=1)


def _dt(s: str | None) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S") if s else None
    except ValueError:
        return None


def _fmt(d: dt.datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


# ============================================================================ guardar
def guardar(tid: str, dados: dict, por: str) -> dict:
    d = definicoes().get(tid)
    if not d:
        raise ValueError("Tarefa desconhecida.")
    freq = dados.get("freq", "diario")
    if freq not in FREQS:
        raise ValueError("Frequência inválida.")
    hora = (dados.get("hora") or "02:00").strip()
    if not (len(hora) == 5 and hora[2] == ":" and hora[:2].isdigit() and hora[3:].isdigit()
            and int(hora[:2]) < 24 and int(hora[3:]) < 60):
        raise ValueError("Hora inválida (HH:MM).")
    try:
        intervalo = int(dados.get("intervalo") or 60)
    except ValueError:
        raise ValueError("Intervalo inválido.") from None
    if freq == "minutos" and not 5 <= intervalo <= 1440:
        raise ValueError("O intervalo em minutos tem de estar entre 5 e 1440.")
    if freq == "horas" and not 1 <= intervalo <= 168:
        raise ValueError("O intervalo em horas tem de estar entre 1 e 168.")
    dias = ",".join(x for x in dados.get("dias", []) if x in {str(i) for i in DIAS}) if isinstance(dados.get("dias"), list) \
        else str(dados.get("dias") or "")
    if freq == "semanal" and not dias:
        raise ValueError("Escolha pelo menos um dia da semana.")
    notificar = dados.get("notificar", "erro")
    if notificar not in NOTIFICAR:
        raise ValueError("Notificação inválida.")
    par = {}
    for p in d.get("parametros", []):
        v = dados.get(f"par_{p['chave']}", p.get("padrao"))
        if p.get("tipo") == "int":
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{p['rotulo']}: tem de ser um número.") from None
            if ("min" in p and v < p["min"]) or ("max" in p and v > p["max"]):
                raise ValueError(f"{p['rotulo']}: entre {p.get('min')} e {p.get('max')}.")
        else:
            v = str(v or "").strip()[:500]
        par[p["chave"]] = v
    c = {"freq": freq, "hora": hora, "intervalo": intervalo, "dias": dias, "notificar": notificar,
         "ativo": bool(dados.get("ativo"))}
    atual = obter(tid) or {}
    c["ultima_inicio"] = atual.get("ultima_inicio")
    prox = _fmt(proxima(c)) if c["ativo"] else None
    with store.tx() as t:
        existe = t.val("SELECT COUNT(*) FROM agendamentos WHERE id = ?", (tid,))
        vals = (1 if c["ativo"] else 0, freq, hora, intervalo, dias, notificar, json.dumps(par), prox, por)
        if existe:
            t.run("UPDATE agendamentos SET ativo = ?, freq = ?, hora = ?, intervalo = ?, dias = ?, notificar = ?, "
                  "parametros = ?, proxima = ?, updated_by = ? WHERE id = ?", (*vals, tid))
        else:
            t.run("INSERT INTO agendamentos (ativo, freq, hora, intervalo, dias, notificar, parametros, proxima, "
                  "updated_by, id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (*vals, tid))
    return obter(tid)


def ativar(tid: str, ativo: bool, por: str) -> dict:
    c = obter(tid)
    if not c:
        raise ValueError("Tarefa desconhecida.")
    dados = {k: c[k] for k in ("freq", "hora", "intervalo", "notificar")}
    dados["dias"] = c["dias"]
    dados.update({f"par_{k}": v for k, v in c["parametros"].items()}, ativo=ativo)
    return guardar(tid, dados, por)


def historico(tid: str | None = None, limite: int = 50) -> list[dict]:
    with store.tx() as t:
        if tid:
            return t.all("SELECT * FROM agendamento_execucoes WHERE tarefa = ? ORDER BY id DESC LIMIT ?", (tid, limite))
        return t.all("SELECT * FROM agendamento_execucoes ORDER BY id DESC LIMIT ?", (limite,))


# ============================================================================ executar
def executar(tid: str, origem: str = "agendamento") -> tuple[str, str]:
    """Corre uma tarefa agora (no processo atual). Devolve (estado, mensagem)."""
    d = definicoes().get(tid)
    if not d:
        return "erro", "Tarefa desconhecida."
    with _lock:
        if tid in _a_correr:
            return "erro", "A tarefa já está a correr."
        _a_correr.add(tid)
    c = obter(tid)
    inicio = store.now()
    with store.tx() as t:
        eid = t.insert("agendamento_execucoes", {"tarefa": tid, "inicio": inicio, "estado": "A CORRER", "origem": origem})
        if not t.val("SELECT COUNT(*) FROM agendamentos WHERE id = ?", (tid,)):
            t.run("INSERT INTO agendamentos (id, ativo, freq, hora, intervalo, dias, notificar) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (tid, 1 if c["ativo"] else 0, c["freq"], c["hora"], c["intervalo"], c["dias"], c["notificar"]))
        t.run("UPDATE agendamentos SET ultima_inicio = ?, ultimo_estado = 'A CORRER' WHERE id = ?", (inicio, tid))
    t0 = time.time()
    try:
        estado, msg = d["funcao"](c["parametros"])
    except Exception as exc:  # noqa: BLE001
        log.exception("tarefa %s", tid)
        estado, msg = "erro", f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}"
    finally:
        with _lock:
            _a_correr.discard(tid)
    fim = store.now()
    c["ultima_inicio"] = inicio
    prox = _fmt(proxima(c)) if c["ativo"] else None
    dur = time.time() - t0
    with store.tx() as t:
        t.run("UPDATE agendamento_execucoes SET fim = ?, estado = ?, msg = ? WHERE id = ?", (fim, estado.upper(), msg, eid))
        t.run("UPDATE agendamentos SET ultima_fim = ?, ultimo_estado = ?, ultima_msg = ?, proxima = ? WHERE id = ?",
              (fim, estado.upper(), msg[:4000], prox, tid))
    if c["notificar"] == "sempre" or (c["notificar"] == "erro" and estado != "sucesso"):
        from . import eventos
        sit = {"sucesso": "sucesso", "aviso": "aviso"}.get(estado, "erro")
        eventos.emitir(EVENTO, sit, f"{d['nome']}: {'concluída' if sit == 'sucesso' else 'com avisos' if sit == 'aviso' else 'falhou'}",
                       f"{msg.splitlines()[0] if msg else ''}\n({dur:.1f} s, {origem})", link="/config/portal/agendamento")
    return estado, msg


def pendentes(agora: dt.datetime | None = None) -> list[str]:
    """Tarefas ativas cuja hora chegou (para o agente)."""
    if not ativo_global():
        return []
    agora = agora or dt.datetime.now()
    out = []
    for c in listar():
        if not c["ativo"] or not c["modulo_ativo"] or c["a_correr"]:
            continue
        p = _dt(c.get("proxima"))
        if p is None:                       # ativa mas sem proxima calculada (ex. ativada por migracao)
            with store.tx() as t:
                t.run("UPDATE agendamentos SET proxima = ? WHERE id = ?", (_fmt(proxima(c, agora)), c["id"]))
            continue
        if p <= agora:
            out.append(c["id"])
    return out


def garantir_padrao() -> None:
    """Cria a linha das tarefas ativas por omissao (para terem 'proxima')."""
    with store.tx() as t:
        existentes = {r["id"] for r in t.all("SELECT id FROM agendamentos")}
    for tid, d in definicoes().items():
        if tid not in existentes and d.get("padrao", {}).get("ativo"):
            c = _config(tid, d, None)
            dados = {k: c[k] for k in ("freq", "hora", "intervalo", "dias", "notificar")}
            dados.update({f"par_{k}": v for k, v in c["parametros"].items()}, ativo=True)
            try:
                guardar(tid, dados, "portal")
            except ValueError:
                log.exception("agendamento %s", tid)


def recuperar_presas() -> None:
    """Execucoes 'A CORRER' de um agente anterior que parou."""
    with store.tx() as t:
        t.run("UPDATE agendamento_execucoes SET estado = 'INTERROMPIDO', fim = ? WHERE estado = 'A CORRER'", (store.now(),))
        t.run("UPDATE agendamentos SET ultimo_estado = 'INTERROMPIDO' WHERE ultimo_estado = 'A CORRER'")
