#!/usr/bin/env python3
"""send_mail.py - envia um email por SMTP (mount-islive).

Uso: send_mail.py "<assunto>" < corpo.txt

Configuração por variáveis de ambiente (carregadas do config/.env pelo check_mount.sh / setup.sh):
  SMTP_SERVER, SMTP_PORT, SMTP_SECURITY (none|starttls|ssl), SMTP_TLS_VERIFY (sim|nao),
  SMTP_USER, SMTP_PASSWORD (já decifrada), MAIL_FROM, MAIL_TO (separados por vírgula)
"""
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formatdate, make_msgid


def main() -> int:
    if len(sys.argv) < 2:
        print("Uso: send_mail.py \"<assunto>\" < corpo", file=sys.stderr)
        return 2

    subject = sys.argv[1]
    body = sys.stdin.read()

    server = os.environ.get("SMTP_SERVER", "")
    port = int(os.environ.get("SMTP_PORT") or 25)
    security = os.environ.get("SMTP_SECURITY", "none").lower()
    verify = os.environ.get("SMTP_TLS_VERIFY", "nao").lower() in ("sim", "s", "yes", "y", "true", "1")
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    mail_from = os.environ.get("MAIL_FROM", "")
    mail_to = [a.strip() for a in os.environ.get("MAIL_TO", "").replace(";", ",").split(",") if a.strip()]

    if not server or not mail_from or not mail_to:
        print("Configuração SMTP incompleta (SMTP_SERVER, MAIL_FROM, MAIL_TO).", file=sys.stderr)
        return 2

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = ", ".join(mail_to)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=mail_from.split("@")[-1] or None)
    msg.set_content(body)

    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    try:
        if security == "ssl":
            smtp = smtplib.SMTP_SSL(server, port, timeout=30, context=ctx)
        else:
            smtp = smtplib.SMTP(server, port, timeout=30)
            smtp.ehlo()
            if security == "starttls":
                smtp.starttls(context=ctx)
                smtp.ehlo()
        with smtp:
            if user:
                smtp.login(user, password)
            smtp.send_message(msg, from_addr=mail_from, to_addrs=mail_to)
    except Exception as exc:  # noqa: BLE001 - reportar qualquer falha ao chamador
        print(f"Erro ao enviar email: {exc}", file=sys.stderr)
        return 1

    print(f"Email enviado para: {', '.join(mail_to)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
