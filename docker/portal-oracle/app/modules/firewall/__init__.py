"""Modulo Firewall: firewalld (zonas, servicos, portas, regras ricas, ipsets) ou ufw, no servidor do
portal. Ler e alterar com 'sudo -n firewall-cmd/ufw' (instalar.sh --firewall NOME on)."""
from flask import Blueprint, abort, g

from ... import modulos

bp = Blueprint("firewall", __name__, url_prefix="/firewall", template_folder="templates")

MODULO = modulos.registar(modulos.Modulo(
    chave="firewall",
    nome="Firewall",
    descricao="Firewall do servidor do portal (firewalld ou ufw): estado, zonas, serviços, portas, regras ricas, "
              "ipsets e registo; criar e apagar regras e ipsets.",
    acoes={"firewall.alterar": "Alterar a firewall: regras, portas, serviços, ipsets, recarregar"},
    abas=[
        ("firewall.geral", "Geral", None),
        ("firewall.regras", "Regras", None),
        ("firewall.ipsets", "IPsets", None),
        ("firewall.logs", "Logs", None),
        ("firewall.definicoes", "Definições", None),
    ],
    ativo_por_omissao=False,
))


@bp.before_request
def _guard():
    if MODULO.chave not in modulos.ativos():
        abort(404)
    if g.get("user") and not g.user.can(MODULO.acesso):
        abort(403)


from . import views  # noqa: E402,F401
