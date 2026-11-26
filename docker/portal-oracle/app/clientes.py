"""Clientes com o agente do portal (agente-cliente/agente-portal.py), estilo agente Zabbix.

- cada cliente tem um id (codigo) e um token (cifrado no portal; o agente guarda-o cifrado no seu .env);
- o agente sincroniza a cada N segundos (definido no cliente): estado do servidor e eventos;
- regras de notificacao: id gerado, nome, descricao e, opcionalmente, um ficheiro de log cuja ultima
  linha o agente envia quando muda; outros scripts notificam com 'agente-portal notificar <ID> ...';
- teste de ligacao: DNS, porta SSH e SSH com a chave do portal (known_hosts proprio)."""
import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import subprocess

from . import crypto, eventos, settings, store

FAMILIAS = {"debian": "Debian / Ubuntu", "redhat": "Red Hat / Rocky / Alma / Oracle Linux"}
ESTADOS_EV = {"ok": "OK", "aviso": "Aviso", "erro": "Erro", "info": "Informação"}
SITUACAO = {"ok": "sucesso", "aviso": "aviso", "erro": "erro", "info": "info"}
NOME_RE = re.compile(r"^[\w .@()-]{1,100}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,253}$|^[0-9a-fA-F:]+$")
UTIL_RE = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$")
AGENTE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agente-cliente")


