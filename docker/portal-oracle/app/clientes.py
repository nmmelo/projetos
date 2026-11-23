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
TIPOS_DADOS = {"processos": "Processos", "sistema": "Sistema", "logs": "Logs", "atualizacoes": "Atualizações pendentes"}


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


def pedir_dados(cid: int, tipos: list[str]) -> None:
    """O agente envia estes dados na proxima sincronizacao (resposta do sync: 'pedidos')."""
    tipos = [x for x in tipos if x in TIPOS_DADOS] or list(TIPOS_DADOS)
    store.save_settings({f"cliente_pedido:{cid}": ",".join(tipos)})


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
    ped = store.get_setting(f"cliente_pedido:{c['id']}") or ""
    if ped:
        store.save_settings({f"cliente_pedido:{c['id']}": ""})
    return {"ok": True, "nome": c["nome"], "intervalo": iv, "pedidos": [x for x in ped.split(",") if x],
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


def testar(c: dict) -> list[dict]:
    """Passos: DNS, porta SSH, SSH com a chave do portal, agente."""
    passos = []
    host, porta, util = c["endereco"], int(c.get("ssh_porta") or 22), c.get("ssh_utilizador") or ""
    try:
        ips = sorted({a[4][0] for a in socket.getaddrinfo(host, porta, proto=socket.IPPROTO_TCP)})
        passos.append({"passo": "DNS", "ok": True, "detalhe": ", ".join(ips)})
    except OSError as exc:
        passos.append({"passo": "DNS", "ok": False, "detalhe": str(exc)})
        return passos
    try:
        with socket.create_connection((host, porta), timeout=5) as s:
            s.settimeout(5)
            banner = s.recv(200).decode(errors="replace").strip()
        passos.append({"passo": f"Porta SSH {porta}", "ok": True, "detalhe": banner or "aberta"})
    except OSError as exc:
        passos.append({"passo": f"Porta SSH {porta}", "ok": False, "detalhe": f"{exc} (firewall do cliente ou do caminho?)"})
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
