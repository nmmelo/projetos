"""Modulo Autenticacao: utilizadores autenticados por LDAP (OpenLDAP ou Active Directory), alem dos
locais. O administrador e sempre local. Configuracao em Autenticacao > LDAP (tambem a partir de
Configuracao > Autenticacao) e registo dos acessos em Autenticacao > Acessos."""
from flask import Blueprint, abort, g

from ... import modulos

bp = Blueprint("autenticacao", __name__, url_prefix="/autenticacao", template_folder="templates")

MODULO = modulos.registar(modulos.Modulo(
    chave="autenticacao",
    nome="Autenticação",
    descricao="Autenticação dos utilizadores por LDAP: OpenLDAP ou Active Directory (Microsoft), com TLS e "
              "certificado da CA, grupo obrigatório e criação automática no primeiro login. O administrador é sempre local.",
    acoes={
        "autenticacao.configurar": "Configurar e testar a ligação LDAP",
        "autenticacao.acessos": "Ver o registo de acessos (logins com sucesso e falhados)",
    },
    abas=[
        ("autenticacao.ldap", "LDAP", None),
        ("autenticacao.acessos", "Acessos", "autenticacao.acessos"),
    ],
    eventos={"autenticacao.ldap": {"nome": "Ligação LDAP (teste)", "situacoes": {"falhou": "Falhou"}}},
    ativo_por_omissao=False,
))


@bp.before_request
def _guard():
    if MODULO.chave not in modulos.ativos():
        abort(404)
    if g.get("user") and not g.user.can(MODULO.acesso):
        abort(403)


from . import views  # noqa: E402,F401
