"""Alarmes dos clientes (agente): limiares sobre as metricas enviadas pelo agente.

Cada alarme: metrica (disco, cpu, memoria, carga, contacto), limiar, ciclos seguidos acima do limiar antes de
disparar, severidade e canais proprios (notificacao no portal e/ou email). Avaliado em cada sincronizacao do
agente (e o "contacto" pela tarefa agendada). Estado por (alarme, cliente, item), ex. item = ponto de montagem.

Zabbix: cada metrica tem a chave equivalente de um agente Zabbix (CHAVES_ZABBIX), usada na API de exportacao
(clientes/api/zabbix/...) e para migrar os alarmes para triggers no futuro."""
import fnmatch

from . import clientes, eventos, store

METRICAS = {
    "disco": {"nome": "Disco (% usado)", "unidade": "%", "por_item": True},
    "cpu": {"nome": "CPU (% usado)", "unidade": "%"},
    "memoria": {"nome": "Memória (% usada)", "unidade": "%"},
    "carga": {"nome": "Carga (1 min, por CPU)", "unidade": ""},
    "contacto": {"nome": "Sem contacto do agente (minutos)", "unidade": " min"},
}
CHAVES_ZABBIX = {
    "disco": "vfs.fs.size[{#FSNAME},pused]",
    "cpu": "system.cpu.util",
    "memoria": "vm.memory.utilization",
    "carga": "system.cpu.load[percpu,avg1]",
    "contacto": "agent.ping (nodata)",
}
SEVERIDADES = {"aviso": "Aviso", "erro": "Crítico"}
DESTINOS = {"admins": "Administradores do portal", "modulo": "Quem tem acesso ao módulo Clientes"}


# ============================================================ regras de alarme
def listar(cliente_id=None) -> list[dict]:
    sql = ("SELECT a.*, c.nome AS cliente_nome FROM cliente_alarmes a LEFT JOIN clientes c ON c.id = a.cliente_id")
    par = ()
    if cliente_id is not None:
        sql += " WHERE (a.cliente_id IS NULL OR a.cliente_id = ?)"
        par = (cliente_id,)
    with store.tx() as t:
        rows = t.all(sql + " ORDER BY a.nome", par)
        dispar = {}
        for e in t.all("SELECT alarme_id, COUNT(*) AS n FROM cliente_alarmes_estado WHERE estado = 'disparado' "
                       "GROUP BY alarme_id"):
            dispar[e["alarme_id"]] = int(e["n"])
    for r in rows:
        r["disparados"] = dispar.get(r["id"], 0)
        r["metrica_nome"] = METRICAS.get(r["metrica"], {}).get("nome", r["metrica"])
        r["unidade"] = METRICAS.get(r["metrica"], {}).get("unidade", "")
        r["chave_zabbix"] = CHAVES_ZABBIX.get(r["metrica"], "")
    return rows


def obter(aid) -> dict | None:
    with store.tx() as t:
        return t.one("SELECT * FROM cliente_alarmes WHERE id = ?", (aid,))


