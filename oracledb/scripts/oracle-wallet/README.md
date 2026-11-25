# Oracle Wallet — validação e gestão (`oracle_wallet.py`)

Script Python (um só ficheiro, sem dependências obrigatórias) para usar em servidores de base de dados Oracle:

- **Validar um wallet sem credenciais** — lê o `cwallet.sso` (auto-login) e mostra **todos os certificados com a
  validade** (dias restantes e estado) e **as credenciais guardadas** (Secure External Password Store).
- **Descobrir os wallets** do servidor (`sqlnet.ora`, `oratab`, `WALLET_ROOT`, `v$encryption_wallet`).
- **Gerir o wallet** (pede a password do wallet): criar wallet, importar certificados por **ficheiro ou texto
  `.crt`**, remover/exportar certificados, criar certificados autoassinados ou pedidos (CSR) e criar / alterar /
  remover credenciais.

Versão: ver [`VERSION`](VERSION).

---

## 1. Requisitos

| Componente | Para quê | Onde |
|---|---|---|
| Python 3.6+ | script | Oracle Linux 7/8/9 (`python3`) |
| `ORACLE_HOME` | `orapki`, `mkstore`, `oraclepki.jar` e o JDK | `$ORACLE_HOME` ou 1.ª entrada do `/etc/oratab` (ou `--oracle-home`) |
| Java (JDK) | ler o `cwallet.sso` sem password | `$ORACLE_HOME/jdk` (incluído no Oracle Home da BD) |
| `$ORACLE_HOME/jlib/oraclepki.jar`, `osdt_cert.jar`, `osdt_core.jar` | API PKI da Oracle | Oracle Home da BD ou Client completo |
| `openssl` **ou** módulo `cryptography` | analisar ficheiros `.crt` / `ewallet.pem` | sistema |

Correr como o utilizador dono do wallet (normalmente `oracle`). Um wallet **auto-login local**
(`-auto_login_local`) só abre no mesmo servidor e com o mesmo utilizador que o criou.

## 2. Como funciona a leitura sem password

Na primeira execução o script compila um pequeno leitor Java (embutido no próprio script) contra o
`oraclepki.jar` do `ORACLE_HOME` e guarda-o em `~/.cache/oracle_wallet/` (permissões `700`). Esse leitor:

1. abre o `cwallet.sso` como keystore `SSO` (provider `OraclePKI`) e lista os certificados *user* e *trusted*
   (subject, issuer, validade, serial, SHA-256);
2. abre o wallet com `OracleWallet.open(<dir>, null)` e lê o *secret store*: agrupa
   `oracle.security.client.connect_stringN / usernameN / passwordN` numa tabela de credenciais.

| Situação | Comportamento |
|---|---|
| Passwords das credenciais | Mascaradas (`********`); só aparecem com `--mostrar-passwords` |
| Outras entradas do secret store (ex.: chaves TDE `ORACLE.SECURITY.DB.ENCRYPTION.*`) | Só o alias e o tamanho — o valor **nunca** é mostrado |
| Sem Java / sem `oraclepki.jar` | Alternativa: `orapki wallet display -complete` (só certificados) |
| Só `ewallet.p12` (sem `cwallet.sso`) | `show --password` pede a password do wallet |
| `ewallet.pem` (python-oracledb thin / Autonomous DB) | Lido diretamente em Python |

## 3. Utilização

```bash
chmod +x oracle_wallet.py

# Procurar wallets no servidor (com ORACLE_SID definido também consulta a BD com '/ as sysdba')
./oracle_wallet.py discover
./oracle_wallet.py discover --sem-bd --procurar /u01/app/oracle/admin

# Validar um wallet (sem password)
./oracle_wallet.py show -w /u01/app/oracle/admin/<SID>/wallet
./oracle_wallet.py show -w <path> --dias 60 --detalhe
./oracle_wallet.py show -w <path> --mostrar-passwords
./oracle_wallet.py show -w <path> --json

# Analisar um certificado antes de importar (ficheiro PEM/DER, texto ou colado no terminal)
./oracle_wallet.py cert-info -f /tmp/<ca>.crt
./oracle_wallet.py cert-info --stdin
```

Exemplo de saída do `show`:

