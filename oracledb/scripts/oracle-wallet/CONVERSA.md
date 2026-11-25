# Conversa: Oracle Wallet

Chat de 25 de novembro de 2026.

---

**Pedido:**
> Quero criar um script em Python que, nas bases de dados ou servidores com base de dados, consiga validar a
> Oracle Wallet, indicando qual o path. Para ler não é preciso credenciais, mas permite ver a validade de todos os
> certificados e também as credenciais lá guardadas. No futuro também podemos editar ou fazer o import do
> certificado através de ficheiro ou do texto crt, criar novas credenciais, certificados, retirar. É para colocar
> no projeto, em oracledb/scripts.

**Resposta:** criado o `oracle_wallet.py` (Python 3.6+, um só ficheiro) em `oracledb/scripts/oracle-wallet`.

| Necessidade | Solução |
|---|---|
| Ler sem credenciais | Leitor Java embutido, compilado contra o `oraclepki.jar` do `ORACLE_HOME`, que abre o `cwallet.sso` (keystore `SSO`) e o secret store |
| Validade dos certificados | Tabela com subject, issuer, datas, dias restantes e estado (`OK`, `A_EXPIRAR`, `EXPIRADO`); código de saída para monitorização |
| Credenciais guardadas | Connect string e utilizador; password mascarada (`--mostrar-passwords` para ver); chaves TDE nunca mostradas |
| Indicar / descobrir o path | `show -w <path>` e `discover` (`sqlnet.ora`, `oratab`, `WALLET_ROOT`, `v$encryption_wallet` com `/ as sysdba`) |
| Import por ficheiro ou texto `.crt` | `cert-add` com `-f`, `--texto` ou `--stdin` (PEM, DER ou base64), com pré-visualização e confirmação |
| Criar / retirar | `create`, `cert-selfsigned`, `cert-request`, `cert-remove`, `cert-export`, `cred-add`, `cred-modify`, `cred-remove` (via `orapki` / `mkstore`, com backup `.tar.gz` antes) |

Alternativas quando não há Java: `orapki wallet display -complete` para os certificados; `ewallet.pem` é lido
diretamente em Python.
