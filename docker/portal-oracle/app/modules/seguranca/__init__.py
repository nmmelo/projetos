"""Modulo Seguranca: auditoria (visitas, logins e bloqueios por IP), acesso por IP (no codigo ou no firewalld),
Autenticacao de Dois Fatores, fator humano no login, bloqueio por tentativas e registo por pedido (LDAP / AD,
validado por um administrador ou por um Gestor do portal). Logica em app/seguranca.py."""
from flask import Blueprint, abort, g

from ... import modulos

bp = Blueprint("seguranca", __name__, url_prefix="/seguranca", template_folder="templates")

MODULO = modulos.registar(modulos.Modulo(
    chave="seguranca",
    nome="Segurança",
    descricao="Auditoria dos acessos por IP, acesso ao portal só a partir de uma lista de IPs/redes (no portal ou "
              "no firewalld, com ipset e regras ricas), Autenticação de Dois Fatores, fator humano no login, bloqueio "
              "por tentativas falhadas e registo de utilizadores do diretório com validação.",
    acoes={
        "seguranca.auditoria": "Ver a auditoria (visitas, logins e bloqueios por IP)",
        "seguranca.configurar": "Configurar o acesso por IP, os dois fatores, o fator humano e o registo",
        "seguranca.pedidos": "Validar pedidos de registo (atribuir perfil, ativar ou recusar) e receber as notificações",
    },
    abas=[
        ("seguranca.auditoria", "Auditoria", "seguranca.auditoria"),
        ("seguranca.acesso_ip", "Acesso por IP", "seguranca.configurar"),
        ("seguranca.autenticacao", "Autenticação", "seguranca.configurar"),
        ("seguranca.registo", "Registo", "seguranca.pedidos"),
    ],
    eventos={
        "seguranca.registo": {"nome": "Pedido de registo de utilizador",
                              "situacoes": {"pendente": "Pendente", "aprovado": "Aprovado"}},
        "seguranca.tentativas": {"nome": "IP bloqueado por tentativas falhadas", "situacoes": {"bloqueado": "Bloqueado"}},
    },
    ativo_por_omissao=False,
))


@bp.before_request
def _guard():
    if MODULO.chave not in modulos.ativos():
        abort(404)
    if g.get("user") and not g.user.can(MODULO.acesso):
        abort(403)


@bp.context_processor
def _ctx():
    from ... import seguranca
    u = g.get("user")
    try:
        return {"n_pendentes": len(seguranca.pendentes()) if u and u.can("seguranca.pedidos") else 0}
    except Exception:  # noqa: BLE001
        return {"n_pendentes": 0}


from . import views  # noqa: E402,F401
