"""Testes do remover_ip.py (executados pelo pytest no workflow do GitHub)."""
import importlib.util
import os
import subprocess
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(AQUI, "remover_ip.py")

spec = importlib.util.spec_from_file_location("remover_ip", SCRIPT)
remover_ip = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remover_ip)

CONTEUDO = (
    "---\n"
    'a: "10.31.240.4"\n'
    'b: "10.31.240.40"\n'
    'c: ["1.1.1.1", "10.31.240.4"]\n'
    "d: 110.31.240.4\n"
    "e: http://10.31.240.4:8080/api\n"
)


def correr(*args, entrada=None):
    return subprocess.run([sys.executable, SCRIPT, *args], input=entrada,
                          capture_output=True, text=True)


def test_validar_ip_aceita_aspas():
    assert remover_ip.validar_ip('"10.31.240.4"') == "10.31.240.4"
    assert remover_ip.validar_ip("'10.31.240.4'") == "10.31.240.4"
    assert remover_ip.validar_ip("“10.31.240.4”") == "10.31.240.4"
    assert remover_ip.validar_ip("abc") is None


def test_regex_so_ip_exato():
    p = remover_ip.regex_ip("10.31.240.4")
    assert p.search('a: "10.31.240.4"')
    assert p.search("url: http://10.31.240.4:8080")
    assert not p.search('b: "10.31.240.40"')
    assert not p.search("d: 110.31.240.4")


def test_remove_linhas_e_faz_backup(tmp_path):
    f = tmp_path / "main.yml"
    f.write_text(CONTEUDO, encoding="utf-8")
    r = correr("-f", str(f), "-i", "10.31.240.4", "-y")
    assert r.returncode == 0, r.stderr
    assert f.read_text(encoding="utf-8") == '---\nb: "10.31.240.40"\nd: 110.31.240.4\n'
    backups = [x for x in os.listdir(tmp_path) if x.endswith(".bak")]
    assert len(backups) == 1
    assert (tmp_path / backups[0]).read_text(encoding="utf-8") == CONTEUDO


def test_dry_run_nao_altera(tmp_path):
    f = tmp_path / "main.yml"
    f.write_text(CONTEUDO, encoding="utf-8")
    r = correr("-f", str(f), "-i", "10.31.240.4", "--dry-run")
    assert r.returncode == 0
    assert "Seriam removidas 3 linha(s)" in r.stdout
    assert f.read_text(encoding="utf-8") == CONTEUDO


def test_mantem_so_o_ultimo_backup(tmp_path):
    f = tmp_path / "main.yml"
    f.write_text(CONTEUDO, encoding="utf-8")
    (tmp_path / "main.yml.20250101_120000.bak").write_text("antigo", encoding="utf-8")
    correr("-f", str(f), "-i", "10.31.240.4", "-y")
    backups = [x for x in os.listdir(tmp_path) if x.endswith(".bak")]
    assert len(backups) == 1 and backups[0] != "main.yml.20250101_120000.bak"


def test_interativo_com_confirmacao(tmp_path):
    f = tmp_path / "main.yml"
    f.write_text(CONTEUDO, encoding="utf-8")
    r = correr(entrada=f"{f}\n\"10.31.240.4\"\ns\n")
    assert r.returncode == 0, r.stderr
    assert f.read_text(encoding="utf-8") == '---\nb: "10.31.240.40"\nd: 110.31.240.4\n'


def test_cancelar_nao_altera(tmp_path):
    f = tmp_path / "main.yml"
    f.write_text(CONTEUDO, encoding="utf-8")
    r = correr("-f", str(f), "-i", "10.31.240.4", entrada="n\n")
    assert r.returncode == 1
    assert f.read_text(encoding="utf-8") == CONTEUDO


def test_ip_inexistente(tmp_path):
    f = tmp_path / "main.yml"
    f.write_text(CONTEUDO, encoding="utf-8")
    r = correr("-f", str(f), "-i", "192.168.1.1", "-y")
    assert r.returncode == 0
    assert "Nada a fazer" in r.stdout
    assert f.read_text(encoding="utf-8") == CONTEUDO


def test_ficheiro_ou_ip_invalidos():
    assert correr("-f", "/nao/existe.yml", "-i", "10.31.240.4").returncode == 1
    assert correr("-f", SCRIPT, "-i", "abc").returncode == 1
