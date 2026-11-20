"""Eventos, regras, notificacoes do portal (sininho) e fila de emails.

Um modulo emite um evento (ex. "oracle.execucao", situacao "falhado"). As regras ativas desse evento e
dessa situacao decidem:
  - canal "portal": notificacoes no sininho, para os utilizadores indicados;
  - canal "email" : mensagens na fila (tabela email_queue), enviadas pelo agente (app/smtp_envio.py),
                    so se o modulo SMTP estiver ativo e o envio automatico ligado.
Regras em SMTP > Regras (email) e SMTP > Notificacoes (portal).
"""
import json
import logging
import re
import threading

from . import modulos, settings, store

log = logging.getLogger("eventos")

NIVEL = {"sucesso": "ok", "aviso": "aviso", "erro": "erro", "falhado": "erro", "parado": "aviso",
         "falhou": "erro", "iniciado": "ok", "acima": "aviso", "recuperou": "ok", "perdido": "erro", "info": "info"}

# Destinos possiveis de uma regra (chaves do JSON "destino")
#   emails: [..]   padrao: bool (destinatarios por omissao do SMTP)   lancador: bool (quem lancou)
#   perfis: [ids]  admins: bool (quem gere o portal)                 modulo: bool (quem tem acesso ao modulo)


# ============================================================================ regras
def _regra(r: dict) -> dict:
    if r:
        r["situacoes"] = json.loads(r.get("situacoes") or "[]")
        r["destino"] = json.loads(r.get("destino") or "{}")
        r["ativo"] = bool(r["ativo"])
    return r


def listar_regras(canal: str | None = None) -> list[dict]:
    with store.tx() as t:
        if canal:
            rows = t.all("SELECT * FROM notification_rules WHERE canal = ? ORDER BY evento, nome", (canal,))
        else:
            rows = t.all("SELECT * FROM notification_rules ORDER BY canal, evento, nome")
    return [_regra(r) for r in rows]


def obter_regra(rid) -> dict | None:
    with store.tx() as t:
        return _regra(t.one("SELECT * FROM notification_rules WHERE id = ?", (rid,)))


def guardar_regra(dados: dict, rid=None) -> int:
    row = {"nome": dados["nome"][:150], "canal": dados["canal"], "evento": dados["evento"],
           "situacoes": json.dumps(dados.get("situacoes", [])), "parametro": dados.get("parametro"),
           "destino": json.dumps(dados.get("destino", {})), "ativo": 1 if dados.get("ativo", True) else 0,
           "updated_at": store.now()}
    with store.tx() as t:
        if rid:
            t.update("notification_rules", rid, row)
            return int(rid)
        return t.insert("notification_rules", {**row, "created_at": store.now()})


def apagar_regra(rid) -> None:
    with store.tx() as t:
        t.run("DELETE FROM notification_rules WHERE id = ?", (rid,))


def regras_iniciais() -> None:
    """Na primeira vez: regras uteis por omissao (podem ser alteradas ou apagadas)."""
    if store.get_setting("regras_iniciais"):
        regras_iniciais_2()
        return
    base = [
        ("Execução Oracle terminada (avisar quem a lançou)", "portal", "oracle.execucao",
         ["sucesso", "aviso", "erro", "falhado"], None, {"lancador": True}),
        ("Serviço parado ou com falha", "portal", "sistema.servico", ["parado", "falhou"], None, {"admins": True}),
        ("Disco acima de 90 %", "portal", "sistema.disco", ["acima"], 90, {"admins": True}),
        ("Falha na ligação SMTP", "portal", "smtp.ligacao", ["falhou"], None, {"admins": True}),
        ("Execução Oracle com erros ou falhada", "email", "oracle.execucao", ["erro", "falhado"], None,
         {"padrao": True, "lancador": True}),
        ("Serviço parado ou com falha", "email", "sistema.servico", ["parado", "falhou"], None, {"padrao": True}),
        ("Disco acima de 90 %", "email", "sistema.disco", ["acima"], 90, {"padrao": True}),
    ]
    for nome, canal, ev, sit, par, dest in base:
        guardar_regra({"nome": nome, "canal": canal, "evento": ev, "situacoes": sit, "parametro": par,
                       "destino": dest, "ativo": True})
    store.save_settings({"regras_iniciais": "1"})
    regras_iniciais_2()


