"""Portal com modulos (Oracle: tablespaces e shrink), utilizadores, perfis e modo de manutencao."""
import os
import secrets

from flask import Flask

from . import crypto, modulos, settings, store


def _persistent_secret() -> str:
    """Chave das sessoes Flask: variavel de ambiente ou ficheiro (partilhada entre workers)."""
    env = os.environ.get("PORTAL_SECRET_KEY")
    if env:
        return env
    path = settings.path("flask_secret.key")
    if not os.path.exists(path):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(secrets.token_hex(32))
        except FileExistsError:
            pass
    with open(path) as fh:
        return fh.read().strip()


def create_app(mark_interrupted: bool = True) -> Flask:
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    os.makedirs(settings.path("backups"), exist_ok=True)
    crypto.init(settings.DATA_DIR)

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=_persistent_secret(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("PORTAL_COOKIE_SECURE", "0") == "1",
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,   # upload de backups
    )

    import datetime as _dt
    import re as _re

    def _ts_epoch(v):
        try:
            return _dt.datetime.fromtimestamp(int(v)).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError, OSError):
            return ""
    app.jinja_env.filters["ts_epoch"] = _ts_epoch
    app.jinja_env.filters["ts_iso"] = _ts_epoch
    app.jinja_env.tests["matching"] = lambda v, padrao: bool(_re.match(padrao, str(v or "")))

    store.get()                      # cria/atualiza o esquema
    if mark_interrupted:
        store.interrupt_running_jobs()

    from .views import bp
    app.register_blueprint(bp)
    modulos.carregar(app)
    from . import eventos
    eventos.regras_iniciais()
    return app