def guardar(d: dict, utilizador: str, aid=None) -> int:
    nome = (d.get("nome") or "").strip()
    if not nome or len(nome) > 100:
        raise ValueError("Indique o nome (até 100 caracteres).")
    met = d.get("metrica")
    if met not in METRICAS:
        raise ValueError("Métrica inválida.")
    try:
        limiar = float(str(d.get("limiar", "")).replace(",", "."))
        ciclos = int(d.get("ciclos") or 1)
    except ValueError:
        raise ValueError("Limiar ou ciclos inválidos.") from None
    if met in ("disco", "cpu", "memoria") and not 0 < limiar <= 100:
        raise ValueError("O limiar é uma percentagem (1 a 100).")
    if limiar <= 0 or not 1 <= ciclos <= 100:
        raise ValueError("Limiar maior que 0 e ciclos de 1 a 100.")
    emails = ", ".join(e.strip() for e in (d.get("emails") or "").replace(";", ",").replace("\n", ",").split(",")
                       if e.strip())
    if any("@" not in e for e in emails.split(", ") if e):
        raise ValueError("Emails inválidos.")
    portal, email = d.get("portal") == "1", d.get("email") == "1"
    if not portal and not email:
        raise ValueError("Escolha pelo menos um canal: notificação no portal e/ou email.")
    cli = d.get("cliente_id") or None
    dados = {"nome": nome, "cliente_id": int(cli) if cli else None, "metrica": met,
             "filtro": (d.get("filtro") or "").strip()[:500] if met == "disco" else "",
             "limiar": limiar, "ciclos": ciclos, "severidade": d.get("severidade") if d.get("severidade") in SEVERIDADES else "aviso",
             "portal": 1 if portal else 0, "portal_destino": d.get("portal_destino") if d.get("portal_destino") in DESTINOS else "modulo",
             "email": 1 if email else 0, "emails": emails, "email_padrao": 1 if d.get("email_padrao") == "1" else 0,
             "recuperacao": 1 if d.get("recuperacao") == "1" else 0, "ativo": 0 if d.get("ativo") == "0" else 1}
    with store.tx() as t:
        if aid:
            t.update("cliente_alarmes", int(aid), dados)
            t.run("DELETE FROM cliente_alarmes_estado WHERE alarme_id = ?", (int(aid),))   # reavalia do zero
            return int(aid)
        dados.update(criado_em=store.now(), criado_por=utilizador)
        return t.insert("cliente_alarmes", dados)


def apagar(aid) -> None:
    with store.tx() as t:
        t.run("DELETE FROM cliente_alarmes_estado WHERE alarme_id = ?", (aid,))
        t.run("DELETE FROM cliente_alarmes WHERE id = ?", (aid,))


def ativos(cliente_id=None) -> list[dict]:
    """Alarmes disparados neste momento."""
    sql = ("SELECT e.*, a.nome, a.metrica, a.limiar, a.severidade, c.nome AS cliente_nome FROM cliente_alarmes_estado e "
           "JOIN cliente_alarmes a ON a.id = e.alarme_id JOIN clientes c ON c.id = e.cliente_id WHERE e.estado = 'disparado'")
    par = ()
    if cliente_id:
        sql += " AND e.cliente_id = ?"
        par = (cliente_id,)
    with store.tx() as t:
        rows = t.all(sql + " ORDER BY e.desde DESC", par)
    for r in rows:
        r["unidade"] = METRICAS.get(r["metrica"], {}).get("unidade", "")
    return rows


# ============================================================ avaliacao
def valores(metrica: str, c: dict, filtro: str = "") -> list[tuple[str, float]]:
    """[(item, valor)] da metrica para o cliente, a partir do ultimo envio do agente."""
    i = c.get("info") or {}
    if metrica == "disco":
        pads = [p.strip() for p in (filtro or "").replace(";", ",").split(",") if p.strip()]
        return [(d["ponto"], float(d["pct"])) for d in i.get("discos") or []
                if d.get("pct") is not None and (not pads or any(fnmatch.fnmatch(d["ponto"], p) for p in pads))]
    if metrica == "cpu" and isinstance(i.get("cpu"), (int, float)):
        return [("cpu", float(i["cpu"]))]
    if metrica == "memoria" and (i.get("mem") or {}).get("pct") is not None:
        return [("memoria", float(i["mem"]["pct"]))]
    if metrica == "carga" and i.get("load") and i.get("cpus"):
        return [("carga", round(float(i["load"][0]) / max(1, int(i["cpus"])), 2))]
    if metrica == "contacto":
        seg = clientes.contacto(c).get("seg")
        return [("contacto", round(seg / 60, 1))] if seg is not None else []
    return []