def regras_iniciais_2() -> None:
    """Regras das tarefas agendadas e da verificacao de tablespaces (versao com agendamentos)."""
    if store.get_setting("regras_iniciais_2"):
        regras_iniciais_3()
        return
    base = [
        ("Tarefa agendada falhou", "portal", "portal.agendamento", ["erro"], None, {"admins": True}),
        ("Tarefa agendada falhou", "email", "portal.agendamento", ["erro"], None, {"padrao": True}),
        ("Tablespace acima do limiar", "portal", "oracle.tablespace", ["acima", "erro"], None, {"modulo": True}),
        ("Tablespace acima do limiar", "email", "oracle.tablespace", ["acima"], None, {"padrao": True}),
    ]
    for nome, canal, ev, sit, par, dest in base:
        guardar_regra({"nome": nome, "canal": canal, "evento": ev, "situacoes": sit, "parametro": par,
                       "destino": dest, "ativo": True})
    store.save_settings({"regras_iniciais_2": "1"})
    regras_iniciais_3()


def regras_iniciais_3() -> None:
    """Regras do modulo Clientes (agente cliente)."""
    if store.get_setting("regras_iniciais_3"):
        return
    base = [
        ("Cliente: erro ou aviso de um script", "portal", "clientes.notificacao", ["erro", "aviso"], None, {"modulo": True}),
        ("Cliente: erro de um script", "email", "clientes.notificacao", ["erro"], None, {"padrao": True}),
        ("Cliente sem contacto do agente", "portal", "clientes.contacto", ["perdido", "recuperou"], None, {"admins": True}),
        ("Cliente sem contacto do agente", "email", "clientes.contacto", ["perdido"], None, {"padrao": True}),
    ]
    for nome, canal, ev, sit, par, dest in base:
        guardar_regra({"nome": nome, "canal": canal, "evento": ev, "situacoes": sit, "parametro": par,
                       "destino": dest, "ativo": True})
    store.save_settings({"regras_iniciais_3": "1"})


# ============================================================================ destinatarios
def _utilizadores() -> list[dict]:
    """[{key, username, email, perms, profile_id}] incluindo o administrador local."""
    out = []
    adm = settings.admin()
    if adm:
        out.append({"key": "admin", "username": adm["username"], "email": adm.get("email") or "",
                    "admin": True, "perms": set(), "profile_id": None})
    perfis = {p["id"]: p for p in store.list_profiles()}
    for u in store.list_users():
        if not u.get("active"):
            continue
        p = perfis.get(u.get("profile_id"))
        out.append({"key": str(u["id"]), "username": u["username"], "email": u.get("email") or "", "admin": False,
                    "perms": set(p["permissions"]) if p else set(), "profile_id": u.get("profile_id")})
    return out


def _resolver(destino: dict, evento: str, autor: str | None) -> list[dict]:
    mod = evento.split(".", 1)[0]
    res = {}
    for u in _utilizadores():
        if (destino.get("lancador") and autor and u["username"] == autor) \
           or (destino.get("admins") and (u["admin"] or "config.portal" in u["perms"])) \
           or (destino.get("modulo") and (u["admin"] or f"modulo.{mod}" in u["perms"])) \
           or (u["profile_id"] and u["profile_id"] in (destino.get("perfis") or [])):
            res[u["key"]] = u
    return list(res.values())


def _emails(destino: dict, evento: str, autor: str | None) -> list[str]:
    out = [e.strip() for e in destino.get("emails") or [] if "@" in e]
    if destino.get("padrao"):
        out += [e.strip() for e in (store.get_setting("smtp_destinatarios") or "").replace(";", ",").split(",")
                if "@" in e]
    out += [u["email"] for u in _resolver({k: v for k, v in destino.items() if k != "padrao"}, evento, autor)
            if u["email"]]
    return list(dict.fromkeys(e.lower() for e in out))