# ============================================================ utilitarios
def _novo_codigo(prefixo: str) -> str:
    return prefixo + secrets.token_hex(4).upper()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _segundos_desde(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return (datetime.datetime.now() - datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds()
    except ValueError:
        return None


def contacto(c: dict) -> dict:
    """Estado do contacto: verde (dentro do intervalo), laranja (atrasado), vermelho (sem contacto), cinza (nunca)."""
    seg = _segundos_desde(c.get("ultimo_contacto"))
    iv = int(c.get("intervalo") or 60)
    if seg is None:
        return {"cor": "cinza", "texto": "Nunca contactou", "seg": None}
    if seg <= 3 * iv + 15:
        return {"cor": "verde", "texto": "Online", "seg": int(seg)}
    if seg <= 10 * iv + 60:
        return {"cor": "laranja", "texto": "Atrasado", "seg": int(seg)}
    return {"cor": "vermelho", "texto": "Sem contacto", "seg": int(seg)}


def _cliente(row: dict | None) -> dict | None:
    if not row:
        return None
    c = dict(row)
    try:
        c["info"] = json.loads(c.get("info") or "{}")
    except ValueError:
        c["info"] = {}
    c["contacto"] = contacto(c)
    return c


# ============================================================ clientes
def listar() -> list[dict]:
    with store.tx() as t:
        rows = t.all("SELECT * FROM clientes ORDER BY nome")
        evs = {r["cliente_id"]: r for r in t.all(
            "SELECT cliente_id, COUNT(*) AS n, SUM(CASE WHEN estado = 'erro' THEN 1 ELSE 0 END) AS erros "
            "FROM cliente_eventos WHERE recebido >= ? GROUP BY cliente_id", (store.ago(hours=24),))}
    out = []
    for r in rows:
        c = _cliente(r)
        e = evs.get(c["id"]) or {}
        c["ev24"], c["erros24"] = int(e.get("n") or 0), int(e.get("erros") or 0)
        out.append(c)
    return out


def obter(cid) -> dict | None:
    with store.tx() as t:
        return _cliente(t.one("SELECT * FROM clientes WHERE id = ?", (cid,)))


def por_codigo(codigo: str) -> dict | None:
    with store.tx() as t:
        return _cliente(t.one("SELECT * FROM clientes WHERE codigo = ?", (codigo,)))


def token(c: dict) -> str:
    try:
        return crypto.decrypt(c["token_enc"]) if c.get("token_enc") else ""
    except RuntimeError:
        return ""


def guardar(d: dict, utilizador: str, cid=None) -> int:
    nome, end = (d.get("nome") or "").strip(), (d.get("endereco") or "").strip()
    if not NOME_RE.match(nome):
        raise ValueError("Nome inválido (até 100 caracteres: letras, números, espaço, . _ - @ ( )).")
    if not HOST_RE.match(end):
        raise ValueError("IP ou DNS inválido.")
    util = (d.get("ssh_utilizador") or "").strip()
    if util and not UTIL_RE.match(util):
        raise ValueError("Utilizador SSH inválido.")
    try:
        porta = int(d.get("ssh_porta") or 22)
        assert 1 <= porta <= 65535
    except (ValueError, AssertionError):
        raise ValueError("Porta SSH inválida.") from None
    fam = d.get("familia") if d.get("familia") in FAMILIAS else "redhat"
    dados = {"nome": nome, "endereco": end, "descricao": (d.get("descricao") or "").strip()[:500],
             "vm": 1 if d.get("vm") in ("1", "on", 1, True) else 0, "familia": fam,
             "ssh_utilizador": util, "ssh_porta": porta,
             "zabbix_host": (d.get("zabbix_host") or "").strip()[:128]}
    with store.tx() as t:
        outro = t.one("SELECT id FROM clientes WHERE nome = ?", (nome,))
        if outro and str(outro["id"]) != str(cid or ""):
            raise ValueError("Já existe um cliente com esse nome.")
        if cid:
            t.update("clientes", int(cid), dados)
            return int(cid)
        tk = secrets.token_urlsafe(32)
        dados.update(codigo=_novo_codigo("C"), token_enc=crypto.encrypt(tk), token_hash=_hash(tk),
                     criado_em=store.now(), criado_por=utilizador, intervalo=60)
        return t.insert("clientes", dados)


def novo_token(cid) -> str:
    tk = secrets.token_urlsafe(32)
    with store.tx() as t:
        t.update("clientes", int(cid), {"token_enc": crypto.encrypt(tk), "token_hash": _hash(tk)})
    return tk


def apagar(cid) -> None:
    with store.tx() as t:
        t.run("DELETE FROM cliente_eventos WHERE cliente_id = ?", (cid,))
        t.run("DELETE FROM cliente_alarmes_estado WHERE cliente_id = ?", (cid,))
        t.run("DELETE FROM cliente_alarmes WHERE cliente_id = ?", (cid,))
        t.run("DELETE FROM cliente_regras WHERE cliente_id = ?", (cid,))
        t.run("DELETE FROM cliente_dados WHERE cliente_id = ?", (cid,))
        t.run("DELETE FROM cliente_logs WHERE cliente_id = ?", (cid,))
        t.run("DELETE FROM clientes WHERE id = ?", (cid,))


# ============================================================ regras de notificacao
def regras(cliente_id=None, so_ativas: bool = False) -> list[dict]:
    sql = "SELECT r.*, c.nome AS cliente_nome FROM cliente_regras r LEFT JOIN clientes c ON c.id = r.cliente_id"
    cond, par = [], []
    if cliente_id is not None:
        cond.append("(r.cliente_id IS NULL OR r.cliente_id = ?)")
        par.append(cliente_id)
    if so_ativas:
        cond.append("r.ativo = 1")
    with store.tx() as t:
        rows = t.all(sql + (" WHERE " + " AND ".join(cond) if cond else "") + " ORDER BY r.nome", tuple(par))
        ult = {r["regra"]: r for r in t.all(
            "SELECT e.regra, e.estado, e.recebido, e.mensagem FROM cliente_eventos e "
            "JOIN (SELECT regra, MAX(id) AS m FROM cliente_eventos GROUP BY regra) x ON x.m = e.id")}
    for r in rows:
        r["ultimo"] = ult.get(r["codigo"])
    return rows


def obter_regra(rid) -> dict | None:
    with store.tx() as t:
        return t.one("SELECT * FROM cliente_regras WHERE id = ?", (rid,))


def guardar_regra(d: dict, utilizador: str, rid=None) -> int:
    nome = (d.get("nome") or "").strip()
    if not nome or len(nome) > 100:
        raise ValueError("Indique o nome (até 100 caracteres).")
    log = (d.get("log_path") or "").strip()
    if log and (not log.startswith("/") or len(log) > 500 or any(ch in log for ch in "\n\r\0")):
        raise ValueError("O log tem de ser um caminho absoluto (ex. /u01/scripts/logs/rman.log).")
    for k in ("padrao_erro", "padrao_aviso"):
        if d.get(k):
            try:
                re.compile(d[k])
            except re.error as exc:
                raise ValueError(f"Expressão inválida em {k}: {exc}") from None
    cli = d.get("cliente_id") or None
    dados = {"nome": nome, "descricao": (d.get("descricao") or "").strip()[:500], "log_path": log,
             "cliente_id": int(cli) if cli else None, "padrao_erro": (d.get("padrao_erro") or "").strip()[:200],
             "padrao_aviso": (d.get("padrao_aviso") or "").strip()[:200], "ativo": 0 if d.get("ativo") == "0" else 1}
    with store.tx() as t:
        if rid:
            t.update("cliente_regras", int(rid), dados)
            return int(rid)
        dados.update(codigo=_novo_codigo("R"), criado_em=store.now(), criado_por=utilizador)
        return t.insert("cliente_regras", dados)


def apagar_regra(rid) -> None:
    with store.tx() as t:
        t.run("DELETE FROM cliente_regras WHERE id = ?", (rid,))


def listar_eventos(cliente_id=None, regra: str = "", estado: str = "", q: str = "", limite: int = 300) -> list[dict]:
    cond, par = [], []
    if cliente_id:
        cond.append("e.cliente_id = ?"), par.append(cliente_id)
    if regra:
        cond.append("e.regra = ?"), par.append(regra)
    if estado in ESTADOS_EV:
        cond.append("e.estado = ?"), par.append(estado)
    if q:
        cond.append("LOWER(e.mensagem) LIKE ?"), par.append(f"%{q.lower()}%")
    with store.tx() as t:
        return t.all("SELECT e.*, c.nome AS cliente_nome, r.nome AS regra_nome FROM cliente_eventos e "
                     "LEFT JOIN clientes c ON c.id = e.cliente_id LEFT JOIN cliente_regras r ON r.codigo = e.regra"
                     + (" WHERE " + " AND ".join(cond) if cond else "") + f" ORDER BY e.id DESC LIMIT {int(limite)}",
                     tuple(par))


# ============================================================ API do agente
def autenticar(codigo: str, tk: str) -> dict | None:
    c = por_codigo(codigo or "")
    if not c or not c.get("token_hash") or not tk:
        return None
    return c if hmac.compare_digest(c["token_hash"], _hash(tk)) else None


# ============================================================ dados extra do agente (processos, sistema, logs, ...)
TIPOS_DADOS = {"processos": "Processos", "sistema": "Sistema", "logs": "Logs", "atualizacoes": "Atualizações pendentes",
               "ficheiros": "Ficheiros de log", "scan": "Ficheiros em /var/log", "firewall": "Firewall"}
TIPOS_PEDIDO = ("processos", "sistema", "logs", "atualizacoes", "scan")


def guardar_dados(cid: int, extras: dict) -> None:
    if not isinstance(extras, dict):
        return
    agora = store.now()
    with store.tx() as t:
        for tipo, dados in extras.items():
            if tipo not in TIPOS_DADOS or not isinstance(dados, dict):
                continue
            texto = json.dumps(dados)[:2_000_000]
            t.run("DELETE FROM cliente_dados WHERE cliente_id = ? AND tipo = ?", (cid, tipo))
            t.run("INSERT INTO cliente_dados (cliente_id, tipo, atualizado, dados) VALUES (?, ?, ?, ?)",
                  (cid, tipo, agora, texto))
    a_verificar = extras.get("_a_verificar")
    store.save_settings({f"cliente_verificar:{cid}": ",".join(a_verificar) if isinstance(a_verificar, list) else ""})


def dados(cid: int) -> dict:
    """tipo -> {"dados", "atualizado"} (o que o agente enviou por ultimo)."""
    out = {}
    with store.tx() as t:
        for r in t.all("SELECT tipo, atualizado, dados FROM cliente_dados WHERE cliente_id = ?", (cid,)):
            try:
                out[r["tipo"]] = {"dados": json.loads(r["dados"] or "{}"), "atualizado": r["atualizado"]}
            except ValueError:
                pass
    v = store.get_setting(f"cliente_verificar:{cid}") or ""
    out["_a_verificar"] = [x for x in v.split(",") if x]
    out["_pedido"] = store.get_setting(f"cliente_pedido:{cid}") or ""
    return out


NOMES_PEDIDO = {"processos": "processos", "sistema": "sistema", "logs": "logs", "atualizacoes": "atualizações pendentes",
                "scan": "ficheiros em /var/log", "ficheiros": "ficheiros de log"}


def pedir_dados(cid: int, tipos: list[str], por: str = "", motivo: str = "") -> int:
    """Pedido de dados ao agente: fica em Clientes -> Pedidos com estado e output. O agente 1.5+ recebe-o em
    ~5 s (consulta de comandos), os anteriores na sincronizacao seguinte. Fecha quando os dados chegam."""
    atual = [x for x in (store.get_setting(f"cliente_pedido:{cid}") or "").split(",") if x]
    tipos = [x for x in tipos if x in TIPOS_PEDIDO] or list(TIPOS_PEDIDO[:4])
    store.save_settings({f"cliente_pedido:{cid}": ",".join(dict.fromkeys(atual + tipos))})
    c = obter(cid) or {}
    cmd = criar_comando(cid, "dados", json.dumps({"tipos": tipos, "faltam": tipos}), por)
    comando_linha(cmd, f"pedido por {por or 'portal'}: {', '.join(NOMES_PEDIDO.get(x, x) for x in tipos)}"
                       + (f" ({motivo})" if motivo else ""))
    if _v(c.get("versao") or "0") >= (1, 5, 0):
        comando_linha(cmd, "à espera que o agente receba o pedido (consulta a cada 5 s)")
    elif _v(c.get("versao") or "0") >= (1, 3, 0):
        comando_linha(cmd, f"o agente {c.get('versao')} recebe o pedido na próxima sincronização (até {c.get('intervalo') or 60} s)")
    else:
        comando_linha(cmd, f"o agente {c.get('versao') or '(sem versão)'} não envia estes dados: atualize-o (aba Agente)")
        comando_estado(cmd, "ERRO")
    return cmd


def _resumo_dados(tipo: str, d: dict) -> str:
    if not isinstance(d, dict):
        return ""
    if d.get("erro"):
        return "erro: " + str(d["erro"])[:200]
    if tipo == "processos":
        return f"{d.get('total')} processos, {d.get('threads')} threads"
    if tipo == "logs":
        return f"{len(d.get('linhas') or [])} linhas ({d.get('fonte')})"
    if tipo == "sistema":
        return f"{len(d.get('ips') or [])} endereço(s), {len(d.get('portas') or [])} portas à escuta, {len(d.get('servicos_falhados') or [])} serviço(s) com falha"
    if tipo == "atualizacoes":
        return f"{d.get('total')} pacote(s) pendente(s), {d.get('seguranca')} de segurança" + (", reinício necessário" if d.get("reinicio") else "")
    if tipo == "scan":
        return f"{len(d.get('ficheiros') or [])} ficheiro(s) em {d.get('base')}"
    if tipo == "ficheiros":
        erros = sum(1 for x in d.values() if isinstance(x, dict) and x.get("erro"))
        return f"{len(d)} ficheiro(s) de log" + (f", {erros} com erro" if erros else "")
    return ""


def _dados_entregues(cid: int, como: str) -> None:
    with store.tx() as t:
        pend = t.all("SELECT id FROM cliente_comandos WHERE cliente_id = ? AND tipo = 'dados' AND estado = 'PENDENTE'", (cid,))
    for p in pend:
        comando_estado(p["id"], "EM CURSO")
        comando_linha(p["id"], f"entregue ao agente{(' ' + como) if como else ''}: a recolher os dados")


def _dados_recebidos(cid: int, extras: dict) -> None:
    with store.tx() as t:
        ativos = t.all("SELECT id, args FROM cliente_comandos WHERE cliente_id = ? AND tipo = 'dados' AND estado IN ('PENDENTE', 'EM CURSO')", (cid,))
    a_verificar = extras.get("_a_verificar") or []
    for r in ativos:
        try:
            a = json.loads(r["args"] or "{}")
        except ValueError:
            a = {}
        faltam = list(a.get("faltam") or [])
        mudou = False
        for tipo in list(faltam):
            chave = "ficheiros" if tipo == "ficheiros" else tipo
            if chave in extras:
                comando_linha(r["id"], f"recebido: {NOMES_PEDIDO.get(tipo, tipo)} — {_resumo_dados(chave, extras[chave])}")
                if tipo == "logs" and "ficheiros" in extras:
                    comando_linha(r["id"], f"recebido: ficheiros de log — {_resumo_dados('ficheiros', extras['ficheiros'])}")
                faltam.remove(tipo)
                mudou = True
            elif tipo in a_verificar and not a.get("aviso_" + tipo):
                comando_linha(r["id"], f"{NOMES_PEDIDO.get(tipo, tipo)}: a verificar no servidor (dnf/yum/apt podem demorar alguns minutos)")
                a["aviso_" + tipo] = 1
                mudou = True
        if mudou:
            a["faltam"] = faltam
            with store.tx() as t:
                t.run("UPDATE cliente_comandos SET args = ? WHERE id = ?", (json.dumps(a)[:500], r["id"]))
            if not faltam:
                comando_linha(r["id"], "concluído")
                comando_estado(r["id"], "OK")


# ============================================================ intervalos de recolha (Gerir clientes -> Recolha de dados)
RECOLHA = {"processos": ("Processos", 0, "em cada sincronização"), "sistema": ("Sistema", 3600, ""), "logs": ("Logs (journal)", 300, ""),
           "ficheiros": ("Ficheiros de log", 300, ""), "atualizacoes": ("Atualizações pendentes", 21600, "dnf/yum consultam os repositórios")}


def intervalos_recolha() -> dict:
    out = {}
    for k, (_n, padrao, _h) in RECOLHA.items():
        try:
            out[k] = int(store.get_setting(f"clientes_recolha_{k}") or padrao)
        except ValueError:
            out[k] = padrao
    return out


# ============================================================ ficheiros de log seguidos (por cliente)
LOG_PASTA_RE = re.compile(r"^/[\w./ -]{0,490}$")
LOG_FICH_RE = re.compile(r"^[\w.*?\[\] -]{1,200}$")


def logs_cfg(cid: int) -> list[dict]:
    with store.tx() as t:
        return t.all("SELECT * FROM cliente_logs WHERE cliente_id = ? ORDER BY tag, descricao, ficheiro", (cid,))


def editar_log(cid: int, lid: int, pasta: str, ficheiro: str, descricao: str = "", tag: str = "", linhas: int = 200) -> None:
    pasta, ficheiro = _validar_log(pasta, ficheiro)
    with store.tx() as t:
        t.run("UPDATE cliente_logs SET pasta = ?, ficheiro = ?, descricao = ?, tag = ?, linhas = ? WHERE id = ? AND cliente_id = ?",
              (pasta, ficheiro, (descricao or "")[:255], (tag or "")[:40], max(20, min(2000, int(linhas or 200))), lid, cid))


def _validar_log(pasta: str, ficheiro: str) -> tuple[str, str]:
    pasta, ficheiro = (pasta or "").strip().rstrip("/") or "/", (ficheiro or "").strip()
    if not LOG_PASTA_RE.match(pasta) or ".." in pasta:
        raise ValueError("Pasta inválida (caminho absoluto, sem '..').")
    if ficheiro and (not LOG_FICH_RE.match(ficheiro) or "/" in ficheiro):
        raise ValueError("Nome de ficheiro inválido (pode usar * e ?). Deixe vazio para seguir a pasta.")
    return pasta, ficheiro


def pode_ler(cid: int, caminho: str, gerir: bool) -> bool:
    """Quem gere clientes le qualquer ficheiro (dentro de LOGS_PASTAS, verificado pelo agente); os restantes
    so os ficheiros configurados nos logs deste cliente (ou dentro de uma pasta configurada)."""
    import fnmatch
    if gerir:
        return True
    caminho = os.path.normpath(caminho or "")
    for l in logs_cfg(cid):
        if not l["ficheiro"] and (caminho == l["pasta"] or caminho.startswith(l["pasta"].rstrip("/") + "/")):
            return True
        if l["ficheiro"] and os.path.dirname(caminho) == l["pasta"] and fnmatch.fnmatch(os.path.basename(caminho), l["ficheiro"]):
            return True
    return False


def pedir_leitura(cid: int, caminho: str, linhas: int = 5000, listar: bool = False, por: str = "") -> int:
    c = obter(cid) or {}
    cmd = criar_comando(cid, "ler", json.dumps({"caminho": caminho, "linhas": int(linhas or 0), "listar": bool(listar)})[:500], por)
    comando_linha(cmd, f"pedido por {por or 'portal'}: {'listar a pasta' if listar else 'ler'} {caminho}"
                       + ("" if listar else (f" (últimas {linhas} linhas)" if linhas else " (completo, até 10 MB)")))
    if _v(c.get("versao") or "0") < (1, 6, 0):
        comando_linha(cmd, f"o agente {c.get('versao') or '(sem versão)'} não sabe abrir ficheiros (a partir da 1.6.0): atualize-o na aba Agente")
        comando_estado(cmd, "ERRO")
    return cmd


def guardar_leitura(cid: int, cmd_id: int, dados: dict) -> None:
    tipo = f"ler:{cmd_id}"
    with store.tx() as t:
        t.run("INSERT INTO cliente_dados (cliente_id, tipo, atualizado, dados) VALUES (?, ?, ?, ?)",
              (cid, tipo, store.now(), json.dumps(dados)))
        velhos = t.all("SELECT id FROM cliente_dados WHERE cliente_id = ? AND tipo LIKE 'ler:%' ORDER BY id DESC", (cid,))[20:]
        for v in velhos:
            t.run("DELETE FROM cliente_dados WHERE id = ?", (v["id"],))


def leitura(cid: int, cmd_id: int) -> dict | None:
    import base64
    import gzip
    with store.tx() as t:
        r = t.one("SELECT dados, atualizado FROM cliente_dados WHERE cliente_id = ? AND tipo = ?", (cid, f"ler:{cmd_id}"))
    if not r:
        return None
    d = json.loads(r["dados"] or "{}")
    if d.get("gz"):
        d["texto"] = gzip.decompress(base64.b64decode(d.pop("gz"))).decode("utf-8", errors="replace")
    d["recebido"] = r["atualizado"]
    return d


def guardar_log(cid: int, pasta: str, ficheiro: str, descricao: str = "", tag: str = "", linhas: int = 200) -> None:
    pasta, ficheiro = _validar_log(pasta, ficheiro)
    linhas = max(20, min(2000, int(linhas or 200)))
    with store.tx() as t:
        if t.val("SELECT COUNT(*) FROM cliente_logs WHERE cliente_id = ? AND pasta = ? AND ficheiro = ?", (cid, pasta, ficheiro)):
            t.run("UPDATE cliente_logs SET descricao = ?, tag = ?, linhas = ? WHERE cliente_id = ? AND pasta = ? AND ficheiro = ?",
                  ((descricao or "")[:255], (tag or "")[:40], linhas, cid, pasta, ficheiro))
        else:
            t.run("INSERT INTO cliente_logs (cliente_id, pasta, ficheiro, descricao, tag, linhas, criado) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (cid, pasta, ficheiro, (descricao or "")[:255], (tag or "")[:40], linhas, store.now()))


def apagar_log(cid: int, lid: int) -> None:
    with store.tx() as t:
        t.run("DELETE FROM cliente_logs WHERE id = ? AND cliente_id = ?", (lid, cid))


# ============================================================ atualizacao do agente pelo portal
def agente_portal() -> tuple[str, str]:
    """(versao, sha256) do agente-portal.py que o portal distribui."""
    p = ficheiro_agente("agente-portal.py")
    if not p:
        return "", ""
    dados = open(p, "rb").read()
    m = re.search(rb'^VERSAO = "([^"]+)"', dados, re.M)
    return (m.group(1).decode() if m else ""), hashlib.sha256(dados).hexdigest()


def _v(x: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", x or "0")[:3])


def desatualizado(c: dict) -> bool:
    v, _ = agente_portal()
    return bool(v and c.get("versao") and _v(c["versao"]) < _v(v))


def pedir_atualizacao(cid: int, por: str = "") -> int:
    """Cria (ou devolve, se ja houver) o comando 'atualizar' deste cliente. Agentes 1.5+ recebem-no em ~5 s
    (consulta de comandos); os 1.4 na sincronizacao seguinte."""
    with store.tx() as t:
        r = t.one("SELECT id FROM cliente_comandos WHERE cliente_id = ? AND tipo = 'atualizar' "
                  "AND estado IN ('PENDENTE', 'EM CURSO') ORDER BY id DESC", (cid,))
    if r:
        return r["id"]
    v, sha = agente_portal()
    c = obter(cid) or {}
    cmd = criar_comando(cid, "atualizar", json.dumps({"versao": v, "sha256": sha}), por)
    comando_linha(cmd, f"pedido por {por or 'portal'}: atualizar o agente {c.get('versao') or '?'} -> {v}")
    if _v(c.get("versao") or "0") < (1, 4, 0):
        comando_linha(cmd, f"o agente {c.get('versao') or '(sem versão)'} não sabe atualizar-se pelo portal (só a partir da 1.4.0).")
        comando_linha(cmd, "Atualize-o uma vez no servidor: Clientes → Ferramentas → Instalar e atualizar → "
                           "\"Atualizar o agente\" (curl ... setup.sh && sudo bash setup.sh --portal ...).")
        comando_estado(cmd, "ERRO")
        return cmd
    if _v(c.get("versao") or "0") < (1, 5, 0):
        comando_linha(cmd, f"o agente {c.get('versao') or '?'} recebe o pedido na próxima sincronização "
                           f"(até {c.get('intervalo') or 60} s); a partir da 1.5.0 é imediato")
    else:
        comando_linha(cmd, "à espera que o agente receba o pedido (consulta a cada 5 s)")
    store.save_settings({f"cliente_atualizar:{cid}": store.now()})
    return cmd


# ============================================================ comandos para o agente (com output)
def criar_comando(cid: int, tipo: str, args: str = "", por: str = "") -> int:
    with store.tx() as t:
        t.run("INSERT INTO cliente_comandos (cliente_id, tipo, args, estado, pedido_por, criado, output) "
              "VALUES (?, ?, ?, 'PENDENTE', ?, ?, '')", (cid, tipo, args[:4000], (por or "")[:100], store.now()))
        return t.val("SELECT MAX(id) FROM cliente_comandos WHERE cliente_id = ?", (cid,))


def comando_linha(cmd_id: int, texto: str) -> None:
    linha = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {texto}"
    with store.tx() as t:
        r = t.one("SELECT output FROM cliente_comandos WHERE id = ?", (cmd_id,))
        if r is not None:
            t.run("UPDATE cliente_comandos SET output = ? WHERE id = ?", (((r["output"] or "") + linha + "\n")[-200_000:], cmd_id))


def comando_estado(cmd_id: int, estado: str) -> None:
    with store.tx() as t:
        if estado == "EM CURSO":
            t.run("UPDATE cliente_comandos SET estado = ?, iniciado = COALESCE(iniciado, ?) WHERE id = ?", (estado, store.now(), cmd_id))
        else:
            t.run("UPDATE cliente_comandos SET estado = ?, terminado = ? WHERE id = ?", (estado, store.now(), cmd_id))
    if estado in ("OK", "ERRO"):
        with store.tx() as t:
            r = t.one("SELECT cliente_id, tipo FROM cliente_comandos WHERE id = ?", (cmd_id,))
        if r and r["tipo"] == "atualizar":
            store.save_settings({f"cliente_atualizar:{r['cliente_id']}": ""})
        if r and r["tipo"] == "upgrade":
            try:
                from flask import url_for
                link = url_for("clientes.execucao", cmd_id=cmd_id)
            except Exception:  # noqa: BLE001 - fora de um pedido
                link = ""
            c = obter(r["cliente_id"]) or {}
            rr = comando(cmd_id) or {}
            er = erros_upgrade(rr.get("output") or "")
            eventos.emitir("clientes.upgrade", "sucesso" if estado == "OK" else "erro",
                           f"{c.get('nome', '?')}: upgrade do sistema {'concluído' if estado == 'OK' else 'falhou'} (#{cmd_id})",
                           descrever(rr) + ("\n" + "\n".join(er[-10:]) if estado != "OK" and er else ""), link,
                           dados={"cliente": c.get("nome", "")})


EXPIRA_MIN = 15


def expirar_comandos(cid: int | None = None) -> None:
    """Comandos sem resposta do agente ha mais de 15 min passam a ERRO (e limpa pedidos antigos sem comando)."""
    limite = (datetime.datetime.now() - datetime.timedelta(minutes=EXPIRA_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    with store.tx() as t:
        velhos = t.all("SELECT c.id, c.cliente_id, c.estado, k.versao FROM cliente_comandos c LEFT JOIN clientes k ON k.id = c.cliente_id "
                       "WHERE c.estado IN ('PENDENTE', 'EM CURSO') AND c.criado < ? AND NOT (c.tipo = 'upgrade' AND c.estado = 'EM CURSO')"
                       + (" AND c.cliente_id = ?" if cid else ""), (limite, cid) if cid else (limite,))
        lim_up = (datetime.datetime.now() - datetime.timedelta(hours=UPGRADE_MAX_H)).strftime("%Y-%m-%d %H:%M:%S")
        longos = t.all("SELECT id FROM cliente_comandos WHERE tipo = 'upgrade' AND estado = 'EM CURSO' AND iniciado < ?"
                       + (" AND cliente_id = ?" if cid else ""), (lim_up, cid) if cid else (lim_up,))
    for r in longos:
        comando_linha(r["id"], f"sem fim ao fim de {UPGRADE_MAX_H} h: dado como falhado (veja no servidor se o dnf/apt ainda corre)")
        comando_estado(r["id"], "ERRO")
    for r in velhos:
        comando_linha(r["id"], f"sem resposta do agente ({r['versao'] or 'sem versão'}) em {EXPIRA_MIN} min: "
                               "agente parado, sem contacto com o portal ou versão que não conhece este comando")
        comando_estado(r["id"], "ERRO")
    # pedidos da versao anterior do portal (so um 'setting', sem comando): limpa os que nao tem comando em curso
    for c in listar() if cid is None else [obter(cid) or {}]:
        if not c or not store.get_setting(f"cliente_atualizar:{c['id']}"):
            continue
        with store.tx() as t:
            ativo = t.val("SELECT COUNT(*) FROM cliente_comandos WHERE cliente_id = ? AND tipo = 'atualizar' "
                          "AND estado IN ('PENDENTE', 'EM CURSO')", (c["id"],))
        if not ativo:
            store.save_settings({f"cliente_atualizar:{c['id']}": ""})


def cancelar_comando(cmd_id: int, por: str = "") -> None:
    r = comando(cmd_id)
    if r and r["estado"] in ("PENDENTE", "EM CURSO"):
        comando_linha(cmd_id, f"cancelado por {por or 'portal'}" + (" (o agente já o tinha recebido: pode ainda concluir)"
                                                                     if r["estado"] == "EM CURSO" else ""))
        comando_estado(cmd_id, "ERRO")


def comando_ativo(cid: int) -> dict | None:
    with store.tx() as t:
        return t.one("SELECT * FROM cliente_comandos WHERE cliente_id = ? AND estado IN ('PENDENTE', 'EM CURSO') ORDER BY id DESC", (cid,))


TIPOS_COMANDO = {"atualizar": "Atualizar o agente", "dados": "Pedir dados", "ler": "Abrir ficheiro / pasta", "firewall": "Firewall",
                 "upgrade": "Upgrade do sistema (pacotes)", "reiniciar": "Reiniciar o agente"}


def pedir_reinicio(cid: int, por: str = "") -> int:
    """Reinicia o agente do cliente (1.8.2+): o processo volta a arrancar e o novo fecha o pedido."""
    c = obter(cid) or {}
    with store.tx() as t:
        r = t.one("SELECT id FROM cliente_comandos WHERE cliente_id = ? AND tipo IN ('reiniciar', 'atualizar') "
                  "AND estado IN ('PENDENTE', 'EM CURSO') ORDER BY id DESC", (cid,))
    if r:
        return r["id"]
    cmd = criar_comando(cid, "reiniciar", "{}", por)
    comando_linha(cmd, f"pedido por {por or 'portal'}: reiniciar o agente {c.get('versao') or ''}")
    if _v(c.get("versao") or "0") < (1, 8, 2):
        comando_linha(cmd, f"o agente {c.get('versao') or '(sem versão)'} não reinicia a pedido (a partir da 1.8.2): "
                           "atualize-o (a atualização também o reinicia) ou, no servidor, systemctl restart agente-portal")
        comando_estado(cmd, "ERRO")
    return cmd
UPGRADE_MAX_H = 6          # upgrade em curso sem terminar ha mais de 6 h passa a ERRO
PKG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._:~@-]{0,200}$")
ERRO_PKG_RE = re.compile(r"(Curl error|\bE: |\bW: |Error|ERROR|Erro|[Ff]ailed|FAILED|dpkg: error|Errors were encountered|No package|"
                         r"Problem: |conflicts with|nothing provides|Could not get lock|Unable to|codigo de saida [1-9])")


def pedir_upgrade(cid: int, modo: str, pacotes: list | None = None, por: str = "", retomar_de: int | None = None) -> int:
    """Upgrade de pacotes no cliente pelo agente (1.8+): todos, so seguranca ou os pacotes escolhidos."""
    if modo not in ("todos", "seguranca", "pacotes"):
        raise ValueError("modo de upgrade inválido")
    pac = sorted({p for p in (pacotes or []) if PKG_RE.match(p or "")})
    c = obter(cid) or {}
    if modo == "seguranca" and not pac:                 # apt nao tem --security: manda a lista de seguranca conhecida
        d = (dados(cid).get("atualizacoes") or {}).get("dados") or {}
        pac = sorted({p["nome"] for p in d.get("pacotes") or [] if p.get("seguranca")})
    if modo == "pacotes" and not pac:
        raise ValueError("escolha pelo menos um pacote")
    with store.tx() as t:
        ativo = t.val("SELECT id FROM cliente_comandos WHERE cliente_id = ? AND tipo = 'upgrade' AND estado IN ('PENDENTE', 'EM CURSO')", (cid,))
    if ativo:
        raise ValueError(f"já há um upgrade a decorrer neste cliente (#{ativo})")
    args = {"modo": modo, "pacotes": pac}
    if retomar_de:
        args.update(retomar=True, retoma=retomar_de)
    txt = json.dumps(args)
    if len(txt) > 4000:                                   # muitos pacotes: atualiza tudo o que esta pendente
        args = {"modo": "todos", "pacotes": [], **({"retomar": True, "retoma": retomar_de} if retomar_de else {})}
        txt = json.dumps(args)
    cmd = criar_comando(cid, "upgrade", txt, por)
    comando_linha(cmd, f"pedido por {por or 'portal'}: " + descrever({"tipo": "upgrade", "args": txt})
                  + (f" (retoma a execução #{retomar_de})" if retomar_de else ""))
    if _v(c.get("versao") or "0") < (1, 8, 0):
        comando_linha(cmd, f"o agente {c.get('versao') or '(sem versão)'} não faz upgrades do sistema (a partir da 1.8.0): atualize-o na aba Agente")
        comando_estado(cmd, "ERRO")
    return cmd


def erros_upgrade(output: str) -> list[str]:
    """Linhas de erro do output de um upgrade (para o resumo da execucao)."""
    out = []
    for l in (output or "").splitlines():
        t = re.sub(r"^\[\d\d:\d\d:\d\d\] ", "", l)
        if ERRO_PKG_RE.search(t) and not t.startswith("$ ") and "Errors:" not in t[:3]:
            out.append(t.strip())
    return out[-60:]

# ============================================================ firewall do cliente (firewalld ou ufw) pelo agente 1.7+
FW_OPS = {"porta_add": "abrir a porta {porta} na zona {zona}", "porta_del": "fechar a porta {porta} na zona {zona}",
          "servico_add": "permitir o serviço {servico} na zona {zona}", "servico_del": "retirar o serviço {servico} da zona {zona}",
          "rich_add": "regra na zona {zona}: {regra}", "rich_del": "remover da zona {zona}: {regra}",
          "fonte_add": "origem {fonte} na zona {zona}", "fonte_del": "retirar a origem {fonte} da zona {zona}",
          "ipset_novo": "criar o ipset {nome} ({tipo})", "ipset_apagar": "apagar o ipset {nome}",
          "entrada_add": "adicionar {entrada} ao ipset {nome}", "entrada_del": "retirar {entrada} do ipset {nome}",
          "ufw_regra": "ufw {acao} porta {porta}/{proto} de {de}", "ufw_apagar": "ufw apagar a regra n.º {numero}",
          "reiniciar": "reiniciar o serviço da firewall"}
FW_CAMPOS = ("op", "zona", "porta", "servico", "regra", "fonte", "nome", "tipo", "entrada", "acao", "proto", "de", "numero", "comentario")


def fw_descrever(op: dict) -> str:
    f = FW_OPS.get(op.get("op"), op.get("op") or "?")
    try:
        return f.format(**{k: op.get(k) or ("qualquer" if k == "de" else "") for k in FW_CAMPOS})
    except (KeyError, IndexError):
        return str(op.get("op"))


def pedir_firewall(cid: int, ops: list | None = None, por: str = "") -> int:
    """Le o estado da firewall do cliente (ops vazio) ou aplica operacoes (validadas tambem no agente)."""
    ops = [{k: str(o[k])[:600] for k in FW_CAMPOS if o.get(k) not in (None, "")} for o in (ops or [])]
    for o in ops:
        if o.get("op") not in FW_OPS:
            raise ValueError("operação de firewall desconhecida")
    args = json.dumps({"ops": ops})
    if len(args) > 4000:
        raise ValueError("demasiadas operações num pedido: divida em vários")
    c = obter(cid) or {}
    cmd = criar_comando(cid, "firewall", args, por)
    comando_linha(cmd, f"pedido por {por or 'portal'}: " + ("; ".join(fw_descrever(o) for o in ops) if ops else "ler o estado da firewall"))
    if any(o.get("op") == "reiniciar" for o in ops) and _v(c.get("versao") or "0") < (1, 7, 4):
        comando_linha(cmd, f"o agente {c.get('versao') or '?'} não reinicia a firewall a pedido (a partir da 1.7.4): atualize-o na aba Agente")
        comando_estado(cmd, "ERRO")
        return cmd
    if _v(c.get("versao") or "0") < (1, 7, 0):
        comando_linha(cmd, f"o agente {c.get('versao') or '(sem versão)'} não gere a firewall (a partir da 1.7.0): atualize-o na aba Agente")
        comando_estado(cmd, "ERRO")
    return cmd


def _portas_acesso(c: dict) -> list[dict]:
    """Acessos de que o portal precisa neste cliente: SSH, TNS (porta de cada BD deste servidor) e servico Oracle do agente."""
    from . import store
    out = [{"acesso": "SSH", "porta": int(c.get("ssh_porta") or 22), "servico": "ssh"}]
    tns = set()
    for d in store.list_dbs():
        if d.get("cliente_id") == c["id"] or (d.get("host") or "").lower() == (c.get("endereco") or "").lower():
            tns.add(int(d.get("port") or 1521))
    for p in sorted(tns or {1521}):
        out.append({"acesso": "TNS (listener Oracle)", "porta": p, "servico": "oracledb" if p == 1521 else ""})
    rpc = (c.get("info") or {}).get("rpc")
    if rpc:
        out.append({"acesso": "Serviço Oracle do agente", "porta": int(rpc.get("porta") or 8795), "servico": ""})
    return out


def _cobre(ip: str, redes) -> bool:
    import ipaddress
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for r in redes:
        try:
            if a in ipaddress.ip_network(str(r).strip(), strict=False):
                return True
        except ValueError:
            continue
    return False


def _regra_porta(regra: str, porta: int, servico: str) -> bool:
    m = re.search(r'port port="?(\d+)(?:-(\d+))?"?', regra)
    if m:
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if a <= porta <= b:
            return True
    return bool(servico) and f'service name="{servico}"' in regra.replace("'", '"')


def fw_acessos(c: dict) -> dict:
    """Para cada acesso do portal (SSH, TNS, servico Oracle do agente): por onde a firewall do cliente o deixa
    passar (ipsets em regras ricas ou origens de zona, porta/servico aberto a todos, regras ufw) e se o IP do portal
    (o de origem para este cliente) la esta. Usa o ultimo estado lido na aba Firewall."""
    fw = ((dados(c["id"]).get("firewall") or {}).get("dados")) or {}
    ip = ip_origem(c["endereco"], int(c.get("ssh_porta") or 22))
    linhas = []
    ipsets = {s["nome"]: s for s in fw.get("ipsets") or []}
    for a in _portas_acesso(c):
        porta, svc = a["porta"], a["servico"]
        via, falta, aberto = [], [], False
        if fw.get("backend") == "firewalld":
            for z in fw.get("zonas") or []:
                for r in z.get("rich") or []:
                    if "accept" not in r or not _regra_porta(r, porta, svc):
                        continue
                    m = re.search(r'source (?:NOT )?ipset="?([^"\s]+)"?', r)
                    if m:
                        n = m.group(1)
                        tem = _cobre(ip, (ipsets.get(n) or {}).get("entradas") or [])
                        via.append({"tipo": "ipset", "nome": n, "zona": z["nome"], "tem": tem})
                        continue
                    m = re.search(r'source address="?([^"\s]+)"?', r)
                    if m:
                        via.append({"tipo": "endereco", "nome": m.group(1), "zona": z["nome"], "tem": _cobre(ip, [m.group(1)])})
                    else:
                        aberto = True
                portas_z = z.get("ports") or []
                abre = f"{porta}/tcp" in portas_z or (svc and svc in (z.get("services") or [])) or any(
                    "-" in x and x.endswith("/tcp") and int(x.split("-")[0]) <= porta <= int(x.split("-")[1].split("/")[0]) for x in portas_z)
                if abre:
                    fontes = z.get("sources") or []
                    if fontes:                                   # zona so para estas origens
                        for f in fontes:
                            if f.startswith("ipset:"):
                                n = f[6:]
                                via.append({"tipo": "ipset", "nome": n, "zona": z["nome"],
                                            "tem": _cobre(ip, (ipsets.get(n) or {}).get("entradas") or [])})
                            else:
                                via.append({"tipo": "endereco", "nome": f, "zona": z["nome"], "tem": _cobre(ip, [f])})
                    else:
                        aberto = True
        elif fw.get("backend") == "ufw":
            for r in fw.get("regras") or []:
                if r.get("acao") != "ALLOW" or r.get("dir", "IN") != "IN":
                    continue
                para = r.get("para", "")
                m = re.match(r"^(\d+)(?::(\d+))?", para)
                if not ((m and int(m.group(1)) <= porta <= int(m.group(2) or m.group(1)))
                        or (svc == "ssh" and para.startswith("OpenSSH"))):
                    continue
                de = r.get("de", "")
                if de.startswith("Anywhere"):
                    aberto = True
                else:
                    via.append({"tipo": "ufw", "nome": de, "zona": f"regra {r.get('n')}", "tem": _cobre(ip, [de])})
        ok = aberto or any(v["tem"] for v in via)
        falta = sorted({v["nome"] for v in via if v["tipo"] == "ipset" and not v["tem"]})
        linhas.append({**a, "via": via, "aberto": aberto, "ok": ok, "falta": falta})
    erro = "" if ip else f"não foi possível saber o IP de origem do portal para {c['endereco']} (o nome resolve no portal?)"
    return {"ip": ip, "backend": fw.get("backend") or "", "lido": fw.get("lido"), "linhas": linhas if ip else [], "erro": erro}


def fw_ops_acessos(c: dict, ipsets: list | None = None) -> list:
    """Operacoes para pôr o IP do portal nos ipsets que dao acesso (firewalld) ou regras ufw para as portas em falta."""
    r = fw_acessos(c)
    ip, ops = r["ip"], []
    if not ip:
        return []
    if r["backend"] == "firewalld":
        nomes = set(ipsets) if ipsets else {n for l in r["linhas"] for n in l["falta"]}
        for n in sorted(nomes):
            ops.append({"op": "entrada_add", "nome": n, "entrada": ip})
    elif r["backend"] == "ufw":
        for l in r["linhas"]:
            if not l["ok"]:
                ops.append({"op": "ufw_regra", "acao": "allow", "porta": str(l["porta"]), "proto": "tcp", "de": ip,
                            "comentario": "portal " + l["acesso"].split()[0].lower()})
    return ops


def fw_ops_ipset_editar(c: dict, nome: str, texto: str) -> list:
    """Editar as entradas de um ipset de uma vez (texto, uma por linha ou separadas por espacos): junta as novas e
    retira as que sairam, em relacao ao ultimo estado lido."""
    fw = ((dados(c["id"]).get("firewall") or {}).get("dados")) or {}
    s = next((x for x in fw.get("ipsets") or [] if x["nome"] == nome), None)
    if not s:
        raise ValueError(f"ipset {nome} não encontrado (leia o estado da firewall)")
    if (s.get("total") or 0) > len(s.get("entradas") or []):
        raise ValueError(f"o ipset {nome} tem {s['total']} entradas e só foram lidas {len(s['entradas'])}: edite uma a uma")
    novas = [x for x in re.split(r"[\s,;]+", texto or "") if x]
    antes = list(s.get("entradas") or [])
    ops = [{"op": "entrada_add", "nome": nome, "entrada": x} for x in novas if x not in antes]
    ops += [{"op": "entrada_del", "nome": nome, "entrada": x} for x in antes if x not in novas]
    return ops


def servicos_monit(cid: int) -> list[str]:
    from . import store
    try:
        v = json.loads(store.get_setting(f"clientes.servicos.{cid}") or "[]")
    except ValueError:
        v = []
    return [str(x) for x in v if isinstance(x, str)][:60]


def guardar_servicos(cid: int, nomes) -> list[str]:
    from . import store
    lim = []
    for n in nomes:
        n = (n or "").strip()
        if n and n not in lim:
            if not re.match(r"^[A-Za-z0-9@_.:-]{1,120}$", n):
                raise ValueError(f"nome de serviço inválido: {n}")
            lim.append(n)
    store.save_settings({f"clientes.servicos.{cid}": json.dumps(lim[:60])})
    return lim


def fw_preset_oracle(estado: dict, ipset: str, entradas: list, porta: int) -> list:
    """Operacoes para abrir a porta RPC do agente (Oracle) a um ipset em todas as zonas ativas (firewalld) ou por IP (ufw)."""
    ops = []
    if estado.get("backend") == "firewalld":
        existe = next((i for i in estado.get("ipsets") or [] if i["nome"] == ipset), None)
        if not existe:
            ops.append({"op": "ipset_novo", "nome": ipset, "tipo": "hash:net"})
        for e in entradas:
            if not existe or e not in existe.get("entradas", []):
                ops.append({"op": "entrada_add", "nome": ipset, "entrada": e})
        regra = f'rule family="ipv4" source ipset="{ipset}" port port="{porta}" protocol="tcp" accept'
        for z in (estado.get("ativas") or {}) or [estado.get("zona_omissao") or "public"]:
            zd = next((x for x in estado.get("zonas") or [] if x["nome"] == z), {})
            if not any(f'ipset="{ipset}"' in r and f'port="{porta}"' in r for r in zd.get("rich", [])):
                ops.append({"op": "rich_add", "zona": z, "regra": regra})
    elif estado.get("backend") == "ufw":
        for e in entradas or [""]:
            ops.append({"op": "ufw_regra", "acao": "allow", "porta": str(porta), "proto": "tcp", "de": e, "comentario": "agente-portal"})
    return ops


def descrever(r: dict) -> str:
    try:
        a = json.loads(r.get("args") or "{}")
    except ValueError:
        a = {}
    if r["tipo"] == "dados":
        return "Pedir dados: " + ", ".join(NOMES_PEDIDO.get(x, x) for x in a.get("tipos") or [])
    if r["tipo"] == "atualizar":
        return f"Atualizar o agente para {a.get('versao') or '?'}"
    if r["tipo"] == "firewall":
        ops = a.get("ops") or []
        return "Firewall: " + ("; ".join(fw_descrever(o) for o in ops) if ops else "ler o estado")
    if r["tipo"] == "reiniciar":
        return "Reiniciar o agente"
    if r["tipo"] == "upgrade":
        n = len(a.get("pacotes") or [])
        alvo = {"todos": "todos os pacotes", "seguranca": f"só segurança ({n})" if n else "só segurança",
                "pacotes": f"{n} pacote(s)" + (": " + ", ".join((a.get("pacotes") or [])[:4]) + ("…" if n > 4 else "") if n else "")}.get(a.get("modo"), "?")
        return ("Retomar upgrade: " if a.get("retomar") else "Upgrade: ") + alvo
    if r["tipo"] == "ler":
        return ("Listar a pasta " if a.get("listar") else "Abrir ") + str(a.get("caminho") or "")
    return r["tipo"]


def repetir_comando(cmd_id: int, por: str = "") -> int:
    """Volta a fazer um pedido que falhou (ou foi interrompido/cancelado) com os mesmos argumentos. Devolve o novo id."""
    r = comando(cmd_id)
    if not r:
        raise ValueError("pedido não encontrado")
    if r["estado"] in ("PENDENTE", "EM CURSO"):
        raise ValueError("o pedido ainda está a decorrer")
    try:
        a = json.loads(r.get("args") or "{}")
    except ValueError:
        a = {}
    cid = r["cliente_id"]
    if r["tipo"] == "dados":
        novo = pedir_dados(cid, a.get("tipos") or [], por, f"repete o pedido #{cmd_id}")
    elif r["tipo"] == "atualizar":
        novo = pedir_atualizacao(cid, por)
    elif r["tipo"] == "ler":
        novo = pedir_leitura(cid, a.get("caminho") or "", a.get("linhas") or 0, bool(a.get("listar")), por)
    elif r["tipo"] == "firewall":
        novo = pedir_firewall(cid, a.get("ops") or [], por)
    elif r["tipo"] == "reiniciar":
        novo = pedir_reinicio(cid, por)
    elif r["tipo"] == "upgrade":
        return pedir_upgrade(cid, a.get("modo") or "todos", a.get("pacotes") or [], por, retomar_de=cmd_id)
    else:
        raise ValueError(f"não é possível repetir pedidos do tipo {r['tipo']}")
    if r["tipo"] != "dados":
        comando_linha(novo, f"repete o pedido #{cmd_id}")
    return novo


def todos_comandos(cliente: int | None = None, estado: str = "", tipo: str = "", limite: int = 300) -> list[dict]:
    sql = ("SELECT c.id, c.cliente_id, c.tipo, c.args, c.estado, c.pedido_por, c.criado, c.iniciado, c.terminado, k.nome AS cliente "
           "FROM cliente_comandos c LEFT JOIN clientes k ON k.id = c.cliente_id WHERE 1 = 1")
    par = []
    if cliente:
        sql += " AND c.cliente_id = ?"
        par.append(cliente)
    if estado:
        sql += " AND c.estado = ?"
        par.append(estado)
    if tipo:
        sql += " AND c.tipo = ?"
        par.append(tipo)
    sql += " ORDER BY c.id DESC LIMIT ?"
    par.append(limite)
    with store.tx() as t:
        rs = t.all(sql, tuple(par))
    for r in rs:
        r["descricao"] = descrever(r)
        a, b = _dtp(r["criado"]), _dtp(r["terminado"]) if r.get("terminado") else None
        r["duracao"] = int((b - a).total_seconds()) if a and b else None
    return rs


def _dtp(x):
    try:
        return datetime.datetime.strptime(str(x)[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def comando(cmd_id: int) -> dict | None:
    with store.tx() as t:
        return t.one("SELECT * FROM cliente_comandos WHERE id = ?", (cmd_id,))


def comandos(cid: int, limite: int = 20) -> list[dict]:
    with store.tx() as t:
        return t.all("SELECT * FROM cliente_comandos WHERE cliente_id = ? ORDER BY id DESC LIMIT ?", (cid, limite))


def entregar_comandos(c: dict) -> dict:
    """Consulta do agente (1.5+, a cada ~5 s): comandos pendentes e pedidos de dados."""
    with store.tx() as t:      # os pedidos de dados vao pela lista 'pedidos' (o agente faz uma sincronizacao com eles)
        pend = t.all("SELECT id, tipo, args FROM cliente_comandos WHERE cliente_id = ? AND estado = 'PENDENTE' "
                     "AND tipo <> 'dados' ORDER BY id", (c["id"],))
    out = []
    for p in pend:
        comando_estado(p["id"], "EM CURSO")
        comando_linha(p["id"], "entregue ao agente")
        try:
            args = json.loads(p["args"] or "{}")
        except ValueError:
            args = {}
        out.append({"id": p["id"], "tipo": p["tipo"], "args": args})
    ped = store.get_setting(f"cliente_pedido:{c['id']}") or ""
    if ped:
        store.save_settings({f"cliente_pedido:{c['id']}": ""})
        _dados_entregues(c["id"], "")
    return {"comandos": out, "pedidos": [x for x in ped.split(",") if x]}


def _fechar_atualizacao(c: dict, info: dict) -> None:
    """Agentes sem consulta de comandos (1.4): fecha o comando pela versao/erro que o agente reporta."""
    with store.tx() as t:
        r = t.one("SELECT * FROM cliente_comandos WHERE cliente_id = ? AND tipo = 'atualizar' AND estado IN ('PENDENTE', 'EM CURSO') "
                  "ORDER BY id DESC", (c["id"],))
    if not r:
        return
    try:
        alvo = json.loads(r["args"] or "{}").get("versao") or ""
    except ValueError:
        alvo = ""
    ult = info.get("atualizacao") if isinstance(info.get("atualizacao"), dict) else {}
    if _v(info.get("versao") or "0") >= (1, 5, 0):
        # 1.5+: o proprio agente recebe e fecha o comando; a sincronizacao so serve de recurso (2 min depois)
        if r["estado"] == "PENDENTE" or not r["iniciado"]:
            return
        try:
            if (datetime.datetime.now() - datetime.datetime.strptime(r["iniciado"], "%Y-%m-%d %H:%M:%S")).total_seconds() < 120:
                return
        except ValueError:
            return
    if alvo and _v(info.get("versao") or "0") >= _v(alvo):
        comando_linha(r["id"], f"o agente sincronizou com a versão {info.get('versao')}")
        comando_estado(r["id"], "OK")
    elif ult.get("erro") and ult.get("para") == alvo and (ult.get("quando") or "").replace("T", " ") >= r["criado"]:
        comando_linha(r["id"], f"o agente reportou erro: {ult['erro']}")
        comando_estado(r["id"], "ERRO")


def sync(c: dict, info: dict, ip: str, link: str = "", extras: dict | None = None) -> dict:
    iv = info.get("intervalo")
    try:
        iv = max(10, min(3600, int(iv)))
    except (TypeError, ValueError):
        iv = c.get("intervalo") or 60
    texto = json.dumps(info)[:65000]
    with store.tx() as t:
        t.update("clientes", c["id"], {"ultimo_contacto": store.now(), "intervalo": iv, "info": texto,
                                       "versao": str(info.get("versao", ""))[:20], "ip_origem": ip[:64],
                                       "estado_contacto": "ok"})
    try:
        from . import clientes_alarmes
        clientes_alarmes.avaliar(obter(c["id"]))
    except Exception:  # noqa: BLE001 - um alarme com problemas nao pode impedir a sincronizacao
        import logging
        logging.getLogger("portal").exception("alarmes do cliente %s", c["nome"])
    if c.get("estado_contacto") == "perdido":
        eventos.emitir("clientes.contacto", "recuperou", f"Cliente {c['nome']}: o agente voltou a contactar",
                       f"Último contacto anterior: {c.get('ultimo_contacto')}.", link, dados={"cliente": c["nome"]})
    if extras:
        guardar_dados(c["id"], extras)
        _dados_recebidos(c["id"], extras)
    ped = store.get_setting(f"cliente_pedido:{c['id']}") or ""
    if ped:
        store.save_settings({f"cliente_pedido:{c['id']}": ""})
        _dados_entregues(c["id"], "na sincronização")
    atualizar = None
    _fechar_atualizacao(c, info)
    pedido_em = store.get_setting(f"cliente_atualizar:{c['id']}") or ""
    if pedido_em and _v(info.get("versao") or "0") >= (1, 5, 0):
        pedido_em = ""                      # 1.5+: recebe pela consulta de comandos
    if pedido_em:
        v, sha = agente_portal()
        ult = info.get("atualizacao") if isinstance(info.get("atualizacao"), dict) else {}
        if ult.get("erro") and ult.get("para") == v and (ult.get("quando") or "").replace("T", " ") >= pedido_em:
            # falhou depois deste pedido: nao insiste (o erro fica visivel no cliente)
            store.save_settings({f"cliente_atualizar:{c['id']}": ""})
        elif v and _v(v) > _v(info.get("versao") or "0"):
            atualizar = {"versao": v, "sha256": sha}
            with store.tx() as t:
                r = t.one("SELECT id, estado FROM cliente_comandos WHERE cliente_id = ? AND tipo = 'atualizar' "
                          "AND estado IN ('PENDENTE', 'EM CURSO') ORDER BY id DESC", (c["id"],))
            if r and r["estado"] == "PENDENTE":
                comando_estado(r["id"], "EM CURSO")
                comando_linha(r["id"], f"entregue ao agente {info.get('versao')} na sincronização: vai descarregar, "
                                       "substituir-se e reiniciar")
        else:
            store.save_settings({f"cliente_atualizar:{c['id']}": ""})     # ja esta na versao do portal
    fl = [{"id": r["id"], "pasta": r["pasta"], "ficheiro": r["ficheiro"], "linhas": r["linhas"] or 200}
          for r in logs_cfg(c["id"])]
    return {"ok": True, "nome": c["nome"], "intervalo": iv, "pedidos": [x for x in ped.split(",") if x],
            "ficheiros_log": fl, "atualizar": atualizar, "intervalos": intervalos_recolha(),
            "servicos": servicos_monit(c["id"]),
            "regras": [{"id": r["codigo"], "nome": r["nome"], "log": r["log_path"] or "",
                        "padrao_erro": r["padrao_erro"] or "", "padrao_aviso": r["padrao_aviso"] or ""}
                       for r in regras(c["id"], so_ativas=True)]}


def receber_eventos(c: dict, evs: list, link: str = "") -> int:
    validas = {r["codigo"]: r for r in regras(c["id"])}
    n = 0
    for e in (evs or [])[:500]:
        if not isinstance(e, dict):
            continue
        cod = str(e.get("regra", ""))[:40]
        est = e.get("estado") if e.get("estado") in ESTADOS_EV else "info"
        msg = str(e.get("mensagem", ""))[:4000]
        quando = str(e.get("quando", ""))[:19].replace("T", " ")
        r = validas.get(cod)
        with store.tx() as t:
            t.insert("cliente_eventos", {"cliente_id": c["id"], "regra": cod, "quando": quando or store.now(),
                                         "recebido": store.now(), "estado": est,
                                         "origem": str(e.get("origem", ""))[:10] if r else "desconhecida",
                                         "mensagem": msg})
        n += 1
        if r and r["ativo"]:
            eventos.emitir("clientes.notificacao", SITUACAO[est], f"{c['nome']} · {r['nome']}: {ESTADOS_EV[est]}",
                           msg, link, dados={"regra": cod, "cliente": c["nome"]})
    return n


def verificar_contactos(_par=None) -> tuple[str, str]:
    """Tarefa agendada: avisa quando um cliente deixa de contactar (e o agente voltar)."""
    perdidos, novos = [], []
    for c in listar():
        if not c.get("ultimo_contacto") or c["contacto"]["cor"] != "vermelho":
            continue
        perdidos.append(c["nome"])
        if c.get("estado_contacto") != "perdido":
            novos.append(c["nome"])
            with store.tx() as t:
                t.update("clientes", c["id"], {"estado_contacto": "perdido"})
            eventos.emitir("clientes.contacto", "perdido", f"Cliente {c['nome']}: sem contacto do agente",
                           f"Último contacto: {c['ultimo_contacto']} (intervalo {c.get('intervalo')} s).",
                           "/clientes/painel", dados={"cliente": c["nome"]})
    from . import clientes_alarmes
    clientes_alarmes.avaliar_contacto()
    with store.tx() as t:
        t.run("DELETE FROM cliente_eventos WHERE recebido < ?", (store.ago(days=int(store.get_setting("clientes_dias_eventos") or 90)),))
    if perdidos:
        return "aviso", "Sem contacto: " + ", ".join(perdidos) + (f" (novos: {', '.join(novos)})" if novos else "")
    return "sucesso", "Todos os clientes com contacto recente (ou nunca contactaram)."


# ============================================================ SSH (chave do portal e teste)
def _dir_ssh() -> str:
    d = settings.path("clientes_ssh")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def chave_publica(criar: bool = True) -> str:
    d = _dir_ssh()
    priv = os.path.join(d, "id_ed25519")
    if not os.path.exists(priv + ".pub") and criar:
        inst = os.environ.get("PORTAL_INSTANCIA") or "portal"
        try:
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"{inst}-clientes", "-f", priv],
                           capture_output=True, timeout=20, check=True)
        except FileNotFoundError:            # sem ssh-keygen: gera com a biblioteca cryptography
            _gerar_chave(priv, f"{inst}-clientes")
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Não consegui criar a chave SSH do portal (ssh-keygen): {exc}") from None
    try:
        with open(priv + ".pub") as f:
            return f.read().strip()
    except OSError:
        return ""


def _gerar_chave(priv: str, comentario: str) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    k = ed25519.Ed25519PrivateKey.generate()
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
    pub = k.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
    fd = os.open(priv, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    with open(priv + ".pub", "w") as f:
        f.write(pub.decode() + " " + comentario + "\n")


def impressao_host(host: str, porta: int) -> str:
    kh = os.path.join(_dir_ssh(), "known_hosts")
    alvo = host if porta == 22 else f"[{host}]:{porta}"
    try:
        r = subprocess.run(["ssh-keygen", "-l", "-F", alvo, "-f", kh], capture_output=True, text=True, timeout=10)
        return "\n".join(l for l in r.stdout.splitlines() if not l.startswith("#")).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def esquecer_host(host: str, porta: int) -> str:
    kh = os.path.join(_dir_ssh(), "known_hosts")
    alvo = host if porta == 22 else f"[{host}]:{porta}"
    r = subprocess.run(["ssh-keygen", "-R", alvo, "-f", kh], capture_output=True, text=True, timeout=10)
    return (r.stdout + r.stderr).strip()


def ip_origem(host: str, porta: int = 22) -> str:
    """IP deste servidor (portal) usado para chegar ao host: e este que tem de estar no ipset/regras do cliente."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, porta))           # UDP: nao envia nada, so escolhe a rota/interface
            return s.getsockname()[0]
    except OSError:
        return ""


def _no_ipset(ip: str, c: dict) -> str:
    """Com o estado da firewall do cliente (aba Firewall): em que ipsets/origens esta o IP do portal."""
    import ipaddress
    fw = ((dados(c["id"]).get("firewall") or {}).get("dados")) or {}
    if not ip or fw.get("backend") != "firewalld":
        return ""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    dentro = []
    for s in fw.get("ipsets") or []:
        for e in s.get("entradas") or []:
            try:
                if a in ipaddress.ip_network(e, strict=False):
                    dentro.append(f"{s['nome']} ({e})")
                    break
            except ValueError:
                continue
    nomes = [s["nome"] for s in fw.get("ipsets") or []]
    if dentro:
        return f"O IP do portal {ip} está no ipset {', '.join(dentro)} (estado da firewall lido {str(fw.get('lido', ''))[:16].replace('T', ' ')})."
    return (f"O IP do portal {ip} NÃO está em nenhum ipset do cliente ({', '.join(nomes) or 'nenhum ipset'}): junte-o "
            "na aba Firewall (Serviço Oracle do agente) ou com firewall-cmd --permanent --ipset=ipset-agente-portal --add-entry=" + ip + ".")


def testar(c: dict) -> list[dict]:
    """Passos: DNS, IP de origem do portal, servico Oracle do agente (porta RPC), porta SSH, SSH com a chave, agente."""
    passos = []
    host, porta, util = c["endereco"], int(c.get("ssh_porta") or 22), c.get("ssh_utilizador") or ""
    try:
        ips = sorted({a[4][0] for a in socket.getaddrinfo(host, porta, proto=socket.IPPROTO_TCP)})
        passos.append({"passo": "DNS", "ok": True, "detalhe": ", ".join(ips)})
    except OSError as exc:
        passos.append({"passo": "DNS", "ok": False, "detalhe": str(exc)})
        return passos
    origem = ip_origem(host, porta)
    if origem:
        passos.append({"passo": "IP do portal", "ok": True,
                       "detalhe": f"O portal liga-se a {host} a partir de {origem}: é este IP que tem de estar no ipset / regras do cliente."})
    agente_ok = c["contacto"]["cor"] in ("verde", "laranja")
    rpc = (c.get("info") or {}).get("rpc")
    if rpc:
        prpc = int(rpc.get("porta") or 8795)
        nome = f"Serviço Oracle do agente ({prpc}/tcp)"
        extra = _no_ipset(origem, c)
        orig = rpc.get("origens") or []
        if orig and origem:
            import ipaddress
            try:
                dentro = any(ipaddress.ip_address(origem) in ipaddress.ip_network(o, strict=False) for o in orig)
            except ValueError:
                dentro = True
            if not dentro:
                extra += (f" Atenção: o agente só aceita pedidos de {', '.join(orig)} (RPC_ORIGENS) e o portal vem de {origem}: "
                          f"sudo agente-portal configurar --origens \"{' '.join(orig + [origem])}\".")
        try:
            with socket.create_connection((host, prpc), timeout=6):
                pass
            from . import agente_rpc
            a, motivo = agente_rpc.alvo_cliente(c, testar=True)
            if a:
                try:
                    agente_rpc.chamar(a, "/rpc/ping", {}, timeout=10)
                    passos.append({"passo": nome, "ok": True, "detalhe": "ligação TLS e pedido assinado OK."
                                   + (" " + extra if " está no ipset" in extra else "")})
                except Exception as exc:  # noqa: BLE001
                    passos.append({"passo": nome, "ok": False, "detalhe": f"a porta abre mas o pedido falhou: {exc}. {extra}"})
            else:
                dica = ""
                if "parado" in motivo and "sem detalhe" in motivo:
                    dica = (" A porta responde, por isso o serviço está provavelmente a correr: este estado veio de um "
                            "'agente-portal testar' (ou do fim do setup.sh), que corre noutro processo — agentes anteriores à 1.8.1 "
                            "enviavam-no como parado. Aguarde a sincronização seguinte do serviço ou atualize o agente.")
                passos.append({"passo": nome, "ok": None, "detalhe": f"porta aberta; {motivo}.{dica} {extra}"})
        except OSError as exc:
            n = getattr(exc, "errno", None)
            causa = {113: (f"recusado com 'host prohibited/no route': quase sempre a firewall do cliente (firewalld) — a regra não "
                           f"apanha o IP {origem or 'do portal'} (ipset sem esse IP, regra noutra zona, ou zona ligada a uma origem "
                           "que tem prioridade). Se a regra e o ipset estiverem certos, é uma firewall no caminho."),
                     111: "ligação recusada: o serviço Oracle do agente não está a escutar (agente-portal estado).",
                     }.get(n, "sem resposta: uma firewall pelo caminho (ou do cliente) está a descartar os pacotes.")
            passos.append({"passo": nome, "ok": False,
                           "detalhe": f"{exc} — {causa} {extra} Para saber se os pacotes chegam ao cliente, no cliente: "
                                      f"sudo tcpdump -ni any tcp port {prpc} and host {origem or '<IP do portal>'} "
                                      "(e carregue em Testar outra vez): se aparecem, é a firewall do cliente; se não aparece nada, "
                                      "é a rede pelo caminho."})
    try:
        with socket.create_connection((host, porta), timeout=5) as s:
            s.settimeout(5)
            banner = s.recv(200).decode(errors="replace").strip()
        passos.append({"passo": f"Porta SSH {porta}", "ok": True, "detalhe": banner or "aberta"})
    except OSError as exc:
        passos.append({"passo": f"Porta SSH {porta}", "ok": None if agente_ok else False,
                       "detalhe": f"{exc} — " + ("nada a escutar na porta (sshd parado ou noutra porta). "
                                                 if getattr(exc, "errno", None) == 111 else
                                                 f"a porta SSH {porta}/tcp está fechada para o portal ({origem or '?'}). ") +
                                  "O SSH só é usado para instalar/testar o agente por SSH; o agente e o serviço Oracle não precisam dele. "
                                  "Para abrir: aba Firewall do cliente (serviço ssh ou regra rica para o ipset)."})
    if not util:
        passos.append({"passo": "SSH", "ok": None, "detalhe": "Sem utilizador SSH definido no cliente."})
    else:
        try:
            chave_publica()
            d = _dir_ssh()
            cmd = ["ssh", "-i", os.path.join(d, "id_ed25519"), "-p", str(porta), "-o", "BatchMode=yes",
                   "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={os.path.join(d, 'known_hosts')}",
                   "-o", "ConnectTimeout=8", "-o", "PasswordAuthentication=no", "-o", "LogLevel=ERROR",
                   f"{util}@{host}",
                   "echo PORTAL_OK; uname -sr; (test -x /opt/agente-portal/agente-portal.py && "
                   "/opt/agente-portal/agente-portal.py estado --curto) || echo 'agente: nao instalado'"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            out = (r.stdout + r.stderr).strip()
            if r.returncode == 0 and "PORTAL_OK" in out:
                det = out.replace("PORTAL_OK", "").strip()
                fp = impressao_host(host, porta)
                passos.append({"passo": f"SSH {util}@{host}", "ok": True,
                               "detalhe": det + (f"\nChave do servidor (known_hosts): {fp}" if fp else "")})
            else:
                dica = ""
                if "IDENTIFICATION HAS CHANGED" in out or "Host key verification failed" in out:
                    dica = " A chave do servidor mudou: confirme e use \"Esquecer a chave do servidor\"."
                elif "Permission denied" in out:
                    dica = (f" A chave pública do portal não está no authorized_keys de {util} "
                            "(o setup do agente acrescenta-a) ou as permissões estão erradas.")
                passos.append({"passo": f"SSH {util}@{host}", "ok": False, "detalhe": out[-800:] + dica})
        except FileNotFoundError:
            passos.append({"passo": "SSH", "ok": False, "detalhe": "Cliente ssh não instalado no servidor do portal."})
        except (RuntimeError, subprocess.SubprocessError) as exc:
            passos.append({"passo": "SSH", "ok": False, "detalhe": str(exc)})
    ct = c["contacto"]
    passos.append({"passo": "Agente", "ok": True if ct["cor"] == "verde" else (None if ct["cor"] == "cinza" else False),
                   "detalhe": ct["texto"] + (f" (último contacto há {ct['seg']} s, intervalo {c.get('intervalo')} s)"
                                             if ct["seg"] is not None else " (instale o agente com os comandos da página)")})
    return passos


def ficheiro_agente(nome: str) -> str | None:
    if nome not in ("setup.sh", "agente-portal.py"):
        return None
    p = os.path.join(AGENTE_DIR, nome)
    return p if os.path.exists(p) else None
