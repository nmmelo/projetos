"""Autenticacao, utilizador atual e permissoes.

O catalogo de permissoes vem de app/modulos.py: as do portal (ACOES_PORTAL) e, por modulo, o acesso
"modulo.<chave>" e as acoes "<chave>.<acao>". Uma acao de modulo so vale se o modulo estiver ativo
e o perfil tiver acesso a esse modulo. Nas rotas: @requires("codigo").
"""
import functools
import time
from dataclasses import dataclass, field

from flask import abort, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import modulos, settings, store

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
    auth_tipo: str = "local"

    def can(self, perm: str) -> bool:
        mod = modulos.modulo_de(perm)
        if mod:
            if mod not in modulos.ativos():
                return False
            if self.is_admin:
                return True
            return f"modulo.{mod}" in self.perms and perm in self.perms
        return self.is_admin or perm in self.perms

    def can_db(self, db_id) -> bool:
        return self.is_admin or self.all_dbs or (db_id is not None and int(db_id) in self.db_ids)

    def visible_dbs(self, dbs: list[dict]) -> list[dict]:
        return [d for d in dbs if self.can_db(d["id"])]

    @property
    def can_config(self) -> bool:
        return self.can("config.portal")

    @property
    def modulos(self) -> list:
        return modulos.visiveis(self)


def check_password_strength(pwd: str) -> None:
    if len(pwd or "") < MIN_PASSWORD:
        raise ValueError(f"A password tem de ter pelo menos {MIN_PASSWORD} caracteres.")


def hash_password(pwd: str) -> str:
    return generate_password_hash(pwd)


# ------------------------------------------------------------------ login
def authenticate(username: str, password: str, ip: str = "") -> CurrentUser | None:
    """Administrador: sempre local. Utilizadores: local (password no portal) ou LDAP (modulo
    Autenticacao ativo e LDAP ligado), conforme o tipo de autenticacao de cada um."""
    from . import ldap_auth
    username = (username or "").strip()
    adm = settings.admin()
    if adm and username == adm["username"]:
        ok = check_password_hash(adm["password_hash"], password)
        ldap_auth.registar_acesso(username, "local", ok, "administrador", ip)
        return _admin_user(adm) if ok else None
    nome = username.lower()
    u = store.get_user(username=nome)
    ldap_on = ldap_ativo()
    if u and (u.get("auth_tipo") or "local") == "ldap":
        if not u["active"]:
            ldap_auth.registar_acesso(nome, "ldap", False, "utilizador inativo", ip)
            return None
        if not ldap_on:
            ldap_auth.registar_acesso(nome, "ldap", False, "LDAP desligado (módulo Autenticação)", ip)
            return None
        ok, info = ldap_auth.autenticar(nome, password)
        ldap_auth.registar_acesso(nome, "ldap", ok, "" if ok else str(info), ip)
        if not ok:
            return None
        campos = {"last_login": store.now()}
        if ldap_auth.config()["atualizar_dados"]:
            campos.update({k: v for k, v in (("name", info["nome"]), ("email", info["email"])) if v})
        store.update_user_fields(u["id"], **campos)
        return _db_user(store.get_user(uid=u["id"]))
    if u:
        ok = bool(u["active"]) and check_password_hash(u["password_hash"], password)
        ldap_auth.registar_acesso(nome, "local", ok, "" if ok else ("utilizador inativo" if not u["active"] else "password errada"), ip)
        if ok:
            store.update_user_fields(u["id"], last_login=store.now())
            return _db_user(u)
        return None
    # nao existe no portal: criar a partir do LDAP, se configurado
    if ldap_on and ldap_auth.config()["auto_criar"] and settings_username_ok(nome):
        ok, info = ldap_auth.autenticar(nome, password)
        ldap_auth.registar_acesso(nome, "ldap", ok, "criado no primeiro login" if ok else str(info), ip)
        if ok:
            perfil = ldap_auth.config()["perfil"]
            uid = store.save_user({"username": nome, "name": info["nome"], "email": info["email"], "active": True,
                                   "profile_id": int(perfil) if str(perfil).isdigit() else None,
                                   "auth_tipo": "ldap"}, None)
            store.update_user_fields(uid, last_login=store.now())
            return _db_user(store.get_user(uid=uid))
        return None
    ldap_auth.registar_acesso(nome, "local", False, "utilizador desconhecido", ip)
    return None


def settings_username_ok(nome: str) -> bool:
    import re
    return bool(re.match(r"^[a-z0-9][a-z0-9._-]{2,63}$", nome))


def ldap_ativo() -> bool:
    """LDAP em uso: modulo Autenticacao ativo e LDAP ligado na configuracao."""
    return "autenticacao" in modulos.ativos() and store.get_setting("ldap_ativo") == "1"


def _admin_user(adm: dict) -> CurrentUser:
    return CurrentUser(id="admin", username=adm["username"], name=adm.get("name", ""),
                       email=adm.get("email", ""), is_admin=True, profile_name="Administrador (local)",
                       perms=set(modulos.todas_acoes()), all_dbs=True)


def _db_user(u: dict) -> CurrentUser:
    prof = store.get_profile(u["profile_id"]) if u.get("profile_id") else None
    return CurrentUser(id=u["id"], username=u["username"], name=u.get("name") or "", email=u.get("email") or "",
                       profile_name=prof["name"] if prof else "(sem perfil)",
                       perms=set(prof["permissions"]) if prof else set(),
                       all_dbs=bool(prof and prof["all_dbs"]),
                       db_ids=set(prof["db_ids"]) if prof else set(),
                       last_login=u.get("last_login") or "", auth_tipo=u.get("auth_tipo") or "local")


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