# ============================================================================ emitir
def emitir(evento: str, situacao: str, titulo: str, texto: str = "", link: str = "",
           autor: str | None = None, dados: dict | None = None) -> None:
    """Avalia as regras e cria notificacoes / emails. Nunca levanta excecoes (so regista)."""
    try:
        _emitir(evento, situacao, titulo, texto, link, autor, dados or {})
    except Exception:  # noqa: BLE001
        log.exception("erro ao emitir %s/%s", evento, situacao)


def _emitir(evento, situacao, titulo, texto, link, autor, dados):
    regras = [r for r in listar_regras() if r["ativo"] and r["evento"] == evento and situacao in r["situacoes"]]
    if evento == "sistema.disco":            # cada regra de disco tem o seu limiar
        regras = [r for r in regras if r["parametro"] not in (None, "")
                  and str(dados.get("limiar")).strip() == str(r["parametro"]).strip()]
    if evento == "clientes.notificacao":     # parametro = IDs das regras do agente (vazio = todas)
        regras = [r for r in regras if not (r["parametro"] or "").strip()
                  or str(dados.get("regra", "")) in re.split(r"[\s,;]+", r["parametro"].strip())]
    if not regras:
        return
    nivel = NIVEL.get(situacao, "info")
    smtp_on = "smtp" in modulos.ativos() and store.get_setting("smtp_ativo") == "1"
    novos_emails = 0
    with store.tx() as t:
        for r in regras:
            if r["canal"] == "portal":
                for u in _resolver(r["destino"], evento, autor):
                    t.insert("notifications", {"user_key": u["key"], "created_at": store.now(), "evento": evento,
                                               "nivel": nivel, "titulo": titulo[:255], "texto": texto[:4000],
                                               "link": link[:255], "lida": 0})
            elif r["canal"] == "email" and smtp_on:
                dest = _emails(r["destino"], evento, autor)
                if not dest:
                    continue
                t.insert("email_queue", {"created_at": store.now(), "tipo": "email", "evento": evento,
                                         "regra": r["nome"][:150], "destinatarios": ", ".join(dest),
                                         "assunto": titulo[:255], "corpo": corpo_email(titulo, texto, link),
                                         "status": "PENDENTE", "tentativas": 0, "pedido_por": autor or ""})
                novos_emails += 1
    if novos_emails:
        enviar_ja_se_sem_agente()


def corpo_email(titulo: str, texto: str, link: str) -> str:
    base = (store.get_setting("smtp_url_portal") or "").rstrip("/")
    nome = store.get_setting("portal_name") or "Portal"
    partes = [titulo, "=" * min(len(titulo), 70), "", texto or ""]
    if link:
        partes += ["", f"Ver no portal: {base + link if base else link}"]
    partes += ["", "--", f"{nome} - mensagem automática (regras em SMTP > Regras)."]
    return "\n".join(partes)


def enviar_ja_se_sem_agente() -> None:
    """Sem agente, o proprio portal envia a fila numa thread (com agente, e ele que envia)."""
    from . import agente_info
    if agente_info.ativo():
        return
    from . import smtp_envio
    threading.Thread(target=smtp_envio.processar_fila, daemon=True).start()


# ============================================================================ sininho
def nao_lidas(user_key: str) -> int:
    with store.tx() as t:
        return int(t.val("SELECT COUNT(*) FROM notifications WHERE user_key = ? AND lida = 0", (user_key,)) or 0)


def listar(user_key: str, limite: int = 20) -> list[dict]:
    with store.tx() as t:
        return t.all("SELECT * FROM notifications WHERE user_key = ? ORDER BY id DESC LIMIT ?",
                     (user_key, int(limite)))


def marcar_lida(user_key: str, nid=None) -> None:
    with store.tx() as t:
        if nid is None:
            t.run("UPDATE notifications SET lida = 1 WHERE user_key = ? AND lida = 0", (user_key,))
        else:
            t.run("UPDATE notifications SET lida = 1 WHERE user_key = ? AND id = ?", (user_key, nid))


def historico(limite: int = 200) -> list[dict]:
    with store.tx() as t:
        return t.all("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (int(limite),))


def limpar_antigas(dias: int = 90) -> None:
    with store.tx() as t:
        t.run("DELETE FROM notifications WHERE created_at < ? AND lida = 1", (store.ago(days=dias),))
