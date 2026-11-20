"""Modulo SMTP: ligacao ao servidor de email (interno, com TLS), regras de email e de notificacao do
portal por evento dos modulos, e registo dos envios. Quem envia e valida e o agente do portal."""
from flask import Blueprint, abort, g

from ... import modulos

bp = Blueprint("smtp", __name__, url_prefix="/smtp", template_folder="templates")

MODULO = modulos.registar(modulos.Modulo(
    chave="smtp",
    nome="SMTP",
    descricao="Email pelo servidor SMTP interno (TLS): avisos quando uma execução termina ou falha, quando um "
              "serviço para, quando um disco enche. Regras por evento, notificações no portal e registo dos envios.",
    acoes={
        "smtp.configurar": "Configurar a ligação SMTP, validar e enviar testes",
        "smtp.regras": "Gerir as regras de email e de notificação",
    },
    abas=[
        ("smtp.geral", "Geral", None),
        ("smtp.regras", "Regras", None),
        ("smtp.notificacoes", "Notificações", None),
        ("smtp.logs", "Logs", None),
    ],
    eventos={"smtp.ligacao": {"nome": "Ligação SMTP (validação periódica pelo agente)",
                              "situacoes": {"falhou": "Falhou", "recuperou": "Recuperou"}}},
    cartoes={
        "smtp.estado": {"titulo": "Email (SMTP)", "icone": "✉️", "template": "smtp/c_estado.html",
                        "link": "smtp.logs", "link_txt": "Ver os envios"},
    },
    home_context=lambda user: _home_context(user),
    tarefas={"smtp.validar": {
        "nome": "Validação da ligação SMTP", "funcao": lambda par: _t_validar(par),
        "descricao": "Liga ao servidor de email (TLS e autenticação), sem enviar. Se falhar, gera o evento "
                     "\"Ligação SMTP: falhou\"; quando volta, \"recuperou\".",
        "padrao": {"ativo": True, "freq": "minutos", "intervalo": 60, "notificar": "nunca"}}},
    ativo_por_omissao=False,
    em_config=True,
))


def _home_context(user) -> dict:
    from ... import smtp_envio, store
    desde = store.ago(hours=24)
    with store.tx() as t:
        cont = {r["status"]: int(r["n"]) for r in t.all(
            "SELECT status, COUNT(*) AS n FROM email_queue WHERE created_at >= ? OR status IN ('PENDENTE', 'A ENVIAR') "
            "GROUP BY status", (desde,))}
        ultimo = t.one("SELECT assunto, status, created_at FROM email_queue ORDER BY id DESC LIMIT 1")
    s = store.get_settings()
    return {"estado": smtp_envio.estado(), "ativo": s.get("smtp_ativo") == "1", "host": s.get("smtp_host") or "",
            "porta": s.get("smtp_porta") or "", "seg": s.get("smtp_seguranca") or "", "cont": cont, "ultimo": ultimo}


@bp.before_request
def _guard():
    if MODULO.chave not in modulos.ativos():
        abort(404)
    if g.get("user") and not g.user.can(MODULO.acesso):
        abort(403)


from . import views  # noqa: E402,F401


def _t_validar(par: dict) -> tuple[str, str]:
    from ... import smtp_envio, store
    if not store.get_setting("smtp_host"):
        return "aviso", "Servidor SMTP por configurar (SMTP → Geral)."
    ok, msg = smtp_envio.validar()
    return ("sucesso" if ok else "erro"), msg
