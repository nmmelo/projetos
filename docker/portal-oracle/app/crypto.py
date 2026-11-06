"""Cifra das passwords das bases de dados (Fernet = AES-128-CBC + HMAC-SHA256)."""
import os

from cryptography.fernet import Fernet, InvalidToken

_fernet = None
_key = b""


def init(data_dir: str) -> None:
    """Carrega a chave de PORTAL_FERNET_KEY ou de DATA_DIR/fernet.key (criada na 1.a vez)."""
    global _fernet, _key
    key = os.environ.get("PORTAL_FERNET_KEY")
    if not key:
        path = os.path.join(data_dir, "fernet.key")
        if not os.path.exists(path):
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(Fernet.generate_key())
            except FileExistsError:
                pass
        with open(path, "rb") as fh:
            key = fh.read().strip()
    _key = key.encode() if isinstance(key, str) else key
    _fernet = Fernet(_key)


def key_bytes() -> bytes:
    return _key


def encrypt(plain: str) -> str:
    return _fernet.encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "Nao foi possivel decifrar a password: a chave (fernet.key / PORTAL_FERNET_KEY) mudou."
        ) from exc