def _canais(a: dict) -> list[dict]:
    out = []
    if a["portal"]:
        out.append({"canal": "portal", "nome": a["nome"], "destino": {a["portal_destino"] or "modulo": True}})
    if a["email"]:
        dest = {"emails": [e for e in (a["emails"] or "").split(", ") if e]}
        if a["email_padrao"]:
            dest["padrao"] = True
        out.append({"canal": "email", "nome": a["nome"], "destino": dest})
    return out


def _notificar(a: dict, c: dict, item: str, valor: float, disparou: bool) -> None:
    un = METRICAS[a["metrica"]]["unidade"]
    alvo = f" {item}" if a["metrica"] == "disco" else ""
    if disparou:
        titulo = f"{c['nome']}: {a['nome']}{alvo} = {valor:g}{un} (limiar {a['limiar']:g}{un})"
        sit = est = "erro" if a["severidade"] == "erro" else "aviso"
    else:
        titulo = f"{c['nome']}: {a['nome']}{alvo} normalizou ({valor:g}{un})"
        sit, est = "recuperou", "ok"
    texto = (f"Cliente: {c['nome']} ({c['endereco']})\nAlarme: {a['nome']} — {METRICAS[a['metrica']]['nome']}"
             f"{(' em ' + item) if a['metrica'] == 'disco' else ''}\nValor: {valor:g}{un}; limiar: {a['limiar']:g}{un}"
             f" ({a['ciclos']} ciclo(s) seguido(s)).")
    link = f"/clientes/cliente/{c['id']}"
    with store.tx() as t:
        t.insert("cliente_eventos", {"cliente_id": c["id"], "regra": f"A{a['id']}", "quando": store.now(),
                                     "recebido": store.now(), "estado": est, "origem": "alarme", "mensagem": titulo})
    eventos.entregar("clientes.alarme", sit, titulo, texto, link, _canais(a))


def avaliar(c: dict, metricas: tuple = ("disco", "cpu", "memoria", "carga")) -> int:
    """Avalia os alarmes ativos que se aplicam ao cliente. Devolve quantos mudaram de estado."""
    mudou = 0
    alarmes = [a for a in listar(c["id"]) if a["ativo"] and a["metrica"] in metricas]
    if not alarmes:
        return 0
    with store.tx() as t:
        est = {(e["alarme_id"], e["item"]): e for e in t.all(
            "SELECT * FROM cliente_alarmes_estado WHERE cliente_id = ?", (c["id"],))}
    for a in alarmes:
        for item, v in valores(a["metrica"], c, a["filtro"]):
            e = est.get((a["id"], item))
            acima = v >= a["limiar"]
            if not e:
                with store.tx() as t:
                    t.insert("cliente_alarmes_estado", {"alarme_id": a["id"], "cliente_id": c["id"], "item": item[:255],
                                                        "estado": "ok", "contagem": 0, "valor": v, "atualizado": store.now()})
                    e = t.one("SELECT * FROM cliente_alarmes_estado WHERE alarme_id = ? AND cliente_id = ? AND item = ?",
                              (a["id"], c["id"], item[:255]))
            cont = (int(e["contagem"]) + 1) if acima else 0
            novo = e["estado"]
            if acima and cont >= int(a["ciclos"]) and e["estado"] != "disparado":
                novo = "disparado"
                _notificar(a, c, item, v, True)
                mudou += 1
            elif not acima and e["estado"] == "disparado":
                novo = "ok"
                if a["recuperacao"]:
                    _notificar(a, c, item, v, False)
                mudou += 1
            with store.tx() as t:
                t.run("UPDATE cliente_alarmes_estado SET estado = ?, contagem = ?, valor = ?, atualizado = ?, "
                      "desde = CASE WHEN ? <> estado THEN ? ELSE desde END WHERE id = ?",
                      (novo, cont, v, store.now(), novo, store.now(), e["id"]))
    return mudou


