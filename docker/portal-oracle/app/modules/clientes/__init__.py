"""Modulo Clientes: servidores com o agente do portal (agente-cliente/), estilo agente Zabbix.
Gestao dos clientes em Configuracao > Clientes; estado, regras de notificacao e eventos aqui."""
from flask import Blueprint, abort, g, request

from ... import clientes, modulos

bp = Blueprint("clientes", __name__, url_prefix="/clientes", template_folder="templates")


def _home_context(user) -> dict:
    cs = clientes.listar()
    cont = {"verde": 0, "laranja": 0, "vermelho": 0, "cinza": 0}
    for c in cs:
        cont[c["contacto"]["cor"]] += 1
    from ... import clientes_alarmes
    return {"clientes": cs, "cont": cont, "eventos": clientes.listar_eventos(limite=6), "alarmes": clientes_alarmes.ativos()}


MODULO = modulos.registar(modulos.Modulo(
    chave="clientes",
    nome="Clientes",
    descricao="Servidores com o agente do portal instalado (como um agente Zabbix): estado (SO, CPU, memória, "
              "discos), último contacto, regras de notificação com id (scripts rman, shrink, ...) e eventos.",
    acoes={
        "clientes.gerir": "Criar, editar e apagar clientes, ver o token e testar a ligação (Configuração → Clientes)",
        "clientes.regras": "Criar, editar e apagar regras de notificação",
        "clientes.alarmes": "Criar, editar e apagar alarmes (limiares de disco, CPU, memória, carga, contacto)",
        "clientes.firewall": "Alterar a firewall (firewalld/ufw) dos clientes pelo agente (aba Firewall do cliente)",
    },
    abas=[
        ("clientes.painel", "Clientes", None),
        ("clientes.alarmes", "Alarmes", None),
        ("clientes.regras", "Regras de notificação", None),
        ("clientes.eventos", "Eventos", None),
        ("clientes.pedidos", "Pedidos", None),
        ("clientes.ferramentas", "Ferramentas", None),
    ],
    cartoes={"clientes.estado": {"titulo": "Clientes", "icone": "🖧", "template": "clientes/c_estado.html",
                                 "link": "clientes.painel", "link_txt": "Ver os clientes"}},
    eventos={"clientes.notificacao": {"nome": "Notificação de um cliente (agente)",
                                      "situacoes": {"erro": "Erro", "aviso": "Aviso", "sucesso": "OK", "info": "Informação"},
                                      "parametro": "IDs das regras (vazio = todas)"},
             "clientes.contacto": {"nome": "Contacto do agente cliente",
                                   "situacoes": {"perdido": "Sem contacto", "recuperou": "Voltou a contactar"}}},
    tarefas={"clientes.verificar": {
        "nome": "Verificar o contacto dos clientes", "funcao": lambda par: clientes.verificar_contactos(par),
        "descricao": "Gera o evento \"Contacto do agente cliente: sem contacto\" quando um cliente passa mais de 10 "
                     "intervalos sem sincronizar (e \"voltou\" quando regressa). Apaga eventos antigos.",
        "padrao": {"ativo": True, "freq": "minutos", "intervalo": 5, "notificar": "nunca"}}},
    home_context=_home_context,
    ativo_por_omissao=False,
))


@bp.before_request
def _guard():
    if MODULO.chave not in modulos.ativos():
        abort(404)
    if request.path.startswith(("/clientes/agente/", "/clientes/api/zabbix/")):     # APIs: autenticadas por token
        return None
    if g.get("user") and not g.user.can(MODULO.acesso):
        abort(403)


from . import views  # noqa: E402,F401
