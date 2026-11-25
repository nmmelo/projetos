# scripts

Scripts de operação para Oracle Database.

| Pasta | Conteúdo |
|---|---|
| [`rman`](rman) | Monitor pós-backup RMAN: valida o log do backup, limpa backups antigos quando a partição RMAN passa os 90% e envia email por SMTP (erro, sucesso ou alerta de espaço) |
| [`oracle-wallet`](oracle-wallet) | Validação de Oracle Wallets sem credenciais (validade dos certificados e credenciais guardadas, a partir do `cwallet.sso`) e gestão: criar wallet, importar certificados por ficheiro ou texto `.crt`, remover/exportar, autoassinados, CSR e credenciais |
