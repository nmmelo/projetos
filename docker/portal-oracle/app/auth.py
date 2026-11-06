"""Autenticacao, utilizador atual e permissoes.

As ACOES sao o catalogo de permissoes atribuiveis a um perfil. Para acrescentar
funcionalidades no futuro basta registar aqui a nova acao e usar
@requires("nova.acao") na rota.
"""
import functools
import time
from dataclasses import dataclass, field

from flask import abort, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import settings, store

# codigo -> (grupo, descricao)
ACTIONS = {
    "tablespaces.ver":  ("Operações", "Ver tablespaces (tamanho, usado, livre)"),
    "shrink.analise":   ("Operações", "Shrink — modo ANÁLISE"),
    "shrink.executar":  ("Operações", "Shrink — modo EXECUTAR (e cancelar execuções)"),
    "config.bds":       ("Configuração", "Gerir bases de dados Oracle"),
    "config.portal":    ("Configuração", "Gerir portal: utilizadores, perfis e backups"),
}

MIN_PASSWORD = 8


@dataclass
class CurrentUser:
    id: object                    # "admin" ou id numerico
    username: str
    name: str = ""
    email: str = ""
    is_admin: bool = False
    profile_name: str = ""
    perms: set = field(default_factory=set)
    all_dbs: bool = False
    db_ids: set = field(default_factory=set)
    last_login: str = ""

    def can(self, perm: str) -> bool:
        return self.is_admin or perm in self.perms

    def can_db(self, db_id) -> bool:
        return self.is_admin or self.all_dbs or (db_id is not None and int(db_id) in self.db_ids)

    def visible_dbs(self, dbs: list[dict]) -> list[dict]:
        return [d for d in dbs if self.can_db(d["id"])]

    @property
    def can_config(self) -> bool:
        return self.can("config.bds") or self.can("config.portal")


def check_password_strength(pwd: str) -> None:
    if len(pwd or "") < MIN_PASSWORD:
        raise ValueError(f"A password tem de ter pelo menos {MIN_PASSWORD} caracteres.")


def hash_password(pwd: str) -> str:
    return generate_password_hash(pwd)


# ------------------------------------------------------------------ login
def authenticate(username: str, password: str) -> CurrentUser | None:
    username = (username or "").strip()
    adm = settings.admin()
    if adm and username == adm["username"]:
        return _admin_user(adm) if check_password_hash(adm["password_hash"], password) else None
    u = store.get_user(username=username.lower())
    if u and u["active"] and check_password_hash(u["password_hash"], password):
        store.update_user_fields(u["id"], last_login=store.now())
        return _db_user(u)
    return None


def _admin_user(adm: dict) -> CurrentUser:
    return CurrentUser(id="admin", username=adm["username"], name=adm.get("name", ""),
                       email=adm.get("email", ""), is_admin=True, profile_name="Administrador (local)",
                       perms=set(ACTIONS), all_dbs=True)


def _db_user(u: dict) -> CurrentUser:
    prof = store.get_profile(u["profile_id"]) if u.get("profile_id") else None
    return CurrentUser(id=u["id"], username=u["username"], name=u.get("name") or "", email=u.get("email") or "",
                       profile_name=prof["name"] if prof else "(sem perfil)",
                       perms=set(prof["permissions"]) if prof else set(),
                       all_dbs=bool(prof and prof["all_dbs"]),
                       db_ids=set(prof["db_ids"]) if prof else set(),
                       last_login=u.get("last_login") or "")


def login_session(user: CurrentUser) -> None:
    session.clear()
    session["uid"] = user.id
    session["ts"] = time.time()


def load_current_user() -> CurrentUser | None:
    """Recarregado a cada pedido: alteracoes de perfil/estado aplicam-se de imediato."""
    uid = session.get("uid")
    if uid is None:
        return None
    try:
        minutes = int(store.get_settings().get("session_minutes", 480))
    except ValueError:
        minutes = 480
    if time.time() - session.get("ts", 0) > minutes * 60:
        session.clear()
        return None
    session["ts"] = time.time()
    if uid == "admin":
        adm = settings.admin()
        return _admin_user(adm) if adm else None
    u = store.get_user(uid=uid)
    if not u or not u["active"]:
        session.clear()
        return None
    return _db_user(u)


# ------------------------------------------------------------------ decoradores
def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not g.get("user"):
            return redirect(url_for("portal.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def requires(*perms):
    """Exige TODAS as permissoes indicadas."""
    def deco(view):
        @functools.wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not all(g.user.can(p) for p in perms):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return deco


def admin_required(view):
    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not g.user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped
