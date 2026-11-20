"""Envio de email por SMTP (servidor interno), como no monitor RMAN do repositorio:

  seguranca   nenhuma | starttls (porta 587/25) | ssl (SMTPS, porta 465)
  verificar   valida o certificado TLS (desligado por omissao: servidores internos com CA privada)
  utilizador  opcional. Se o servidor nao anunciar AUTH (ex. porta 25 sem STARTTLS) ou so tiver
              mecanismos nao suportados, envia sem autenticar e regista um aviso.

O dialogo SMTP fica registado (sem a password) na mensagem da fila (email_queue.log), e pode ser
acompanhado em direto no teste (SMTP > Geral). Quem envia a fila e o agente; sem agente, o portal.
"""
import json
import smtplib
import socket
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from . import crypto, store

MAX_TENTATIVAS = 3
_lock = threading.Lock()                 # uma fila de cada vez neste processo


def config() -> dict:
    s = store.get_settings()
    pw = ""
    if s.get("smtp_password_enc"):
        try:
            pw = crypto.decrypt(s["smtp_password_enc"])
        except Exception:  # noqa: BLE001 - chave mudou
            pw = ""
    return {"host": _host(s.get("smtp_host", "")), "porta": int(s.get("smtp_porta") or 25),
            "seguranca": s.get("smtp_seguranca") or "starttls", "verificar": s.get("smtp_verificar") == "1",
            "utilizador": s.get("smtp_utilizador", ""), "password": pw,
            "remetente": s.get("smtp_remetente", ""), "remetente_nome": s.get("smtp_remetente_nome", ""),
            "timeout": int(s.get("smtp_timeout") or 20), "prefixo": s.get("smtp_prefixo", ""),
            "helo": s.get("smtp_helo") or socket.getfqdn()}


class Registo:
    """Linhas do dialogo SMTP, com a password escondida. `flush` e chamado a cada linha nova."""

    def __init__(self, flush=None):
        self.linhas: list[str] = []
        self.flush = flush
        self._t = 0.0
        self._auth = False                  # entre "### autenticação" e o resultado: esconder o que e enviado

    def __call__(self, texto: str) -> None:
        for l in str(texto).splitlines() or [""]:
            if l.startswith("### autentica"):
                self._auth = not l.startswith("### autenticado")
            elif l.startswith("### "):
                self._auth = False
            elif self._auth and l.startswith("send:"):
                l = "send: ****** (credenciais escondidas)"
            elif "AUTH " in l.upper() and l.startswith("send:"):
                l = "send: AUTH ******"
            self.linhas.append(f"{time.strftime('%H:%M:%S')} {l}")
        if self.flush and time.time() - self._t > 0.3:
            self._t = time.time()
            self.flush(self.texto())

    def texto(self) -> str:
        return "\n".join(self.linhas)


def _host(h: str) -> str:
    """'smtp://mail.empresa.pt:587/' ou ' mail.empresa.pt. ' -> 'mail.empresa.pt' (a porta e um campo a parte)."""
    h = (h or "").strip()
    for pre in ("smtps://", "smtp://"):
        if h.lower().startswith(pre):
            h = h[len(pre):]
    h = h.split("/")[0].strip()
    if h.count(":") == 1:
        h = h.split(":")[0]
    return h.strip(".")


def _classe(base):
    class C(base):
        registo: Registo = None

        def _print_debug(self, *args):             # captura o dialogo (debuglevel 1)
            if self.registo:
                self.registo(" ".join(str(a) for a in args))
    return C


SMTP, SMTP_SSL = _classe(smtplib.SMTP), _classe(smtplib.SMTP_SSL)


