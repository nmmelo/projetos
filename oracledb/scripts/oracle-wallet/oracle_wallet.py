#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# oracle_wallet.py - Validação e gestão de Oracle Wallets
#
# Copyright 2026 Nuno Melo
# Licenciado sob a Apache License, Version 2.0 (ver LICENSE e NOTICE na raiz do repositório)
#
# Leitura (sem credenciais, a partir do cwallet.sso):
#   discover      procura wallets no servidor (sqlnet.ora, oratab, WALLET_ROOT, v$encryption_wallet)
#   show          certificados (validade) e credenciais guardadas num wallet
#   cert-info     analisa um certificado em ficheiro ou texto (.crt / PEM)
#
# Escrita (pede a password do wallet; usa orapki e mkstore do ORACLE_HOME):
#   create        cria um wallet novo
#   cert-add      importa um certificado (ficheiro ou texto) como trusted ou user
#   cert-remove   remove um certificado
#   cert-export   exporta um certificado para ficheiro
#   cert-selfsigned  cria um certificado autoassinado
#   cert-request  cria um pedido de certificado (CSR)
#   cred-add / cred-modify / cred-remove   credenciais (Secure External Password Store)
#
# Compatível com Python 3.6+ (Oracle Linux 7/8/9) e sem dependências obrigatórias.

import argparse
import datetime
import getpass
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

VERSAO = "1.0.0"
DIAS_AVISO = 30

# Códigos de saída (úteis para monitorização / cron)
RC_OK, RC_AVISO, RC_EXPIRADO, RC_ERRO = 0, 1, 2, 3

CLIENT_PREFIX = "oracle.security.client."

# --------------------------------------------------------------------------------------
# Utilitários de apresentação
# --------------------------------------------------------------------------------------

USE_COR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def cor(txt, c):
    if not USE_COR:
        return txt
    codes = {"verde": "32", "amarelo": "33", "vermelho": "31", "azul": "36", "negrito": "1"}
    return "\033[%sm%s\033[0m" % (codes.get(c, "0"), txt)


def info(msg):
    print(cor("[INFO] ", "azul") + msg, file=sys.stderr)


def aviso(msg):
    print(cor("[AVISO] ", "amarelo") + msg, file=sys.stderr)


def erro(msg):
    print(cor("[ERRO] ", "vermelho") + msg, file=sys.stderr)


def _vis_len(s):
    return len(re.sub(r"\033\[[0-9;]*m", "", s))


def tabela(cabecalho, linhas, larg_max=60):
    """Imprime uma tabela em texto simples."""
    if not linhas:
        print("  (nenhum)")
        return

    def corta(v):
        v = "" if v is None else str(v)
        if _vis_len(v) > larg_max and "\033" not in v:
            return v[: larg_max - 3] + "..."
        return v

    linhas = [[corta(c) for c in l] for l in linhas]
    larg = [len(h) for h in cabecalho]
    for l in linhas:
        for i, c in enumerate(l):
            larg[i] = max(larg[i], _vis_len(c))
    sep = "+" + "+".join("-" * (w + 2) for w in larg) + "+"
    print(sep)
    print("| " + " | ".join(h.ljust(larg[i]) for i, h in enumerate(cabecalho)) + " |")
    print(sep)
    for l in linhas:
        print("| " + " | ".join(c + " " * (larg[i] - _vis_len(c)) for i, c in enumerate(l)) + " |")
    print(sep)


def run(cmd, stdin_txt=None, env=None, mascarar=None):
    """Executa um comando e devolve (rc, stdout+stderr). Não mostra passwords."""
    visivel = " ".join(cmd)
    for m in mascarar or []:
        if m:
            visivel = visivel.replace(m, "********")
    info("A executar: " + visivel)
    p = subprocess.run(
        cmd,
        input=stdin_txt if stdin_txt is not None else "",  # nunca fica à espera do terminal
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        env=env,
    )
    return p.returncode, p.stdout


# --------------------------------------------------------------------------------------
# ORACLE_HOME e ferramentas
# --------------------------------------------------------------------------------------


def oracle_homes():
    homes = []
    oh = os.environ.get("ORACLE_HOME")
    if oh:
        homes.append(oh)
    for oratab in ("/etc/oratab", "/var/opt/oracle/oratab"):
        if os.path.isfile(oratab):
            with open(oratab) as f:
                for l in f:
                    l = l.strip()
                    if not l or l.startswith("#") or ":" not in l:
                        continue
                    partes = l.split(":")
                    if len(partes) >= 2 and partes[1] and partes[1] not in homes:
                        homes.append(partes[1])
    return [h for h in homes if os.path.isdir(h)]


def oracle_home(args):
    oh = getattr(args, "oracle_home", None) or os.environ.get("ORACLE_HOME")
    if oh:
        return oh
    hs = oracle_homes()
    return hs[0] if hs else None


def ferramenta(nome, args):
    oh = oracle_home(args)
    if oh:
        p = os.path.join(oh, "bin", nome)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return shutil.which(nome)


def env_oracle(args):
    env = dict(os.environ)
    oh = oracle_home(args)
    if oh:
        env["ORACLE_HOME"] = oh
        env["PATH"] = os.path.join(oh, "bin") + os.pathsep + env.get("PATH", "")
    return env


# --------------------------------------------------------------------------------------
# Análise de certificados (Python: cryptography -> openssl)
# --------------------------------------------------------------------------------------

PEM_RE = re.compile(r"-----BEGIN (?:TRUSTED )?CERTIFICATE-----.+?-----END (?:TRUSTED )?CERTIFICATE-----", re.S)


def extrair_pems(texto):
    return [m.group(0) for m in PEM_RE.finditer(texto)]


def _data_openssl(s):
    # ex.: "Jan  1 00:00:00 2030 GMT"
    s = " ".join(s.split())
    return datetime.datetime.strptime(s, "%b %d %H:%M:%S %Y %Z")