```
Certificados (aviso a 30 dias)
+---------+------------------------+------------------------+------------+------------+------+-----------+
| Tipo    | Subject                | Issuer                 | Válido de  | Válido até | Dias | Estado    |
+---------+------------------------+------------------------+------------+------------+------+-----------+
| trusted | CN=<CA intermédia>,... | CN=<CA raiz>,...       | 2024-01-10 | 2026-12-05 | 9    | A_EXPIRAR |
| user    | CN=<servidor>,...      | CN=<CA intermédia>,... | 2025-03-01 | 2027-03-01 | 460  | OK        |
+---------+------------------------+------------------------+------------+------------+------+-----------+

Credenciais (Secure External Password Store)
+---+----------------+------------+-------------------+
| # | Connect string | Utilizador | Password          |
+---+----------------+------------+-------------------+
| 1 | <ALIAS_TNS>    | <user>     | ******** (12 car.)|
+---+----------------+------------+-------------------+
```

### Códigos de saída (monitorização / cron)

| Código | Significado |
|---|---|
| 0 | OK |
| 1 | Há certificados a expirar (dentro de `--dias`) ou ainda não válidos |
| 2 | Há certificados expirados |
| 3 | Erro (wallet ilegível, ferramenta em falta, operação falhou) |

```bash
# Exemplo: alerta diário
./oracle_wallet.py show -w <path> --dias 45 --sem-credenciais > /tmp/wallet.txt || mail -s "Wallet $(hostname)" <email> < /tmp/wallet.txt
```

## 4. Gestão do wallet (pede a password)

Antes de qualquer alteração é feito um **backup** `.tar.gz` do wallet na pasta-mãe (ou em `--backup-dir`);
`--sem-backup` desativa. Todas as operações pedem confirmação (`-y` para não perguntar).

| Ação | Comando | Ferramenta Oracle |
|---|---|---|
| Criar wallet | `create -w <path> --modo auto_login` (`auto_login_local`, `auto_login_only`, `sem_auto_login`) | `orapki wallet create` |
| Importar certificado (ficheiro) | `cert-add -w <path> -f <ficheiro.crt> --tipo trusted` | `orapki wallet add -trusted_cert` |
| Importar certificado (texto) | `cert-add -w <path> --stdin` ou `--texto "-----BEGIN CERTIFICATE-----\n..."` | idem |
| Importar certificado assinado do servidor | `cert-add -w <path> -f <servidor.crt> --tipo user` | `orapki wallet add -user_cert` |
| Remover certificado | `cert-remove -w <path> --dn "<DN>" --tipo trusted` (ou `--todos-trusted`) | `orapki wallet remove` |
| Exportar certificado | `cert-export -w <path> --dn "<DN>" -o <ficheiro.crt>` | `orapki wallet export -cert` |
| Certificado autoassinado | `cert-selfsigned -w <path> --dn "CN=<servidor>,O=<empresa>,C=PT" --validade 3650` | `orapki wallet add -self_signed` |
| Pedido de certificado (CSR) | `cert-request -w <path> --dn "CN=<servidor>,..." -o <servidor>.csr` | `orapki wallet add` + `export -request` |
| Criar credencial | `cred-add -w <path> --connect <ALIAS_TNS> --user <user>` | `mkstore -createCredential` |
| Alterar credencial | `cred-modify -w <path> --connect <ALIAS_TNS> --user <user>` | `mkstore -modifyCredential` |
| Remover credencial | `cred-remove -w <path> --connect <ALIAS_TNS>` | `mkstore -deleteCredential` |

Notas:

- Num ficheiro `.crt` com a cadeia completa (vários `BEGIN CERTIFICATE`), cada certificado é importado à vez.
  Importe primeiro a raiz e as intermédias (`trusted`) e só depois o certificado do servidor (`user`).
- O `--dn` a usar no `cert-remove` / `cert-export` é o *Subject* mostrado no `show`.
- Para wallets `auto_login_only` (sem `ewallet.p12`) use `--auto-login-only` nas operações de certificados.
  As credenciais (`mkstore`) precisam de um wallet com password.
- Passwords sem interação: variáveis `ORA_WALLET_PWD` (wallet) e `ORA_CRED_PWD` (utilizador da BD).
  A password da credencial e a do wallet são passadas ao `mkstore` por *stdin*; o `orapki` recebe a do wallet
  em `-pwd` (fica mascarada no ecrã, mas é visível no `ps` durante a execução).
- Em 23ai o `mkstore` está *deprecated* mas continua disponível no Oracle Home.

## 5. Ficheiros

| Ficheiro | Descrição |
|---|---|
| `oracle_wallet.py` | Script (inclui o código-fonte do leitor Java) |
| `VERSION` | Versão |
| `CONVERSA.md` | Resumo do pedido que deu origem ao projeto |
| `~/.cache/oracle_wallet/<hash>/` | Leitor Java compilado (criado em runtime, fora do Git) |

## 6. Licença

Apache License 2.0 — ver [`LICENSE`](../../../LICENSE) e [`NOTICE`](../../../NOTICE) na raiz do repositório.
Autor original: Nuno Melo.
