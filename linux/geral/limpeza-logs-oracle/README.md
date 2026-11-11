# limpeza-logs-oracle

Gestão e limpeza automática de logs (alert.log, listener.log, traces, audit, etc.) **sem reiniciar a base de dados**.
Um assistente em modo texto com fundo azul (whiptail) permite configurar um conjunto de caminhos e, para cada um,
o padrão dos ficheiros, o tamanho máximo e/ou o tempo máximo e a ação a fazer. A limpeza corre todos os dias
pelo cron (ou pelo logrotate) e tudo o que é feito fica num histórico de 365 dias.

## Ficheiros

| Ficheiro | Descrição |
|---|---|
| [`gestao_logs.sh`](gestao_logs.sh) | Assistente interativo + motor de limpeza (`--executar`, `--simular`) |
| [`gestao_logs.conf.example`](gestao_logs.conf.example) | Exemplo de configuração |
| [`CONVERSA.md`](CONVERSA.md) | Resumo do chat que deu origem ao projeto |

Criados na utilização, não versionados (ver `.gitignore`):

| Ficheiro | Conteúdo |
|---|---|
| `gestao_logs.conf` | Configuração no formato .env (`HIST_DIAS=`, uma linha `ENTRADA=` por caminho). Pode ser editado à mão |
| `.gestao_logs.estado` | Data da última limpeza de cada ficheiro (para o limite em dias de `rodar`/`esvaziar`) |
| `historico/AAAA-MM-DD.log` | Histórico diário das ações. Os ficheiros com mais de `HIST_DIAS` dias são apagados |
| `gestao_logs.cron` | Linha de crontab gerada |
| `gestao_logs.logrotate` | Configuração de logrotate gerada |
| `ultima_execucao.log` | Saída da última execução pelo cron |

## Requisitos

| Requisito | Notas |
|---|---|
| bash 4.2+ | Oracle Linux / RHEL 7, 8, 9 |
| `whiptail` (pacote `newt`) ou `dialog` | Só para o assistente. `yum install -y newt` |
| `gzip`, `flock`, `find`, `stat` (GNU) | Normalmente já instalados |

## Instalação

```bash
mkdir -p /home/oracle/gestao_logs && cd /home/oracle/gestao_logs
# copiar gestao_logs.sh para esta pasta
chmod +x gestao_logs.sh
./gestao_logs.sh
```

Os ficheiros de configuração e de histórico ficam na pasta do script. Para usar outra pasta:
`export GESTAO_LOGS_HOME=/caminho`.

## Utilização

```bash
./gestao_logs.sh              # assistente interativo
./gestao_logs.sh --simular    # mostra o que seria feito, sem alterar nada
./gestao_logs.sh --executar   # executa a limpeza (usado pelo cron)
./gestao_logs.sh --ajuda
```

### Menu principal

| Opção | O que faz |
|---|---|
| 1. Ver configuração atual | Lista as entradas com limites e o espaço ocupado neste momento |
| 2. Adicionar caminho / ficheiro | Caminho (pasta ou ficheiro) → padrão → subpastas → ação → tamanho → dias → (dias das cópias) |
| 3. Ativar / desativar entrada | Suspende uma entrada sem a remover |
| 4. Remover entrada | Remove uma entrada |
| 5. Simular limpeza | Mostra o que seria feito, sem alterar nada |
| 6. Executar limpeza agora | Limpa e mostra o resultado |
| 7. Ver histórico | Últimos 7, 30 ou 365 dias, resumo mensal e pesquisa |
| 8. Gerar / instalar crontab | Gera a linha diária e, se quiser, instala no crontab do utilizador |
| 9. Gerar configuração logrotate | Gera `gestao_logs.logrotate` e, como root, copia para `/etc/logrotate.d/` |
| 10. Definições | Dias de histórico (por omissão 365) |

### Ações

| Ação | Uso típico | Quando atua |
|---|---|---|
| `rodar` | alert.log | Tamanho ≥ limite **ou** última limpeza há ≥ N dias → cópia `<ficheiro>.AAAAMMDD_HHMMSS.gz` + esvazia. Cópias com mais de X dias são apagadas |
| `esvaziar` | alert.log, listener.log | Igual a `rodar`, sem cópia |
| `apagar` | `*.trc`, `*.trm`, `*.aud` | Tamanho ≥ limite **ou** sem alterações há mais de N dias |

Esvaziar (`: > ficheiro`) é seguro com a base de dados aberta: o Oracle abre, escreve e fecha o `alert_<SID>.log`
em cada mensagem. Na ação `rodar`, as linhas escritas entre a cópia e o esvaziamento (milissegundos) podem
perder-se, tal como no `copytruncate` do logrotate.

### Exemplo de saída (`--executar`)

```
RODAR       950.1K  /oracle/app/oracle/diag/rdbms/orcl/ORCL/trace/alert_ORCL.log
          tamanho 2.1G >= 1G -> OK, cópia: alert_ORCL.log.20261110_023000.gz (180.3M)
APAGAR          2B  /oracle/app/oracle/diag/rdbms/orcl/ORCL/trace/ORCL_ora_1234.trc
          sem alterações há 40 dias -> OK
------------------------------------------------------------------
 Ficheiros tratados: 2  |  Espaço libertado: 1.9G  |  Erros: 0
```

### Resumo mensal do histórico

```
Mês       Exec.  Rodar  Esvaziar  Apagar  Cópias gz  Erros   Libertado
--------  -----  -----  --------  ------  ---------  -----   ---------
2026-11       2      1         1       3          1      0        1.9G
2026-10      31      2         4     812          2      0       14.2G
```

## Agendamento

| Opção | Notas |
|---|---|
| **cron** (recomendado) | Suporta todas as ações e subpastas. Linha gerada: `30 2 * * * /home/oracle/gestao_logs/gestao_logs.sh --executar > .../ultima_execucao.log 2>&1  # gestao_logs`. Correr como `oracle` (dono dos ficheiros) ou root |
| **logrotate** | Só para `rodar`/`esvaziar` sem subpastas (usa `copytruncate`, `size`/`maxsize`, `maxage`). Intervalos só diário/semanal/mensal/anual. Testar com `logrotate -d /etc/logrotate.d/gestao_logs` |

Não use os dois para as mesmas entradas.

## Segurança

| Proteção | Detalhe |
|---|---|
| Pastas proibidas | `/`, `/bin`, `/boot`, `/dev`, `/etc`, `/lib`, `/lib64`, `/proc`, `/root`, `/sbin`, `/sys`, `/usr`, `/var`, `/home`, `/opt`, `/run` (comparação exata) |
| Confirmação extra | Ação `apagar` com padrão `*` |
| `flock` | Impede duas execuções em simultâneo |
| Simulação | `--simular` / opção 5 antes da primeira execução |
| Erros de compressão | Se o `gzip` falhar (ex.: disco cheio) o ficheiro **não** é esvaziado |

## Comandos úteis

```sql
-- pasta do alert.log e dos traces
SELECT name, value FROM v$diag_info WHERE name IN ('Diag Trace','Diag Alert');
-- pasta dos audit files
SHOW PARAMETER audit_file_dest
```

```bash
# testar se o Oracle volta a escrever no alert.log depois de esvaziado
sqlplus -s / as sysdba <<< "exec dbms_system.ksdwrt(2,'Teste gestao_logs')"
tail -2 alert_<SID>.log

# limpar o alert XML do ADR (roda sozinho a cada ~10 MB) - idade em minutos
adrci exec="set home diag/rdbms/orcl/ORCL; purge -age 43200 -type alert"
```