def analisar_pem(pem):
    """Devolve dict com subject, issuer, not_before, not_after (datetime UTC), serial, sha256."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes

        c = x509.load_pem_x509_certificate(pem.encode())
        nb = getattr(c, "not_valid_before_utc", None) or c.not_valid_before
        na = getattr(c, "not_valid_after_utc", None) or c.not_valid_after
        return {
            "subject": c.subject.rfc4514_string(),
            "issuer": c.issuer.rfc4514_string(),
            "not_before": nb.replace(tzinfo=None),
            "not_after": na.replace(tzinfo=None),
            "serial": format(c.serial_number, "X"),
            "sha256": c.fingerprint(hashes.SHA256()).hex().upper(),
        }
    except ImportError:
        pass
    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("Sem o módulo 'cryptography' e sem o comando 'openssl' para analisar certificados")
    p = subprocess.run(
        [openssl, "x509", "-noout", "-subject", "-issuer", "-startdate", "-enddate", "-serial",
         "-fingerprint", "-sha256", "-nameopt", "RFC2253"],
        input=pem, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
    )
    if p.returncode != 0:
        raise RuntimeError("openssl não conseguiu ler o certificado: " + p.stderr.strip())
    d = {}
    for l in p.stdout.splitlines():
        if "=" not in l:
            continue
        k, v = l.split("=", 1)
        k = k.strip().lower()
        if k == "subject":
            d["subject"] = v.strip()
        elif k == "issuer":
            d["issuer"] = v.strip()
        elif k == "notbefore":
            d["not_before"] = _data_openssl(v)
        elif k == "notafter":
            d["not_after"] = _data_openssl(v)
        elif k == "serial":
            d["serial"] = v.strip()
        elif "fingerprint" in k:
            d["sha256"] = v.strip().replace(":", "")
    return d


def ler_certificado_entrada(args):
    """Lê o certificado de --ficheiro, --texto ou --stdin e devolve lista de PEMs."""
    if getattr(args, "ficheiro", None):
        with open(args.ficheiro, "rb") as f:
            dados = f.read()
        try:
            txt = dados.decode("utf-8")
        except UnicodeDecodeError:
            txt = ""
        pems = extrair_pems(txt)
        if pems:
            return pems
        # DER -> PEM
        openssl = shutil.which("openssl")
        if openssl:
            p = subprocess.run([openssl, "x509", "-inform", "DER", "-outform", "PEM"], input=dados,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if p.returncode == 0:
                return extrair_pems(p.stdout.decode())
        raise RuntimeError("O ficheiro não contém um certificado PEM/DER válido: " + args.ficheiro)
    if getattr(args, "texto", None):
        txt = args.texto.replace("\\n", "\n")
    else:
        print("Cole o certificado (-----BEGIN CERTIFICATE----- ... -----END CERTIFICATE-----) e termine com Ctrl-D:",
              file=sys.stderr)
        txt = sys.stdin.read()
    pems = extrair_pems(txt)
    if not pems:
        # texto base64 sem cabeçalhos
        b64 = re.sub(r"\s+", "", txt)
        if re.fullmatch(r"[A-Za-z0-9+/=]{100,}", b64 or ""):
            linhas = [b64[i:i + 64] for i in range(0, len(b64), 64)]
            pems = ["-----BEGIN CERTIFICATE-----\n" + "\n".join(linhas) + "\n-----END CERTIFICATE-----"]
    if not pems:
        raise RuntimeError("Não foi encontrado nenhum certificado no texto")
    return pems


def estado_validade(not_after, not_before=None, dias_aviso=DIAS_AVISO):
    agora = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    if not_after is None:
        return None, "DESCONHECIDO"
    dias = (not_after - agora).days
    if not_before and not_before > agora:
        return dias, "AINDA_NAO_VALIDO"
    if not_after < agora:
        return dias, "EXPIRADO"
    if dias <= dias_aviso:
        return dias, "A_EXPIRAR"
    return dias, "OK"


def pinta_estado(e):
    return cor(e, {"OK": "verde", "A_EXPIRAR": "amarelo", "EXPIRADO": "vermelho",
                   "AINDA_NAO_VALIDO": "amarelo"}.get(e, "negrito"))


# --------------------------------------------------------------------------------------
# Leitor Java (oraclepki.jar) - lê cwallet.sso sem password
# --------------------------------------------------------------------------------------

JAVA_SRC = r"""
import java.io.*;
import java.security.*;
import java.security.cert.X509Certificate;
import java.text.SimpleDateFormat;
import java.util.*;
import oracle.security.pki.OraclePKIProvider;
import oracle.security.pki.OracleWallet;
import oracle.security.pki.OracleSecretStore;

