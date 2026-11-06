"""Ficheiros locais de configuracao (em DATA_DIR):

- portal.json : onde ficam guardados os dados do portal (SQLite, MariaDB ou PostgreSQL)
- admin.json  : o administrador, unico utilizador local (nao fica na base de dados)
"""
import json
import os
import tempfile

DATA_DIR = os.path.abspath(os.environ.get(
    "PORTAL_DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data")))

DEFAULT_STORAGE = {"type": "sqlite", "path": "portal.sqlite3"}
STORAGE_TYPES = {"sqlite": "SQLite (ficheiro local)", "mariadb": "MariaDB / MySQL", "postgresql": "PostgreSQL"}
DEFAULT_PORTS = {"mariadb": 3306, "postgresql": 5432}


def path(name: str) -> str:
    return os.path.join(DATA_DIR, name)


def _read(name: str, default):
    try:
        with open(path(name)) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def _write(name: str, data) -> None:
    """Escrita atomica com permissoes 600 (contem hashes / passwords cifradas)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, prefix=f".{name}.")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path(name))


# ------------------------------------------------------------------ armazenamento
def storage_config() -> dict:
    return _read("portal.json", {}).get("storage", dict(DEFAULT_STORAGE))


def save_storage_config(cfg: dict) -> None:
    data = _read("portal.json", {})
    data["storage"] = cfg
    _write("portal.json", data)


def storage_mtime() -> float:
    try:
        return os.stat(path("portal.json")).st_mtime
    except FileNotFoundError:
        return 0.0


# ------------------------------------------------------------------ administrador local
def _normalize_hash(h: str) -> str:
    # o docker compose v2 exige "$$" no .env; o v1 nao. Aceita os dois.
    return h.replace("$$", "$") if h else h


def admin() -> dict | None:
    data = _read("admin.json", None)
    if data:
        return data
    env_hash = _normalize_hash(os.environ.get("PORTAL_ADMIN_PASSWORD_HASH", ""))
    if env_hash:  # arranque: cria o admin.json a partir do .env
        data = {"username": os.environ.get("PORTAL_ADMIN_USER", "admin"),
                "password_hash": env_hash, "name": "Administrador", "email": ""}
        _write("admin.json", data)
        return data
    return None


def save_admin(data: dict) -> None:
    _write("admin.json", data)
