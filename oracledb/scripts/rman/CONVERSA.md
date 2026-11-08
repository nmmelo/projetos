# Conversa: monitor RMAN

Chat com o Claude, 8 de novembro de 2026.

---

**Pergunta:**
> Quero também o rman, que é basicamente verificar se o rman está acima de 90%, caso sim apaga os mais antigos e envia um email por smtp, podes colocar no Oracle/scripts com tudo o que lá está

**Resposta:** a pasta `rman/` foi copiada sem alterações de `nmmelo/workspace` (branch `nmmelo`, versão `1.3.0`)
para `oracledb/scripts/rman`. Antes da cópia confirmou-se que não há segredos nos ficheiros versionados: a
password SMTP fica em `config/rman_monitor.conf`, cifrada com `config/smtp.key`, e ambos estão no `.gitignore`.

Resumo do funcionamento (detalhe completo no [`README.md`](README.md) e no [`DIAGRAMA.md`](DIAGRAMA.md)):

| Passo | Script | Ação |
|---|---|---|
| 1 | `check_rman_backup.sh` | Depois do backup, lê o log RMAN mais recente e procura erros de `config/tipos_error.txt` |
| 2 | `clean_rman_space.sh` | Se a partição RMAN estiver ≥ `RMAN_CLEANUP_THRESHOLD` (90%), corre `config/rman_cleanup.cmd` como `oracle` (crosscheck e apaga backups/archivelogs com mais de 7 dias) |
| 3 | `notify_rman.sh` | Envia o email SMTP de erro, sucesso ou alerta de espaço (≥ 80%), conforme `NOTIFY_ON` |
| — | `setup_smtp.sh` | Assistente que grava a configuração e cifra a password SMTP (AES-256) |
| — | `update.sh` / `migrate_config.sh` | Atualizar a instalação ou migrar a configuração de uma instalação antiga |
