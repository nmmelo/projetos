"""Wheels do python-oracledb (e dependencias) guardados no portal, para o setup.sh do agente instalar o servico
Oracle em clientes SEM acesso a internet: o setup.sh pede a lista ao portal, descarrega os wheels compativeis
com o Python e a arquitetura do cliente e instala com pip --no-index.

Os wheels ficam em DATA_DIR/wheels e chegam de duas formas:
  - obter(): pip download no portal (precisa de internet no portal) para varias versoes do Python (3.8-3.13)
    e arquiteturas (x86_64, aarch64), com glibc >= 2.17 (RHEL/OL 7+, Debian 9+, Ubuntu 18.04+);
  - carregados a mao em Clientes > Ferramentas (descarregados noutra maquina).

Linha de comando (usado pelo instalar.sh, que ignora a falha se o portal nao tiver internet):
    python -m app.wheels obter [--silencioso]
"""
import os
import re
import subprocess
import sys
import threading

from . import settings

PASTA = os.path.join(settings.DATA_DIR, "wheels")
LOG = os.path.join(PASTA, ".obter.log")
PYTHONS = ("3.8", "3.9", "3.10", "3.11", "3.12", "3.13")
ARQS = ("x86_64", "aarch64")
# do mais antigo ao mais recente aceite: o pip escolhe o melhor wheel compativel com estas plataformas
GLIBC = ("manylinux2014", "manylinux_2_17", "manylinux_2_24", "manylinux_2_28")
NOME_RE = re.compile(r"^[A-Za-z0-9_.+-]+\.whl$")

_lock = threading.Lock()
_estado = {"a_correr": False}


def listar() -> list[dict]:
    if not os.path.isdir(PASTA):
        return []
    out = []
    for n in sorted(os.listdir(PASTA)):
        if NOME_RE.match(n):
            p = os.path.join(PASTA, n)
            out.append({"nome": n, "bytes": os.path.getsize(p), "pacote": n.split("-")[0]})
    return out


def caminho(nome: str) -> str | None:
    if not NOME_RE.match(nome or ""):
        return None
    p = os.path.join(PASTA, nome)
    return p if os.path.isfile(p) else None


def guardar(nome: str, dados: bytes) -> str:
    nome = os.path.basename(nome or "")
    if not NOME_RE.match(nome):
        raise ValueError("só ficheiros .whl")
    if dados[:2] != b"PK":
        raise ValueError(f"{nome} não é um wheel (zip) válido")
    os.makedirs(PASTA, exist_ok=True)
    with open(os.path.join(PASTA, nome), "wb") as fh:
        fh.write(dados)
    return nome


def apagar(nome: str) -> bool:
    p = caminho(nome)
    if p:
        os.remove(p)
        return True
    return False


def log() -> str:
    try:
        with open(LOG, encoding="utf-8", errors="replace") as fh:
            return fh.read()[-20000:]
    except OSError:
        return ""


def a_correr() -> bool:
    return _estado["a_correr"]


def obter(escrever=None, timeout: int = 60) -> bool:
    """pip download do oracledb para cada Python/arquitetura. Devolve True se obteve pelo menos um conjunto."""
    os.makedirs(PASTA, exist_ok=True)
    ok_algum = False
    with open(LOG, "w", encoding="utf-8") as lf:
        def p(m):
            lf.write(m + "\n")
            lf.flush()
            if escrever:
                escrever(m)
        for arq in ARQS:
            for py in PYTHONS:
                cmd = [sys.executable, "-m", "pip", "download", "oracledb", "--only-binary=:all:", "--dest", PASTA,
                       "--python-version", py, "--implementation", "cp", "--disable-pip-version-check",
                       "--retries", "1", "--timeout", "15", "-q"]
                for g in GLIBC:
                    cmd += ["--platform", f"{g}_{arq}"]
                p(f"$ pip download oracledb (Python {py}, {arq})")
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout * 3)
                except subprocess.TimeoutExpired:
                    p("  tempo esgotado")
                    continue
                if r.returncode == 0:
                    ok_algum = True
                    p("  ok")
                else:
                    err = (r.stderr or r.stdout).strip().splitlines()
                    p("  falhou: " + (err[-1] if err else f"código {r.returncode}"))
                    if any("Network is unreachable" in e or "Failed to establish" in e or "Name or service" in e
                           or "Temporary failure in name" in e for e in err):
                        p("sem acesso à internet a partir do portal: carregue os wheels à mão (Clientes > Ferramentas)")
                        return ok_algum
        n = len(listar())
        p(f"fim: {n} wheel(s) em {PASTA}")
    return ok_algum


def obter_em_fundo() -> bool:
    """Lanca obter() numa thread (Clientes > Ferramentas). False se ja estiver a correr."""
    with _lock:
        if _estado["a_correr"]:
            return False
        _estado["a_correr"] = True

    def corre():
        try:
            obter()
        except Exception as exc:  # noqa: BLE001
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write(f"erro: {exc}\n")
        finally:
            _estado["a_correr"] = False
    threading.Thread(target=corre, daemon=True, name="wheels-oracledb").start()
    return True


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "obter":
        silencioso = "--silencioso" in sys.argv
        ok = obter(None if silencioso else print, timeout=30)
        print(f"wheels do oracledb para os agentes: {len(listar())} ficheiro(s)" if ok
              else "wheels do oracledb: sem acesso a internet (carregue-os em Clientes > Ferramentas)")
        sys.exit(0)
    print(__doc__)