def _contexto(verificar: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not verificar:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def ligar(cfg: dict, reg: Registo):
    """Liga, EHLO, STARTTLS e AUTH conforme a configuracao. Devolve a ligacao aberta."""
    if not cfg["host"]:
        raise RuntimeError("Servidor SMTP não configurado (SMTP > Geral).")
    seg = cfg["seguranca"]
    reg(f"### a ligar a {cfg['host']}:{cfg['porta']} (segurança: {seg}, "
        f"certificado {'validado' if cfg['verificar'] else 'não validado'})")
    ctx = _contexto(cfg["verificar"])
    if seg == "ssl":
        s = SMTP_SSL(timeout=cfg["timeout"], context=ctx, local_hostname=cfg["helo"])
    else:
        s = SMTP(timeout=cfg["timeout"], local_hostname=cfg["helo"])
    s.registo = reg
    s.set_debuglevel(1)
    # o nome do servidor tem de ficar em _host antes do connect: e o que o smtplib usa no TLS (SNI e validacao do
    # certificado). Criado sem host, fica "" e o STARTTLS/SSL falha com "server_hostname cannot be an empty string"
    s._host = cfg["host"]
    s.connect(cfg["host"], cfg["porta"])
    s.ehlo()
    if seg == "starttls":
        if not s.has_extn("starttls"):
            raise RuntimeError("O servidor não anuncia STARTTLS: use outra porta (ex. 587) ou a segurança 'nenhuma'.")
        reg("### STARTTLS")
        s.starttls(context=ctx)
        s.ehlo()
        tls = s.sock.version() if hasattr(s.sock, "version") else "TLS"
        reg(f"### canal cifrado ({tls})")
    elif seg == "ssl":
        reg(f"### canal cifrado ({s.sock.version()})")
    if cfg["utilizador"]:
        if not s.has_extn("auth"):
            reg("### AVISO: o servidor não anuncia AUTH: envio sem autenticação")
        else:
            mecs = (s.esmtp_features.get("auth") or "").upper().split()
            if not {"PLAIN", "LOGIN", "CRAM-MD5"} & set(mecs):
                reg(f"### AVISO: mecanismos AUTH não suportados ({' '.join(mecs)}): envio sem autenticação")
            else:
                reg(f"### autenticação como {cfg['utilizador']}")
                s.login(cfg["utilizador"], cfg["password"])
                reg("### autenticado")
    return s


def enviar(destinatarios: list[str], assunto: str, corpo: str, reg: Registo, cfg: dict | None = None) -> None:
    cfg = cfg or config()
    if not cfg["remetente"]:
        raise RuntimeError("Remetente não configurado (SMTP > Geral).")
    msg = EmailMessage()
    msg["From"] = formataddr((cfg["remetente_nome"], cfg["remetente"])) if cfg["remetente_nome"] else cfg["remetente"]
    msg["To"] = ", ".join(destinatarios)
    msg["Subject"] = f"{cfg['prefixo']} {assunto}".strip()
    msg["Message-ID"] = make_msgid(domain=cfg["remetente"].split("@")[-1])
    msg.set_content(corpo)
    s = ligar(cfg, reg)
    try:
        reg(f"### a enviar para {', '.join(destinatarios)}")
        recusados = s.send_message(msg)
        if recusados:
            reg(f"### AVISO: recusados pelo servidor: {recusados}")
        reg("### mensagem aceite pelo servidor")
    finally:
        try:
            s.quit()
        except Exception:  # noqa: BLE001
            pass


def validar(reg: Registo | None = None) -> tuple[bool, str]:
    """Liga, STARTTLS e AUTH, sem enviar. Guarda o resultado em smtp_estado."""
    reg = reg or Registo()
    try:
        c = config()
        s = ligar(c, reg)
        s.quit()
        partes = ["Ligação"] + (["TLS"] if c["seguranca"] != "nenhuma" else []) + (["autenticação"] if c["utilizador"] else [])
        ok, msg = True, (", ".join(partes[:-1]) + " e " + partes[-1] if len(partes) > 1 else partes[0]) + " OK"
    except Exception as exc:  # noqa: BLE001
        ok, msg = False, _erro(exc)
        reg(f"### ERRO: {msg}")
    anterior = estado()
    store.save_settings({"smtp_estado": json.dumps({"ok": ok, "msg": msg, "quando": store.now(), "ts": time.time(),
                                                    "log": reg.texto()[-6000:]})})
    if anterior and anterior.get("ok") != ok:
        from . import eventos
        eventos.emitir("smtp.ligacao", "recuperou" if ok else "falhou",
                       "Ligação SMTP " + ("recuperada" if ok else "com falha"), msg, "/smtp/geral")
    return ok, msg


def estado() -> dict:
    try:
        return json.loads(store.get_setting("smtp_estado") or "{}")
    except ValueError:
        return {}


def _erro(exc: Exception) -> str:
    t = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in t:
        t += " — o certificado não é reconhecido (CA interna?): desligue 'Validar certificado' ou instale a CA."
    elif isinstance(exc, smtplib.SMTPAuthenticationError):
        t += " — utilizador ou password errados."
    elif "530" in t and "auth" in t.lower():
        t += " — o servidor exige autenticação (use STARTTLS na porta 587 com utilizador)."
    elif isinstance(exc, (ConnectionRefusedError, socket.timeout, TimeoutError)):
        t += " — servidor ou porta inacessível (firewall?)."
    return t


# ============================================================================ fila
def _atualizar(eid: int, **campos) -> None:
    with store.tx() as t:
        t.update("email_queue", eid, campos)


def enviar_registo(e: dict) -> bool:
    """Envia uma mensagem da fila (email ou teste) e regista o resultado e o dialogo."""
    reg = Registo(flush=lambda txt: _atualizar(e["id"], log=txt))
    _atualizar(e["id"], status="A ENVIAR", tentativas=(e.get("tentativas") or 0) + 1)
    try:
        dest = [d.strip() for d in (e["destinatarios"] or "").split(",") if "@" in d]
        if not dest:
            raise RuntimeError("Sem destinatários.")
        enviar(dest, e["assunto"], e["corpo"] or "", reg)
        _atualizar(e["id"], status="ENVIADO", enviado_em=store.now(), erro=None, log=reg.texto())
        return True
    except Exception as exc:  # noqa: BLE001
        msg = _erro(exc)
        reg(f"### ERRO: {msg}")
        n = (e.get("tentativas") or 0) + 1
        final = e["tipo"] == "teste" or n >= MAX_TENTATIVAS
        _atualizar(e["id"], status="ERRO" if final else "PENDENTE", erro=msg, log=reg.texto(),
                   proxima=None if final else store.ago(minutes=-2 * n))
        return False


def processar_fila(limite: int = 20) -> int:
    """Envia as mensagens PENDENTE (testes primeiro). Devolve quantas tentou."""
    if not _lock.acquire(blocking=False):
        return 0
    try:
        with store.tx() as t:
            rows = t.all("""SELECT * FROM email_queue
                             WHERE status = 'PENDENTE' AND (proxima IS NULL OR proxima <= ?)
                             ORDER BY CASE WHEN tipo = 'teste' THEN 0 ELSE 1 END, id LIMIT ?""",
                         (store.now(), int(limite)))
        for e in rows:
            with store.tx() as t:      # outro processo (portal/agente) pode ja ter pegado nela
                if t.run("UPDATE email_queue SET status = 'A ENVIAR' WHERE id = ? AND status = 'PENDENTE'",
                         (e["id"],)) != 1:
                    continue
            enviar_registo(e)
        return len(rows)
    finally:
        _lock.release()


def pedir_teste(destinatario: str, por: str) -> int:
    nome = store.get_setting("portal_name") or "Portal"
    corpo = (f"Mensagem de teste do {nome}.\n\nSe a recebeu, o envio de email (SMTP) está a funcionar.\n\n"
             f"Pedido por: {por}\nData: {store.now()}\n")
    with store.tx() as t:
        return t.insert("email_queue", {"created_at": store.now(), "tipo": "teste", "evento": "smtp.teste",
                                        "regra": "teste", "destinatarios": destinatario,
                                        "assunto": f"Teste de email — {nome}", "corpo": corpo,
                                        "status": "PENDENTE", "tentativas": 0, "pedido_por": por})


def recuperar_presas() -> None:
    """Ao arrancar: mensagens que ficaram 'A ENVIAR' (processo terminou a meio) voltam a PENDENTE."""
    with store.tx() as t:
        t.run("UPDATE email_queue SET status = 'PENDENTE' WHERE status = 'A ENVIAR'")
