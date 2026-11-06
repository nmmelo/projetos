"""Portal de gestao Oracle (shrink de tablespaces), com utilizadores e perfis."""
import os
import secrets

from flask import Flask

from . import crypto, settings, store


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

    store.get()                      # cria/atualiza o esquema
    if mark_interrupted:
        store.interrupt_running_jobs()

    from .views import bp
    app.register_blueprint(bp)
    return app
