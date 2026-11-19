"""Registo de modulos do portal.

Cada modulo e um pacote em app/modules/<chave>/ que define um objeto MODULO (esta classe) e um
Blueprint com url_prefix /<chave>. O modulo:
  - pode ser ativado/desativado em Configuracao > Portal > Modulos (estado na tabela settings,
    chave "module.<chave>"); desativado, o menu desaparece e as rotas respondem 404;
  - tem uma permissao de acesso "modulo.<chave>" e as suas acoes "<chave>.<acao>", atribuidas
    nos perfis (Configuracao > Portal > Perfis), agrupadas por modulo;
  - define as abas do seu menu e, opcionalmente, um bloco para a Home.

Para acrescentar um modulo: criar app/modules/<chave>/__init__.py com MODULO e bp, e acrescentar
a chave a INSTALADOS (abaixo). Ver README, seccao "Modulos".
"""
from dataclasses import dataclass, field

INSTALADOS = ("oracle",)                  # pacotes em app/modules/, pela ordem do menu


@dataclass
class Modulo:
    chave: str
    nome: str
    descricao: str
    acoes: dict = field(default_factory=dict)        # "<chave>.<acao>" -> descricao
    abas: list = field(default_factory=list)          # (endpoint, rotulo, permissao ou None)
    home_template: str | None = None                  # incluido na Home
    home_context: object = None                       # funcao(user) -> dict para o home_template
    ativo_por_omissao: bool = True

    @property
    def acesso(self) -> str:
        return f"modulo.{self.chave}"


REGISTO: dict[str, Modulo] = {}

# Permissoes do portal (nao pertencem a nenhum modulo)
ACOES_PORTAL = {
    "config.portal": "Gerir o portal: aparência, manutenção, módulos, utilizadores, perfis e backups "
                     "(e entrar durante a manutenção)",
}

# Codigos antigos (antes dos modulos) -> novos. Aplicado ao ler perfis e ao importar backups.
LEGADO = {
    "tablespaces.ver": "oracle.tablespaces",
    "shrink.analise": "oracle.shrink.analise",
    "shrink.executar": "oracle.shrink.executar",
    "config.bds": "oracle.definicoes",
}


def registar(m: Modulo) -> Modulo:
    REGISTO[m.chave] = m
    return m


def modulo_de(perm: str) -> str | None:
    """Chave do modulo a que pertence uma permissao ('oracle.shrink.executar' -> 'oracle')."""
    if perm.startswith("modulo."):
        return perm.split(".", 1)[1]
    chave = perm.split(".", 1)[0]
    return chave if chave in REGISTO else None


def normalizar_permissoes(perms, legado: bool = False, tinha_bds: bool = False) -> list[str]:
    """Converte os codigos antigos. Com legado=True (perfil anterior aos modulos), um perfil com acoes
    Oracle ou com bases de dados atribuidas recebe tambem o acesso ao modulo Oracle, que antes estava
    sempre visivel (Execucoes)."""
    out, antigo = set(), False
    for p in perms or []:
        if p in LEGADO:
            out.add(LEGADO[p]); antigo = True
        else:
            out.add(p)
    if legado and (antigo or tinha_bds) and not any(p.startswith("modulo.") for p in out):
        out.add("modulo.oracle")
    return sorted(out)


def todas_acoes() -> dict:
    """codigo -> (grupo, descricao), para o formulario dos perfis."""
    acoes = {k: ("Portal", v) for k, v in ACOES_PORTAL.items()}
    for m in REGISTO.values():
        acoes[m.acesso] = (m.nome, f"Acesso ao módulo {m.nome}")
        acoes.update({k: (m.nome, v) for k, v in m.acoes.items()})
    return acoes


def carregar(app) -> None:
    """Importa os modulos instalados e regista os seus blueprints."""
    import importlib
    for chave in INSTALADOS:
        pkg = importlib.import_module(f"app.modules.{chave}")
        app.register_blueprint(pkg.bp)


def ativos() -> set:
    """Chaves dos modulos ativos (cache por pedido em flask.g)."""
    from flask import g, has_app_context
    if has_app_context() and "_modulos_ativos" in g:
        return g._modulos_ativos
    from . import store
    try:
        s = store.get_settings()
    except Exception:  # noqa: BLE001 - BD do portal indisponivel: assume os valores por omissao
        s = {}
    res = {k for k, m in REGISTO.items()
           if s.get(f"module.{k}", "1" if m.ativo_por_omissao else "0") == "1"}
    if has_app_context():
        g._modulos_ativos = res
    return res


def visiveis(user) -> list:
    """Modulos ativos a que o utilizador tem acesso, pela ordem de INSTALADOS."""
    if not user:
        return []
    return [REGISTO[k] for k in INSTALADOS if k in REGISTO and user.can(REGISTO[k].acesso)]
