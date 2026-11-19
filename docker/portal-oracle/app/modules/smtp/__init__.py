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
    ativo_por_omissao=False,
))


@bp.before_request
def _guard():
    if MODULO.chave not in modulos.ativos():
        abort(404)
    if g.get("user") and not g.user.can(MODULO.acesso):
        abort(403)


from . import views  # noqa: E402,F401