def avaliar_contacto() -> int:
    """Chamado pela tarefa agendada: alarmes de 'sem contacto' (minutos)."""
    n = 0
    for c in clientes.listar():
        if c.get("ultimo_contacto"):
            n += avaliar(c, ("contacto",))
    return n


# ============================================================ Zabbix (exportacao)
def zabbix_metricas(c: dict) -> dict:
    """Valores do cliente com as chaves de um agente Zabbix (para itens HTTP agent / dependentes)."""
    i = c.get("info") or {}
    ct = clientes.contacto(c)
    m = {"agent.ping": 1 if ct["cor"] == "verde" else 0, "agent.version": c.get("versao") or "",
         "system.hostname": i.get("hostname") or "", "system.sw.os": i.get("so") or "", "system.uname": i.get("kernel") or "",
         "system.cpu.num": i.get("cpus"), "system.cpu.util": i.get("cpu"), "system.uptime": i.get("uptime"),
         "portal.ultimo_contacto": c.get("ultimo_contacto") or "", "portal.segundos_sem_contacto": ct.get("seg"),
         "portal.intervalo": c.get("intervalo")}
    if i.get("load"):
        for k, v in zip(("avg1", "avg5", "avg15"), i["load"]):
            m[f"system.cpu.load[all,{k}]"] = v
            if i.get("cpus"):
                m[f"system.cpu.load[percpu,{k}]"] = round(v / max(1, int(i["cpus"])), 2)
    if i.get("mem"):
        m["vm.memory.size[total]"] = i["mem"].get("total")
        m["vm.memory.size[available]"] = i["mem"].get("livre")
        m["vm.memory.utilization"] = i["mem"].get("pct")
    for d in i.get("discos") or []:
        p = d["ponto"]
        m[f"vfs.fs.size[{p},total]"] = d.get("total")
        m[f"vfs.fs.size[{p},free]"] = d.get("livre")
        m[f"vfs.fs.size[{p},used]"] = (d.get("total") or 0) - (d.get("livre") or 0)
        m[f"vfs.fs.size[{p},pused]"] = d.get("pct")
    m["portal.alarmes_ativos"] = len(ativos(c["id"]))
    return m


def zabbix_descoberta() -> dict:
    """LLD (low-level discovery): clientes e sistemas de ficheiros."""
    data = []
    for c in clientes.listar():
        host = c.get("zabbix_host") or c["nome"]
        data.append({"{#CLIENTE}": c["nome"], "{#HOST}": host, "{#ENDERECO}": c["endereco"], "{#FSNAME}": "", "{#FSTYPE}": ""})
        for d in (c.get("info") or {}).get("discos") or []:
            data.append({"{#CLIENTE}": c["nome"], "{#HOST}": host, "{#ENDERECO}": c["endereco"],
                         "{#FSNAME}": d["ponto"], "{#FSTYPE}": d.get("fs", "")})
    return {"data": data}


def zabbix_alarmes() -> list[dict]:
    """Alarmes do portal no formato de um trigger Zabbix (expressao sugerida), para migracao futura."""
    sev = {"aviso": "WARNING", "erro": "HIGH"}
    out = []
    for a in listar():
        chave = CHAVES_ZABBIX[a["metrica"]]
        if a["metrica"] == "contacto":
            expr = f"nodata(/{{HOST.HOST}}/agent.ping,{int(a['limiar'])}m)=1"
        else:
            expr = f"min(/{{HOST.HOST}}/{chave},#{a['ciclos']})>={a['limiar']:g}"
        out.append({"nome": a["nome"], "cliente": a.get("cliente_nome") or "*", "metrica": a["metrica"], "chave": chave,
                    "filtro": a["filtro"] or "", "limiar": a["limiar"], "ciclos": a["ciclos"],
                    "severidade": sev.get(a["severidade"], "WARNING"), "expressao": expr, "ativo": bool(a["ativo"])})
    return out