public class OwReader {
    static String esc(String s) {
        if (s == null) return "null";
        StringBuilder b = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            switch (c) {
                case '"': b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                default:
                    if (c < 0x20) b.append(String.format("\\u%04x", (int) c)); else b.append(c);
            }
        }
        return b.append("\"").toString();
    }

    static String hex(byte[] d) {
        StringBuilder b = new StringBuilder();
        for (byte x : d) b.append(String.format("%02X", x));
        return b.toString();
    }

    public static void main(String[] a) {
        String dir = a[0];
        boolean secrets = a.length > 1 && a[1].contains("secrets");
        boolean show = a.length > 1 && a[1].contains("show");
        String envp = System.getenv("OW_WALLET_PWD");
        char[] pwd = (envp != null && envp.length() > 0) ? envp.toCharArray() : null;
        SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss");
        f.setTimeZone(TimeZone.getTimeZone("UTC"));
        Provider prov = new OraclePKIProvider();
        Security.addProvider(prov);
        StringBuilder o = new StringBuilder("{");

        // ---- certificados
        o.append("\"certs\":[");
        String certErr = null;
        try {
            KeyStore ks;
            File sso = new File(dir, "cwallet.sso");
            File p12 = new File(dir, "ewallet.p12");
            if (pwd == null && sso.exists()) {
                ks = KeyStore.getInstance("SSO", prov);
                try (InputStream in = new FileInputStream(sso)) { ks.load(in, null); }
            } else if (p12.exists() && pwd != null) {
                ks = KeyStore.getInstance("PKCS12", prov);
                try (InputStream in = new FileInputStream(p12)) { ks.load(in, pwd); }
            } else {
                throw new Exception("sem cwallet.sso (auto-login) e sem password para o ewallet.p12");
            }
            boolean first = true;
            for (Enumeration<String> e = ks.aliases(); e.hasMoreElements(); ) {
                String al = e.nextElement();
                String kind = ks.isKeyEntry(al) ? "user" : (ks.isCertificateEntry(al) ? "trusted" : "outro");
                java.security.cert.Certificate c = ks.getCertificate(al);
                if (!(c instanceof X509Certificate)) continue;
                X509Certificate x = (X509Certificate) c;
                MessageDigest md = MessageDigest.getInstance("SHA-256");
                if (!first) o.append(",");
                first = false;
                o.append("{\"alias\":").append(esc(al))
                 .append(",\"tipo\":").append(esc(kind))
                 .append(",\"subject\":").append(esc(x.getSubjectX500Principal().getName("RFC2253")))
                 .append(",\"issuer\":").append(esc(x.getIssuerX500Principal().getName("RFC2253")))
                 .append(",\"not_before\":").append(esc(f.format(x.getNotBefore())))
                 .append(",\"not_after\":").append(esc(f.format(x.getNotAfter())))
                 .append(",\"serial\":").append(esc(x.getSerialNumber().toString(16).toUpperCase()))
                 .append(",\"sig_alg\":").append(esc(x.getSigAlgName()))
                 .append(",\"sha256\":").append(esc(hex(md.digest(x.getEncoded()))))
                 .append("}");
            }
        } catch (Throwable t) {
            certErr = t.toString();
        }
        o.append("],\"certs_erro\":").append(esc(certErr));

        // ---- secret store
        o.append(",\"secrets\":[");
        String secErr = null;
        if (secrets) {
            try {
                OracleWallet w = new OracleWallet();
                w.open(dir, pwd);
                OracleSecretStore ss = w.getSecretStore();
                boolean first = true;
                for (Enumeration e = ss.internalAliases(); e.hasMoreElements(); ) {
                    String al = (String) e.nextElement();
                    char[] v = ss.getSecret(al);
                    String low = al.toLowerCase();
                    String val;
                    boolean client = low.startsWith("oracle.security.client.");
                    if (client && !low.startsWith("oracle.security.client.password")) val = new String(v);
                    else if (client && show) val = new String(v);
                    else val = null;
                    if (!first) o.append(",");
                    first = false;
                    o.append("{\"alias\":").append(esc(al))
                     .append(",\"valor\":").append(esc(val))
                     .append(",\"tamanho\":").append(v == null ? 0 : v.length)
                     .append("}");
                    if (v != null) Arrays.fill(v, ' ');
                }
            } catch (Throwable t) {
                secErr = t.toString();
            }
        }
        o.append("],\"secrets_erro\":").append(esc(secErr)).append("}");
        System.out.println(o);
    }
}
"""


def _jars(oh):
    nomes = ["oraclepki.jar", "osdt_cert.jar", "osdt_core.jar"]
    jars = []
    for n in nomes:
        cands = [os.path.join(oh, "jlib", n)] + glob.glob(os.path.join(oh, "**", n), recursive=True)
        for c in cands:
            if os.path.isfile(c):
                jars.append(c)
                break
    return jars


def _java_bins(oh):
    java = javac = None
    if oh:
        for d in (os.path.join(oh, "jdk", "bin"), os.path.join(oh, "jdk", "jre", "bin")):
            if not java and os.path.isfile(os.path.join(d, "java")):
                java = os.path.join(d, "java")
            if not javac and os.path.isfile(os.path.join(d, "javac")):
                javac = os.path.join(d, "javac")
    return java or shutil.which("java"), javac or shutil.which("javac")


def leitor_java(args):
    """Compila (uma vez, em cache) o leitor Java e devolve (java, classpath) ou (None, motivo)."""
    oh = oracle_home(args)
    if not oh:
        return None, "ORACLE_HOME não definido (use --oracle-home)"
    jars = _jars(oh)
    if not any(j.endswith("oraclepki.jar") for j in jars):
        return None, "oraclepki.jar não encontrado em %s/jlib" % oh
    java, javac = _java_bins(oh)
    if not java:
        return None, "java não encontrado (nem em $ORACLE_HOME/jdk)"
    h = hashlib.sha1((JAVA_SRC + "|".join(jars)).encode()).hexdigest()[:12]
    cache = os.path.join(os.path.expanduser("~"), ".cache", "oracle_wallet", h)
    classe = os.path.join(cache, "OwReader.class")
    if not os.path.isfile(classe):
        if not javac:
            return None, "javac não encontrado para compilar o leitor (precisa de um JDK, ex.: $ORACLE_HOME/jdk)"
        os.makedirs(cache, exist_ok=True)
        os.chmod(cache, 0o700)
        src = os.path.join(cache, "OwReader.java")
        with open(src, "w") as f:
            f.write(JAVA_SRC)
        p = subprocess.run([javac, "-nowarn", "-cp", os.pathsep.join(jars), "-d", cache, src],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        if p.returncode != 0:
            return None, "falha a compilar o leitor Java:\n" + p.stdout
    return java, os.pathsep.join([cache] + jars)


def ler_wallet_java(wallet, args, secrets=True, mostrar=False, pwd=None):
    java, cp = leitor_java(args)
    if not java:
        raise RuntimeError(cp)
    modo = ("secrets" if secrets else "certs") + ("+show" if mostrar else "")
    env = env_oracle(args)
    if pwd:
        env["OW_WALLET_PWD"] = pwd
    p = subprocess.run([java, "-cp", cp, "OwReader", wallet, modo], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True, env=env)
    linha = [l for l in p.stdout.splitlines() if l.startswith("{")]
    if p.returncode != 0 or not linha:
        raise RuntimeError("leitor Java falhou: " + (p.stderr or p.stdout).strip())
    return json.loads(linha[-1])


# --------------------------------------------------------------------------------------
# Leitura alternativa com orapki (sem Java próprio)
# --------------------------------------------------------------------------------------


def ler_certs_orapki(wallet, args):
    orapki = ferramenta("orapki", args)
    if not orapki:
        raise RuntimeError("orapki não encontrado")
    rc, out = run([orapki, "wallet", "display", "-wallet", wallet, "-complete"], env=env_oracle(args))
    if rc != 0:
        raise RuntimeError(out.strip())
    certs, atual, tipo = [], None, "?"
    for l in out.splitlines():
        s = l.strip()
        low = s.lower()
        if low.startswith("user certificates"):
            tipo = "user"
        elif low.startswith("trusted certificates"):
            tipo = "trusted"
        elif low.startswith("requested certificates"):
            tipo = "request"
        m = re.match(r"(Subject|Issuer)\s*:\s*(.+)", s, re.I)
        if m:
            if m.group(1).lower() == "subject":
                if atual and atual.get("subject"):
                    certs.append(atual)
                atual = dict(atual or {}) if atual and not atual.get("subject") else {}
                atual.update({"subject": m.group(2).strip(), "tipo": tipo})
            else:
                atual = atual or {}
                atual["issuer"] = m.group(2).strip()
            continue
        m = re.match(r"(Valid From|Not Before|Valid Until|Valid To|Not After)\s*:\s*(.+)", s, re.I)
        if m and atual is not None:
            chave = "not_before" if m.group(1).lower() in ("valid from", "not before") else "not_after"
            atual[chave] = _parse_data_livre(m.group(2))
    if atual and atual.get("subject"):
        certs.append(atual)
    return certs


def _parse_data_livre(s):
    s = " ".join(s.split())
    for fmt in ("%a %b %d %H:%M:%S %Z %Y", "%b %d %H:%M:%S %Y %Z", "%Y-%m-%d %H:%M:%S",
                "%a %b %d %H:%M:%S %Y"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------------------
# Informação dos ficheiros do wallet
# --------------------------------------------------------------------------------------


def tipo_sso(path):
    """Distingue auto-login de auto-login local pelo cabeçalho do cwallet.sso."""
    try:
        with open(path, "rb") as f:
            h = f.read(8)
    except OSError:
        return "?"
    if len(h) < 4:
        return "?"
    # Cabeçalho: A1 F8 4E <tipo>  -> 36/38 = auto-login, 37/39... = local (varia com a versão)
    if h[:3] != b"\xa1\xf8\x4e":
        return "desconhecido"
    return "auto-login local" if h[3] in (0x37, 0x39) else "auto-login"


def ficheiros_wallet(wallet):
    linhas = []
    for nome in ("cwallet.sso", "ewallet.p12", "ewallet.pem", "tnsnames.ora", "sqlnet.ora", "keystore.jks",
                 "truststore.jks"):
        p = os.path.join(wallet, nome)
        if os.path.isfile(p):
            st = os.stat(p)
            extra = tipo_sso(p) if nome == "cwallet.sso" else ""
            linhas.append([nome, "%d" % st.st_size, oct(st.st_mode & 0o777),
                           datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"), extra])
    return linhas


def _iso(d):
    return d.strftime("%Y-%m-%d") if isinstance(d, datetime.datetime) else (d or "")


def _to_dt(v):
    if isinstance(v, datetime.datetime) or v is None:
        return v
    try:
        return datetime.datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def agrupar_credenciais(secrets):
    """Agrupa oracle.security.client.{connect_string,username,password}N por índice."""
    grupos, outros = {}, []
    for s in secrets:
        al = s["alias"]
        m = re.match(r"oracle\.security\.client\.(connect_string|username|password)(\d+)$", al, re.I)
        if m:
            g = grupos.setdefault(int(m.group(2)), {})
            g[m.group(1).lower()] = s
        elif al.lower().startswith(CLIENT_PREFIX + "default_"):
            g = grupos.setdefault(0, {})
            k = al.lower().replace(CLIENT_PREFIX + "default_", "")
            g[k] = s
        else:
            outros.append(s)
    return grupos, outros


# --------------------------------------------------------------------------------------
# Comando: show
# --------------------------------------------------------------------------------------


def cmd_show(args):
    wallet = os.path.abspath(os.path.expanduser(args.wallet))
    if not os.path.isdir(wallet):
        erro("Diretório do wallet não existe: " + wallet)
        return RC_ERRO
    pwd = None
    if args.password:
        pwd = obter_password(args, confirmar=False)

    res = {"wallet": wallet, "ficheiros": [], "certificados": [], "credenciais": [], "outras_entradas": [],
           "erros": []}
    fich = ficheiros_wallet(wallet)
    res["ficheiros"] = [dict(zip(["ficheiro", "bytes", "permissoes", "alterado", "tipo"], f)) for f in fich]
    if not fich:
        aviso("Nenhum ficheiro de wallet conhecido em " + wallet)

    certs, secrets = [], []
    # 1) Java + oraclepki (cwallet.sso sem password)
    tem_oracle = os.path.isfile(os.path.join(wallet, "cwallet.sso")) or os.path.isfile(
        os.path.join(wallet, "ewallet.p12"))
    if tem_oracle and not args.sem_java:
        try:
            d = ler_wallet_java(wallet, args, secrets=not args.sem_credenciais, mostrar=args.mostrar_passwords,
                                pwd=pwd)
            certs = d.get("certs", [])
            secrets = d.get("secrets", [])
            if d.get("certs_erro"):
                res["erros"].append("certificados: " + d["certs_erro"])
            if d.get("secrets_erro"):
                res["erros"].append("credenciais: " + d["secrets_erro"])
        except Exception as e:  # noqa
            res["erros"].append("leitor Java: %s" % e)
    # 2) Alternativa: orapki display
    if tem_oracle and not certs and (args.sem_java or res["erros"]):
        try:
            certs = ler_certs_orapki(wallet, args)
            res["erros"].append("certificados lidos com 'orapki wallet display' (modo alternativo)")
        except Exception as e:  # noqa
            res["erros"].append("orapki: %s" % e)
    # 3) ewallet.pem (python-oracledb / Autonomous DB)
    pem = os.path.join(wallet, "ewallet.pem")
    if os.path.isfile(pem) and not certs:
        with open(pem) as f:
            for i, p in enumerate(extrair_pems(f.read())):
                try:
                    c = analisar_pem(p)
                    c.update({"alias": "ewallet.pem#%d" % (i + 1), "tipo": "pem"})
                    certs.append(c)
                except Exception as e:  # noqa
                    res["erros"].append("ewallet.pem #%d: %s" % (i + 1, e))

    # Validade
    pior = RC_OK
    if tem_oracle and not certs and any(e.startswith(("certificados:", "leitor Java:", "orapki:"))
                                        for e in res["erros"]):
        pior = RC_ERRO  # não foi possível ler os certificados
    for c in certs:
        nb, na = _to_dt(c.get("not_before")), _to_dt(c.get("not_after"))
        dias, est = estado_validade(na, nb, args.dias)
        c["not_before"], c["not_after"] = _iso(nb), _iso(na)
        c["dias_restantes"], c["estado"] = dias, est
        if est == "EXPIRADO":
            pior = max(pior, RC_EXPIRADO)
        elif est in ("A_EXPIRAR", "AINDA_NAO_VALIDO"):
            pior = max(pior, RC_AVISO)
    certs.sort(key=lambda c: (c.get("dias_restantes") is None, c.get("dias_restantes") or 0))
    res["certificados"] = certs

    grupos, outros = agrupar_credenciais(secrets)
    for idx in sorted(grupos):
        g = grupos[idx]
        pw = g.get("password")
        if pw is None:
            pwtxt = ""
        elif pw.get("valor") is not None:
            pwtxt = pw["valor"]
        else:
            pwtxt = "*" * 8 + " (%d car.)" % pw.get("tamanho", 0)
        res["credenciais"].append({
            "indice": idx,
            "connect_string": (g.get("connect_string") or {}).get("valor"),
            "username": (g.get("username") or {}).get("valor"),
            "password": pwtxt,
        })
    res["outras_entradas"] = [{"alias": o["alias"], "tamanho": o.get("tamanho")} for o in outros]
    res["codigo_saida"] = pior

    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        return pior

    print(cor("\nWallet: ", "negrito") + wallet)
    print(cor("\nFicheiros", "negrito"))
    tabela(["Ficheiro", "Bytes", "Perm.", "Alterado", "Tipo"], fich)

    print(cor("\nCertificados (aviso a %d dias)" % args.dias, "negrito"))
    tabela(["Tipo", "Subject", "Issuer", "Válido de", "Válido até", "Dias", "Estado"],
           [[c.get("tipo", ""), c.get("subject", ""), c.get("issuer", ""), c["not_before"], c["not_after"],
             "" if c["dias_restantes"] is None else str(c["dias_restantes"]), pinta_estado(c["estado"])]
            for c in certs])
    if args.detalhe and certs:
        for c in certs:
            print("\n  - " + c.get("subject", ""))
            for k in ("alias", "issuer", "serial", "sig_alg", "sha256"):
                if c.get(k):
                    print("      %-8s %s" % (k + ":", c[k]))

    if not args.sem_credenciais:
        print(cor("\nCredenciais (Secure External Password Store)", "negrito"))
        tabela(["#", "Connect string", "Utilizador", "Password"],
               [[str(c["indice"]), c["connect_string"] or "", c["username"] or "", c["password"]]
                for c in res["credenciais"]])
        if outros:
            print(cor("\nOutras entradas do secret store (valores não mostrados)", "negrito"))
            tabela(["Alias", "Tamanho"], [[o["alias"], str(o.get("tamanho", ""))] for o in outros], larg_max=90)

    for e in res["erros"]:
        aviso(e)
    resumo = {RC_OK: cor("OK", "verde"), RC_AVISO: cor("AVISO", "amarelo"),
              RC_EXPIRADO: cor("EXPIRADO", "vermelho"), RC_ERRO: cor("ERRO DE LEITURA", "vermelho")}[pior]
    print("\nResultado: %s  (%d certificado(s), %d credencial(is))\n" % (resumo, len(certs), len(res["credenciais"])))
    return pior


# --------------------------------------------------------------------------------------
# Comando: discover
# --------------------------------------------------------------------------------------

WALLET_PARAM_RE = re.compile(r"^\s*([A-Z_]*WALLET_LOCATION)\s*=(.*?)(?=^\s*[A-Z_][A-Z0-9_.]*\s*=|\Z)", re.I | re.M | re.S)


def _sqlnet_paths():
    paths = []
    ta = os.environ.get("TNS_ADMIN")
    if ta:
        paths.append(os.path.join(ta, "sqlnet.ora"))
    for oh in oracle_homes():
        paths.append(os.path.join(oh, "network", "admin", "sqlnet.ora"))
    for p in glob.glob("/u0*/app/oracle/**/network/admin/sqlnet.ora", recursive=False):
        paths.append(p)
    vistos, res = set(), []
    for p in paths:
        rp = os.path.realpath(p)
        if rp not in vistos and os.path.isfile(rp):
            vistos.add(rp)
            res.append(p)
    return res


def _sqlplus_os_auth(args, sql):
    """Consulta com '/ as sysdba' (autenticação do SO, sem credenciais)."""
    sqlplus = ferramenta("sqlplus", args)
    if not sqlplus or not os.environ.get("ORACLE_SID"):
        return None
    script = "set pages 0 lines 400 feed off head off trimspool on verify off\n" + sql + "\nexit\n"
    p = subprocess.run([sqlplus, "-s", "-L", "/ as sysdba"], input=script, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, universal_newlines=True, env=env_oracle(args))
    if p.returncode != 0 or "ORA-" in p.stdout or "SP2-" in p.stdout:
        return None
    return [l.strip() for l in p.stdout.splitlines() if l.strip()]


def cmd_discover(args):
    achados = []

    def junta(path, origem):
        if not path:
            return
        path = os.path.expandvars(os.path.expanduser(path.strip().strip('"').strip("'")))
        existe = os.path.isdir(path)
        ficheiros = ", ".join(f[0] for f in ficheiros_wallet(path)) if existe else ""
        chave = os.path.realpath(path)
        for a in achados:
            if a["chave"] == chave:
                if origem not in a["origem"]:
                    a["origem"] += "; " + origem
                return
        achados.append({"chave": chave, "path": path, "origem": origem, "existe": existe, "ficheiros": ficheiros})

    for s in _sqlnet_paths():
        with open(s) as f:
            txt = "\n".join(l for l in f.read().splitlines() if not l.lstrip().startswith("#"))
        for m in WALLET_PARAM_RE.finditer(txt):
            d = re.search(r"DIRECTORY\s*=\s*([^)\s]+)", m.group(2), re.I)
            junta(d.group(1) if d else m.group(2).strip(), "%s (%s)" % (s, m.group(1).upper()))

    if not args.sem_bd:
        linhas = _sqlplus_os_auth(args, "select 'WR|'||value from v$parameter where name='wallet_root' and value is not null;\n"
                                        "select 'EW|'||wrl_parameter||'|'||status||'|'||wallet_type from v$encryption_wallet;")
        if linhas is None:
            info("Sem consulta à BD (ORACLE_SID não definido ou sem acesso '/ as sysdba').")
        else:
            for l in linhas:
                p = l.split("|")
                if p[0] == "WR" and len(p) > 1:
                    junta(os.path.join(p[1], "tde"), "WALLET_ROOT/tde (%s)" % os.environ.get("ORACLE_SID"))
                    junta(os.path.join(p[1], "tls"), "WALLET_ROOT/tls (%s)" % os.environ.get("ORACLE_SID"))
                elif p[0] == "EW" and len(p) > 1 and p[1]:
                    junta(p[1], "v$encryption_wallet %s %s" % (p[2] if len(p) > 2 else "",
                                                                p[3] if len(p) > 3 else ""))

    padroes = ["/etc/ORACLE/WALLETS/*", "/u0*/app/oracle/admin/*/wallet", "/u0*/app/oracle/admin/*/*wallet*",
               "/u0*/app/oracle/wallet*", "/u0*/app/oracle/product/*/*/owm/wallets/*"]
    for oh in oracle_homes():
        padroes += [os.path.join(oh, "owm", "wallets", "*"), os.path.join(oh, "network", "admin", "wallet*")]
    for extra in args.procurar or []:
        padroes.append(os.path.join(extra, "**"))
    for pad in padroes:
        for d in glob.glob(pad, recursive=True):
            if os.path.isdir(d) and (os.path.isfile(os.path.join(d, "cwallet.sso")) or
                                     os.path.isfile(os.path.join(d, "ewallet.p12"))):
                junta(d, "procura (%s)" % pad)

    if args.json:
        print(json.dumps([{k: v for k, v in a.items() if k != "chave"} for a in achados], indent=2,
                         ensure_ascii=False))
        return RC_OK
    print(cor("\nWallets encontrados", "negrito"))
    tabela(["Path", "Existe", "Ficheiros", "Origem"],
           [[a["path"], "sim" if a["existe"] else cor("não", "vermelho"), a["ficheiros"], a["origem"]]
            for a in achados], larg_max=70)
    print("\nPara validar: %s show -w <path>\n" % os.path.basename(sys.argv[0]))
    return RC_OK


# --------------------------------------------------------------------------------------
# Comando: cert-info
# --------------------------------------------------------------------------------------


def cmd_cert_info(args):
    pems = ler_certificado_entrada(args)
    pior = RC_OK
    linhas = []
    for i, p in enumerate(pems, 1):
        c = analisar_pem(p)
        dias, est = estado_validade(c["not_after"], c["not_before"], args.dias)
        pior = max(pior, RC_EXPIRADO if est == "EXPIRADO" else (RC_AVISO if est != "OK" else RC_OK))
        linhas.append([str(i), c["subject"], c["issuer"], _iso(c["not_before"]), _iso(c["not_after"]),
                       str(dias), pinta_estado(est)])
        if args.detalhe:
            print("#%d serial=%s sha256=%s" % (i, c.get("serial"), c.get("sha256")))
    tabela(["#", "Subject", "Issuer", "Válido de", "Válido até", "Dias", "Estado"], linhas)
    return pior


# --------------------------------------------------------------------------------------
# Escrita: password, backup, orapki, mkstore
# --------------------------------------------------------------------------------------


def obter_password(args, confirmar=False, texto="Password do wallet: "):
    pwd = os.environ.get("ORA_WALLET_PWD")
    if pwd:
        return pwd
    p1 = getpass.getpass(texto)
    if confirmar:
        p2 = getpass.getpass("Confirme a password: ")
        if p1 != p2:
            raise RuntimeError("As passwords não coincidem")
    return p1


def confirmar(args, msg):
    if getattr(args, "yes", False):
        return True
    r = input(msg + " [s/N] ").strip().lower()
    return r in ("s", "sim", "y", "yes")


def backup_wallet(args, wallet):
    if getattr(args, "sem_backup", False) or not os.path.isdir(wallet):
        return None
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    destino_dir = args.backup_dir or os.path.dirname(wallet.rstrip("/"))
    destino = os.path.join(destino_dir, "%s_backup_%s.tar.gz" % (os.path.basename(wallet.rstrip("/")), ts))
    with tarfile.open(destino, "w:gz") as t:
        t.add(wallet, arcname=os.path.basename(wallet.rstrip("/")))
    os.chmod(destino, 0o600)
    info("Backup do wallet: " + destino)
    return destino


def orapki(args, params, pwd=None):
    bin_ = ferramenta("orapki", args)
    if not bin_:
        raise RuntimeError("orapki não encontrado (defina ORACLE_HOME ou use --oracle-home)")
    cmd = [bin_] + params
    if pwd:
        cmd += ["-pwd", pwd]
    rc, out = run(cmd, env=env_oracle(args), mascarar=[pwd])
    print(out.strip())
    if rc != 0 or re.search(r"PKI-\d+|Exception|error", out, re.I):
        raise RuntimeError("orapki terminou com erro (rc=%d)" % rc)
    return out


def mkstore(args, params, stdin_txt):
    bin_ = ferramenta("mkstore", args)
    if not bin_:
        raise RuntimeError("mkstore não encontrado (defina ORACLE_HOME ou use --oracle-home)")
    rc, out = run([bin_] + params, stdin_txt=stdin_txt, env=env_oracle(args))
    out_limpo = "\n".join(l for l in out.splitlines() if l.strip())
    print(out_limpo)
    if rc != 0 or re.search(r"PKI-\d+|Exception|Secret Store error", out, re.I):
        raise RuntimeError("mkstore terminou com erro (rc=%d)" % rc)
    return out


def _wallet_path(args):
    return os.path.abspath(os.path.expanduser(args.wallet))


def _pwd_ou_vazio(args):
    p = obter_password(args)
    return p or None


def cmd_create(args):
    wallet = _wallet_path(args)
    if os.path.exists(os.path.join(wallet, "ewallet.p12")) or os.path.exists(os.path.join(wallet, "cwallet.sso")):
        erro("Já existe um wallet em " + wallet)
        return RC_ERRO
    os.makedirs(wallet, mode=0o700, exist_ok=True)
    params = ["wallet", "create", "-wallet", wallet]
    pwd = None
    if args.modo == "auto_login_only":
        params.append("-auto_login_only")
    else:
        pwd = obter_password(args, confirmar=True, texto="Nova password do wallet (mín. 8, letras e números): ")
        if args.modo == "auto_login":
            params.append("-auto_login")
        elif args.modo == "auto_login_local":
            params.append("-auto_login_local")
    orapki(args, params, pwd)
    info("Wallet criado em " + wallet)
    return RC_OK


def cmd_cert_add(args):
    wallet = _wallet_path(args)
    pems = ler_certificado_entrada(args)
    linhas = []
    for p in pems:
        c = analisar_pem(p)
        dias, est = estado_validade(c["not_after"], c["not_before"], DIAS_AVISO)
        linhas.append([c["subject"], c["issuer"], _iso(c["not_after"]), pinta_estado(est)])
    print(cor("\nCertificado(s) a importar como %s" % args.tipo, "negrito"))
    tabela(["Subject", "Issuer", "Válido até", "Estado"], linhas)
    if not confirmar(args, "Importar para %s?" % wallet):
        return RC_OK
    pwd = None if args.auto_login_only else _pwd_ou_vazio(args)
    backup_wallet(args, wallet)
    tmpdir = tempfile.mkdtemp(prefix="owcert_")
    try:
        for i, p in enumerate(pems):
            f = os.path.join(tmpdir, "cert_%d.crt" % i)
            with open(f, "w") as fh:
                fh.write(p.replace("TRUSTED CERTIFICATE", "CERTIFICATE") + "\n")
            flag = "-trusted_cert" if args.tipo == "trusted" else "-user_cert"
            orapki(args, ["wallet", "add", "-wallet", wallet, flag, "-cert", f], pwd)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    info("Importação concluída. Valide com: show -w " + wallet)
    return RC_OK


def cmd_cert_remove(args):
    wallet = _wallet_path(args)
    alvo = "todos os certificados trusted" if args.todos_trusted else args.dn
    if not alvo:
        erro("Indique --dn \"<DN>\" ou --todos-trusted")
        return RC_ERRO
    if not confirmar(args, "Remover %s (%s) de %s?" % (alvo, args.tipo, wallet)):
        return RC_OK
    pwd = None if args.auto_login_only else _pwd_ou_vazio(args)
    backup_wallet(args, wallet)
    if args.todos_trusted:
        params = ["wallet", "remove", "-wallet", wallet, "-trusted_cert_all"]
    elif args.tipo == "trusted":
        params = ["wallet", "remove", "-wallet", wallet, "-trusted_cert", "-alias", args.dn]
    else:
        params = ["wallet", "remove", "-wallet", wallet, "-user_cert", "-dn", args.dn]
    orapki(args, params, pwd)
    return RC_OK


def cmd_cert_export(args):
    wallet = _wallet_path(args)
    pwd = None if args.auto_login_only else _pwd_ou_vazio(args)
    orapki(args, ["wallet", "export", "-wallet", wallet, "-dn", args.dn, "-cert", args.saida], pwd)
    info("Certificado exportado para " + args.saida)
    return RC_OK


def cmd_cert_selfsigned(args):
    wallet = _wallet_path(args)
    if not confirmar(args, "Criar certificado autoassinado '%s' (%d dias) em %s?" % (args.dn, args.validade, wallet)):
        return RC_OK
    pwd = None if args.auto_login_only else _pwd_ou_vazio(args)
    backup_wallet(args, wallet)
    orapki(args, ["wallet", "add", "-wallet", wallet, "-dn", args.dn, "-keysize", str(args.keysize),
                  "-self_signed", "-validity", str(args.validade), "-sign_alg", "sha256"], pwd)
    return RC_OK


def cmd_cert_request(args):
    wallet = _wallet_path(args)
    pwd = None if args.auto_login_only else _pwd_ou_vazio(args)
    backup_wallet(args, wallet)
    orapki(args, ["wallet", "add", "-wallet", wallet, "-dn", args.dn, "-keysize", str(args.keysize)], pwd)
    orapki(args, ["wallet", "export", "-wallet", wallet, "-dn", args.dn, "-request", args.saida], pwd)
    info("CSR gravado em %s. Depois de assinado, importe com: cert-add -w %s --tipo user -f <cert>" %
         (args.saida, wallet))
    return RC_OK


def _cred(args, acao):
    wallet = _wallet_path(args)
    if acao == "remover":
        if not confirmar(args, "Remover a credencial '%s' de %s?" % (args.connect, wallet)):
            return RC_OK
        wpwd = obter_password(args)
        backup_wallet(args, wallet)
        mkstore(args, ["-wrl", wallet, "-deleteCredential", args.connect], wpwd + "\n")
        return RC_OK
    segredo = os.environ.get("ORA_CRED_PWD")
    if not segredo:
        p1 = getpass.getpass("Password do utilizador %s na BD: " % args.user)
        if p1 != getpass.getpass("Confirme a password: "):
            raise RuntimeError("As passwords não coincidem")
        segredo = p1
    wpwd = os.environ.get("ORA_WALLET_PWD") or getpass.getpass("Password do wallet: ")
    backup_wallet(args, wallet)
    op = "-createCredential" if acao == "criar" else "-modifyCredential"
    # mkstore pede: segredo, confirmação do segredo e password do wallet (por esta ordem)
    mkstore(args, ["-wrl", wallet, op, args.connect, args.user], "%s\n%s\n%s\n" % (segredo, segredo, wpwd))
    info("Credencial '%s' (%s) %s." % (args.connect, args.user, "criada" if acao == "criar" else "alterada"))
    return RC_OK


def cmd_cred_add(args):
    return _cred(args, "criar")


def cmd_cred_modify(args):
    return _cred(args, "alterar")


def cmd_cred_remove(args):
    return _cred(args, "remover")


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def parser():
    ap = argparse.ArgumentParser(
        prog="oracle_wallet.py",
        description="Validação e gestão de Oracle Wallets (certificados e credenciais).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Códigos de saída: 0 OK, 1 certificado a expirar, 2 certificado expirado, 3 erro.",
    )
    ap.add_argument("--version", action="version", version="%(prog)s " + VERSAO)
    ap.add_argument("--oracle-home", help="ORACLE_HOME a usar (por omissão $ORACLE_HOME ou o 1.º do oratab)")
    sub = ap.add_subparsers(dest="cmd")

    def com_wallet(p):
        p.add_argument("-w", "--wallet", required=True, help="diretório do wallet")

    def escrita(p):
        com_wallet(p)
        p.add_argument("-y", "--yes", action="store_true", help="não pedir confirmação")
        p.add_argument("--sem-backup", action="store_true", help="não fazer backup .tar.gz antes de alterar")
        p.add_argument("--backup-dir", help="pasta para o backup (por omissão a pasta-mãe do wallet)")
        p.add_argument("--auto-login-only", action="store_true",
                       help="wallet só com cwallet.sso (sem password)")

    def entrada_cert(p):
        g = p.add_mutually_exclusive_group()
        g.add_argument("-f", "--ficheiro", help="ficheiro .crt/.cer/.pem (PEM ou DER)")
        g.add_argument("--texto", help="texto PEM do certificado (aceita \\n)")
        g.add_argument("--stdin", action="store_true", help="colar o certificado no terminal (Ctrl-D no fim)")

    p = sub.add_parser("discover", help="procura wallets no servidor")
    p.add_argument("--sem-bd", action="store_true", help="não consultar a BD com '/ as sysdba'")
    p.add_argument("--procurar", action="append", help="pasta adicional onde procurar (repetível)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("show", help="certificados (validade) e credenciais do wallet")
    com_wallet(p)
    p.add_argument("--dias", type=int, default=DIAS_AVISO, help="dias para aviso de expiração (omissão 30)")
    p.add_argument("--mostrar-passwords", action="store_true", help="mostra as passwords das credenciais")
    p.add_argument("--sem-credenciais", action="store_true", help="não ler o secret store")
    p.add_argument("--password", action="store_true",
                   help="pedir a password e ler o ewallet.p12 (wallets sem cwallet.sso)")
    p.add_argument("--sem-java", action="store_true", help="usar só 'orapki wallet display'")
    p.add_argument("--detalhe", action="store_true", help="mostra alias, serial e SHA-256")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("cert-info", help="analisa um certificado (ficheiro ou texto)")
    entrada_cert(p)
    p.add_argument("--dias", type=int, default=DIAS_AVISO)
    p.add_argument("--detalhe", action="store_true")
    p.set_defaults(func=cmd_cert_info)

    p = sub.add_parser("create", help="cria um wallet novo")
    com_wallet(p)
    p.add_argument("--modo", choices=["auto_login", "auto_login_local", "auto_login_only", "sem_auto_login"],
                   default="auto_login")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("cert-add", help="importa certificado (ficheiro ou texto)")
    escrita(p)
    entrada_cert(p)
    p.add_argument("--tipo", choices=["trusted", "user"], default="trusted",
                   help="trusted = CA/raiz/intermédio; user = certificado assinado do próprio wallet")
    p.set_defaults(func=cmd_cert_add)

    p = sub.add_parser("cert-remove", help="remove certificado")
    escrita(p)
    p.add_argument("--dn", help="DN do certificado (como aparece no 'show')")
    p.add_argument("--tipo", choices=["trusted", "user"], default="trusted")
    p.add_argument("--todos-trusted", action="store_true", help="remove todos os trusted")
    p.set_defaults(func=cmd_cert_remove)

    p = sub.add_parser("cert-export", help="exporta certificado para ficheiro")
    com_wallet(p)
    p.add_argument("--auto-login-only", action="store_true")
    p.add_argument("--dn", required=True)
    p.add_argument("-o", "--saida", required=True, help="ficheiro de saída")
    p.set_defaults(func=cmd_cert_export)

    p = sub.add_parser("cert-selfsigned", help="cria certificado autoassinado")
    escrita(p)
    p.add_argument("--dn", required=True, help='ex.: "CN=<servidor>,OU=<equipa>,O=<empresa>,C=PT"')
    p.add_argument("--keysize", type=int, default=2048, choices=[2048, 3072, 4096])
    p.add_argument("--validade", type=int, default=3650, help="dias (omissão 3650)")
    p.set_defaults(func=cmd_cert_selfsigned)

    p = sub.add_parser("cert-request", help="cria pedido de certificado (CSR)")
    escrita(p)
    p.add_argument("--dn", required=True)
    p.add_argument("--keysize", type=int, default=2048, choices=[2048, 3072, 4096])
    p.add_argument("-o", "--saida", required=True, help="ficheiro .csr")
    p.set_defaults(func=cmd_cert_request)

    for nome, func, ajuda, com_user in (("cred-add", cmd_cred_add, "cria credencial", True),
                                        ("cred-modify", cmd_cred_modify, "altera credencial", True),
                                        ("cred-remove", cmd_cred_remove, "remove credencial", False)):
        p = sub.add_parser(nome, help=ajuda)
        escrita(p)
        p.add_argument("--connect", required=True, help="alias TNS / connect string (ex.: <ALIAS_TNS>)")
        if com_user:
            p.add_argument("--user", required=True, help="utilizador da BD")
        p.set_defaults(func=func)
    return ap


def main():
    ap = parser()
    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return RC_OK
    try:
        return args.func(args)
    except (KeyboardInterrupt, EOFError):
        erro("Cancelado.")
        return RC_ERRO
    except Exception as e:  # noqa
        erro(str(e))
        return RC_ERRO


if __name__ == "__main__":
    sys.exit(main())
