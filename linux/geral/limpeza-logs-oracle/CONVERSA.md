# Conversa: limpeza-logs-oracle

Chat com o Claude, 10 e 11 de novembro de 2026.

---

## 1. `$ORACLE_BASE/cfgtoollogs/sqlpatch`

**Pergunta:**
> orcle database, /oracle/app/oracle/cfgtoollogs/sqlpatch

**Resposta:** é a pasta onde o **datapatch** guarda os logs de cada execução (uma pasta `sqlpatch_<pid>_<timestamp>/`
por invocação e `<patch_id>/<patch_uid>/` com os logs de apply/rollback por BD e PDB).

| Questão | Resposta |
|---|---|
| São só logs? | Sim. O estado dos patches está em `DBA_REGISTRY_SQLPATCH` |
| Os scripts de rollback estão aqui? | Não: estão em `$ORACLE_HOME/sqlpatch/<patch_id>/` e (18c+) na BD. **Não apagar `$ORACLE_HOME/sqlpatch`** |
| Recomendação | Manter os últimos ciclos de patching (o Suporte pede-os num SR) e apagar os antigos |

```bash
find /oracle/app/oracle/cfgtoollogs/sqlpatch -mindepth 1 -maxdepth 1 -type d -mtime +365 -exec rm -rf {} +
```

---

## 2. alert.log com mais de 2 GB

**Pergunta:**
> preciso de apagar um ficheiro ou fazer truncate, sem ter que reiniciar a base de dados, o alert.log está com mais de 2GB

**Resposta:** pode ser feito com a BD aberta. O Oracle abre, escreve e fecha o `alert_<SID>.log` a cada mensagem;
se for apagado ou renomeado é criado um novo na escrita seguinte.

| Opção | Comandos |
|---|---|
| Arquivar e esvaziar (recomendada) | `gzip -c alert_SID.log > alert_SID.log.$(date +%Y%m%d).gz` + `: > alert_SID.log` |
| Renomear e comprimir | `mv alert_SID.log alert_SID.log.$(date +%Y%m%d)` + `gzip` |
| Só esvaziar | `: > alert_SID.log` |

Notas: a versão XML (`alert/log_*.xml`) roda sozinha a cada ~10 MB e limpa-se com `adrci purge`; o `listener.log`
fica aberto pelo listener (`lsnrctl set log_status off` antes de mexer).

---

## 3. Como esvaziar o ficheiro

**Pergunta:**
> como posso esvaziar o ficheiro

**Resposta:**

```bash
: > alert_<SID>.log          # ou: truncate -s 0 alert_<SID>.log   ou: cat /dev/null > alert_<SID>.log
ls -lh alert_<SID>.log       # 0 bytes
```

Não usar `echo "" >` (deixa uma linha em branco).

---

## 4. Script de gestão de logs

**Pergunta:**
> quero aproveitar e fazer um script tipo setup linux com fundo azul, com a descrição a informar que é para
> configurar a fazer a gestão de pastas, onde colocamos por exemplo o caminho e o ficheiro alert.log ou outro nome
> e dizemos que queremos que seja apagados sempre que atingir 1G ou se passar o tempo definido. preciso de limpar
> os logs, podes criar um script que permita colocar um conjunto de path para analisar todos os dias, em cada path
> podemos indicar quanto tempo queremos manter ou o espaço que pode ocupar, depois guarda isso num ficheiro tipo
> .env ou algum parecido, pode também gerar o crontab para colocar para ficar agendado ou então gerar por exemplo
> para o logrotate.d. Sempre que abrir temos a opção de adicionar mais path ou ficheiros e ver o que já temos.
> sendo que devemos ter um historico de 365 dias sobre o que foi feito para mais facil para fazer gestão.

**Resposta:** foi criado o [`gestao_logs.sh`](gestao_logs.sh):

| Pedido | Implementação |
|---|---|
| Setup tipo Linux com fundo azul e descrição | whiptail (fallback `dialog`) com ecrã de boas-vindas |
| Caminho + ficheiro (alert.log ou outro nome) | Pasta ou caminho completo; padrão com `*` e `?`; opção de subpastas |
| Apagar ao atingir 1G ou ao passar o tempo | Limites de tamanho e de dias por entrada; ações `rodar`, `esvaziar` e `apagar` |
| Conjunto de paths analisados todos os dias | Várias linhas `ENTRADA=` processadas por `--executar` |
| Ficheiro tipo .env | `gestao_logs.conf` (`HIST_DIAS=`, `ENTRADA=...`) |
| Gerar crontab ou logrotate.d | Opções 8 (gera e instala no crontab) e 9 (gera `gestao_logs.logrotate`) |
| Ao abrir: adicionar e ver o que já existe | Menu principal: ver, adicionar, ativar/desativar, remover |
| Histórico de 365 dias | `historico/AAAA-MM-DD.log`, purga automática, vista de 7/30/365 dias, resumo mensal e pesquisa |

Extras: simulação (`--simular`), `flock`, pastas de sistema proibidas, confirmação para `apagar *`, o ficheiro só é
esvaziado se o `gzip` correr bem, compatível com bash 4.2 (OL/RHEL 7).

Testado: rodar por tamanho, purga das cópias `.gz` antigas, apagar `*.trc` com mais de 30 dias (com subpastas),
esvaziar por tempo, caminho inexistente, histórico, resumo mensal e logrotate gerado.

---

## 5. Guardar no repositório

**Pergunta:**
> quero que coloques o limpeza de logs Oracle no meu repositorio projeto, em linux, geral

**Resposta:** guardado em `linux/geral/limpeza-logs-oracle` com o script, o exemplo de configuração, o README e esta
conversa, e adicionado aos READMEs de `linux/geral` e da raiz.
