"""Codigos de uso unico por tempo (TOTP, RFC 6238): Autenticacao de Dois Fatores.

Compativel com Microsoft Authenticator, Google Authenticator, FreeOTP, Aegis, 1Password, ...
(SHA-1, 6 digitos, 30 segundos). Sem bibliotecas externas.
"""
import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from urllib.parse import quote

PASSO = 30
DIGITOS = 6


def novo_segredo() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def _chave(segredo: str) -> bytes:
    s = segredo.replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def codigo(segredo: str, contador: int) -> str:
    h = hmac.new(_chave(segredo), struct.pack(">Q", contador), hashlib.sha1).digest()
    o = h[-1] & 15
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** DIGITOS).zfill(DIGITOS)


def contador_atual(t: float | None = None) -> int:
    return int((time.time() if t is None else t) // PASSO)


def verificar(segredo: str, cod: str, ultimo: int = -1, janela: int = 1) -> int | None:
    """Contador do codigo valido (aceita +-janela passos, para relogios ligeiramente desacertados), ou None.
    Um codigo ja usado (contador <= ultimo) nao volta a ser aceite."""
    cod = "".join(c for c in str(cod or "") if c.isdigit())
    if len(cod) != DIGITOS or not segredo:
        return None
    agora = contador_atual()
    for c in range(agora - janela, agora + janela + 1):
        if c > ultimo and hmac.compare_digest(codigo(segredo, c), cod):
            return c
    return None


def uri(segredo: str, conta: str, emissor: str) -> str:
    """otpauth://totp/... (o conteudo do codigo QR)."""
    rot = quote(f"{emissor}:{conta}", safe="")
    return f"otpauth://totp/{rot}?secret={segredo}&issuer={quote(emissor, safe='')}&digits={DIGITOS}&period={PASSO}"


def formatar(segredo: str) -> str:
    """Segredo em grupos de 4 (para escrever a mao na aplicacao)."""
    return " ".join(segredo[i:i + 4] for i in range(0, len(segredo), 4))


def codigos_recuperacao(n: int = 8) -> list[str]:
    """Codigos de recuperacao (uso unico), ex. 'k7m2-q9xw'."""
    alfa = "abcdefghjkmnpqrstuvwxyz23456789"
    return ["".join(secrets.choice(alfa) for _ in range(4)) + "-" + "".join(secrets.choice(alfa) for _ in range(4))
            for _ in range(n)]


def hash_recuperacao(c: str) -> str:
    return hashlib.sha256(c.strip().lower().replace(" ", "").encode()).hexdigest()
