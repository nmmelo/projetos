# shrink_tablespace.sql

Script SQL*Plus para **Oracle Database** que verifica se um tablespace, ou **todos**, pode ser reduzido (resize dos datafiles). Quando o ganho direto não chega, **reorganiza** os segmentos que estão no fim dos datafiles e volta a tentar o resize.

---

## Requisitos

| Item | Detalhe |
|---|---|
| Versão Oracle | 12.2 ou superior |
| Operações ONLINE | Enterprise Edition (em Standard Edition use `ONLINE = N`) |
| Privilégios | DBA: leitura das views `DBA_*`, `ALTER DATABASE`, `ALTER ANY TABLE`, `ALTER ANY INDEX` |
| Tablespaces suportados | Permanentes e ONLINE (não aceita SYSTEM, SYSAUX, UNDO nem TEMP) |

---

## Como funciona

### Um tablespace ou todos

| `p_ts` | Comportamento |
|---|---|
| `NOME_TS` | Processa só esse tablespace |
| `TODOS` (default) | Processa os tablespaces permanentes, do que tem mais espaço livre para o que tem menos. Exclui **SYSTEM, SYSAUX, UNDO e TEMP** |

Com `TODOS`, alguns tablespaces são **ignorados** (aparecem no resumo global com o motivo):
- os que não estão ONLINE (READ ONLY ou OFFLINE);
- os que têm **menos espaço livre do que o ganho mínimo** (`p_min_ganho`). O ganho nunca pode ser maior do que o espaço livre, e assim evita-se o cálculo do HWM, que pode ser demorado. Para os incluir, baixe o ganho mínimo.

Um erro num tablespace não interrompe os restantes. No fim aparece um **RESUMO GLOBAL** com uma linha por tablespace: tamanho antes e depois, ganho, se houve reorganização, erros e estado.

### Passos por tablespace

| Passo | Ação |
|---|---|
| 1. Validação | Confirma que o tablespace existe, é permanente e está ONLINE |
| 2. Diagnóstico | Mostra, por datafile, o tamanho, o espaço ocupado, o HWM, o espaço recuperável já e o recuperável depois de reorganizar |
| 3. Recyclebin | Avisa se há objetos `BIN$` e faz o purge se for pedido |
| 2b. HWM | Calcula o HWM de cada datafile (via `X$KTFBUE` ou `DBA_EXTENTS`) e o alvo (ocupado + margem) |
| 4. Shrink direto | Limpa os segmentos `TEMPORARY` órfãos, se os houver, e faz `ALTER DATABASE DATAFILE ... RESIZE` até ao HWM atual |
| 5. Reorganização | Só corre se o ganho extra for ≥ ao mínimo definido. Move os segmentos que estão acima do alvo em cada datafile |
| 5b. 2.ª passagem | Se depois do MOVE o HWM continuar acima do alvo, volta a mover os segmentos que ficaram no fim do ficheiro |
| 6. Índices UNUSABLE | Reconstrói os índices que ficaram inválidos |
| 7. Shrink final | Faz um novo resize até ao novo HWM, com tentativas progressivas se der `ORA-03297` |
| Resumo | Tamanho antes e depois, espaço recuperado, comandos OK ou com erro e objetos para tratar à mão |

### Tratamento por tipo de segmento

| Tipo de segmento | Comando gerado |
|---|---|
| TABLE | `ALTER TABLE ... MOVE [ONLINE] TABLESPACE ...` |
| TABLE (overflow de IOT) | `ALTER TABLE <iot> MOVE OVERFLOW TABLESPACE ...` |
| TABLE PARTITION / SUBPARTITION | `ALTER TABLE ... MOVE [SUB]PARTITION ...` |
| INDEX | `ALTER INDEX ... REBUILD [ONLINE]` |
| INDEX (IOT) | `ALTER TABLE <iot> MOVE ...` |
| INDEX PARTITION / SUBPARTITION | `ALTER INDEX ... REBUILD [SUB]PARTITION ...` |
| LOBSEGMENT | `ALTER TABLE ... MOVE LOB(col) STORE AS (TABLESPACE ...)` |
| LOB PARTITION | `ALTER TABLE ... MOVE PARTITION ... LOB(col) STORE AS (...)` |
| LOBINDEX | Ignorado, porque se move junto com o LOB |
| Objetos de SYS/SYSTEM e outros tipos | Marcados como `-- MANUAL` |

Sem `ONLINE`, os comandos levam `UPDATE INDEXES` para os índices continuarem válidos.

### Situações tratadas automaticamente

| Situação | O que o script faz |
|---|---|
| O `MOVE ONLINE` deixa o segmento novo **no fim do ficheiro**, porque o antigo e o novo coexistem durante o MOVE | **2.ª passagem**: volta a mover esses segmentos, que vão então para o espaço libertado no início |
| Segmentos **`TEMPORARY`** órfãos, restos de MOVE/REBUILD que o SMON ainda não limpou (o `DBA_EXTENTS` pode não os mostrar) | Lista-os. Em EXECUTAR, se não houver MOVE/REBUILD/CREATE INDEX a correr (`GV$SESSION`), executa `ALTER SESSION SET EVENTS 'immediate trace name DROP_SEGMENTS level <ts#+1>'` e espera até 60 s |
| `ORA-03297` no resize (há blocos usados acima do valor pedido) | Recalcula o HWM e tenta tamanhos maiores (+10 % + 64 MB), até 6 tentativas |
| Objetos na recyclebin (`BIN$`) | Avisa, e com `p_purge = S` faz `PURGE TABLESPACE` |

---

## Parâmetros

| Parâmetro | Default | Descrição |
|---|---|---|
| `p_pdb` | (vazio) | PDB onde está o tablespace. Com Enter mantém o contentor atual (base de dados não-CDB ou sessão já na PDB) |
| `p_ts` | `TODOS` | Nome de um tablespace, ou `TODOS` para processar todos os permanentes exceto SYSTEM e SYSAUX (UNDO e TEMP ficam sempre de fora) |
| `p_modo` | `ANALISE` | `ANALISE` só mostra os comandos. `EXECUTAR` aplica-os |
| `p_online` | `S` | Usa `ONLINE` nos MOVE e REBUILD (requer EE) |
| `p_min_ganho` | `100` | Ganho mínimo (MB) para avançar com a reorganização |
| `p_margem` | `10` | Espaço livre (%) que fica em cada datafile |
| `p_purge` | `N` | Faz `PURGE TABLESPACE` da recyclebin |

---

## Utilização

### 1. Modo ANALISE (recomendado primeiro)

```bash
sqlplus / as sysdba @shrink_tablespace.sql
```

O script começa por listar as PDBs disponíveis e pergunta em qual está o tablespace. Em seguida indique o nome do tablespace e carregue em **Enter** nos restantes prompts para ficar com os defaults. O modo por omissão é `ANALISE`.

> **Multitenant (CDB/PDB):** quem entra com `sqlplus / as sysdba` fica na raiz `CDB$ROOT`, onde os tablespaces das PDBs não são visíveis. Sem indicar a PDB, o script falha com `ORA-20001: Tablespace ... nao existe no contentor CDB$ROOT`.

Em ANALISE o script **não altera nada**: só faz SELECTs e mostra os comandos que executaria.

### 2. O que verificar no output

| Secção | O que indica |
|---|---|
| Datafiles ANTES | `recuperavel_ja_mb` > 0 quer dizer que o shrink direto é possível |
| 3. Diagnóstico | Ganho direto e ganho estimado depois de reorganizar |
| 4. Shrink direto | Comandos `RESIZE` que seriam executados |
| 5. Reorganização | Segmentos que bloqueiam o shrink e os comandos que seriam aplicados |
| 7. Shrink final | `RESIZE` estimados depois da reorganização |
| `-- MANUAL` | Objetos que o script não trata sozinho |

### 3. Modo EXECUTAR

Corra de novo e responda `EXECUTAR` no prompt do modo.

### Execução sem interação

Comente as linhas `ACCEPT` no script e defina os valores antes de o chamar:

```sql
DEFINE p_pdb       = MINHA_PDB
DEFINE p_ts        = USERS
DEFINE p_modo      = ANALISE
DEFINE p_online    = S
DEFINE p_min_ganho = 100
DEFINE p_margem    = 10
DEFINE p_purge     = N
@shrink_tablespace.sql
```

---

## Log

Todo o output fica gravado em `shrink_tablespace_<TABLESPACE>.log`, na diretoria a partir da qual o SQL*Plus foi lançado.

O script também cria aí o ficheiro auxiliar `_set_container.sql`, que contém o `ALTER SESSION SET CONTAINER`. Pode apagá-lo depois de correr.

---

## Desempenho

A view `DBA_EXTENTS` é muito lenta em tablespaces com muitos extents, porque junta cada extent com o dicionário de segmentos. Por isso, o script:

| Dado | Fonte |
|---|---|
| Espaço ocupado e livre | `DBA_FREE_SPACE`, que é rápida |
| HWM e extents | `SYS.X$KTFBUE`, lida diretamente e muito mais rápida (precisa de ligação como **SYS**) |
| Recurso | Sem acesso a `X$KTFBUE`, usa `DBA_EXTENTS` automaticamente |

A secção 2b indica qual dos métodos foi usado e quanto tempo demorou o cálculo.

### Acompanhar o progresso

O output do bloco PL/SQL só aparece no fim. Para ver em que passo vai o script, corra noutra sessão, na mesma PDB:

```sql
SELECT s.sid, s.action passo, s.event, s.last_call_et seg_neste_sql, s.sql_id,
       SUBSTR(q.sql_text, 1, 100) sql_text
FROM   v$session s
LEFT   JOIN v$sql q ON q.sql_id = s.sql_id AND q.child_number = s.sql_child_number
WHERE  s.module = 'SHRINK_TS';
```

A coluna `ACTION` (alias `PASSO`) mostra o tablespace e o passo (`2 calculo HWM`, `5 reorg 3/40 ...`) e `SQL_TEXT` a query em curso.

Se correr com `DBA_EXTENTS` e for lento:

```sql
-- objetos BIN$ tornam DBA_EXTENTS muito mais lenta
PURGE TABLESPACE <TABLESPACE>;

-- estatísticas do dicionário e das fixed tables (correr fora de horas de ponta)
EXEC DBMS_STATS.GATHER_DICTIONARY_STATS;
EXEC DBMS_STATS.GATHER_FIXED_OBJECTS_STATS;
```

---

## Notas e cuidados

- **Corra sempre primeiro em ANALISE** e reveja os comandos antes de usar EXECUTAR.
- Sem `ONLINE`, o `MOVE` **bloqueia a tabela** enquanto corre. Execute numa janela de manutenção.
- Um erro num comando (por exemplo, uma tabela com coluna `LONG` ou um `ORA-03297` no resize) **não interrompe** o script: o erro é registado e passa ao objeto seguinte.
- O `MOVE` dentro do mesmo tablespace nem sempre coloca os objetos no início do ficheiro. O script faz uma 2.ª passagem automática. Se mesmo assim o HWM não baixar o suficiente, repita mais tarde ou mova os objetos para um tablespace intermédio e traga-os de volta.
- O `MOVE` e o `REBUILD` geram redo e precisam de espaço livre temporário igual ao tamanho do segmento. Confirme que há espaço suficiente e, se houver Data Guard, verifique o lag.
- O `PURGE TABLESPACE` remove **definitivamente** os objetos da recyclebin desse tablespace.
- Faça um **backup** antes de correr em produção.
- **Oracle 23ai:** em tablespaces BIGFILE existe `DBMS_SPACE.SHRINK_TABLESPACE`, que faz este processo de forma nativa.

---

## SYSAUX

O script **não processa o SYSAUX**, porque os objetos são do Oracle (`SYS`). Para reduzir o SYSAUX:

1. Ver quem ocupa o espaço:
   ```sql
   SELECT occupant_name, schema_name, ROUND(space_usage_kbytes / 1024) mb
   FROM   v$sysaux_occupants ORDER BY space_usage_kbytes DESC FETCH FIRST 10 ROWS ONLY;
   ```
2. Limpar com os procedimentos oficiais de cada ocupante:

| Ocupante | Limpeza |
|---|---|
| `JOB_SCHEDULER` | Confirmar que o job `SYS.PURGE_LOG` está ativo; `DBMS_SCHEDULER.PURGE_LOG(log_history => 30)`; `store_output = FALSE` nos jobs com muito output (LOB `SCHEDULER$_JOB_OUTPUT.OUTPUT`) |
| `SM/ADVISOR` | `AUTO_STATS_ADVISOR_TASK`: `EXECUTION_DAYS_TO_EXPIRE`, `DBMS_STATS.DROP_ADVISOR_TASK` + `INIT_PACKAGE` (MOS 2305512.1) |
| `SM/OPTSTAT` | `DBMS_STATS.ALTER_STATS_HISTORY_RETENTION` e `PURGE_STATS` (por fases) |
| `SM/AWR` | `DBMS_WORKLOAD_REPOSITORY.MODIFY_SNAPSHOT_SETTINGS(retention => ...)` e `DROP_SNAPSHOT_RANGE` |
| `AUDSYS` | `DBMS_AUDIT_MGMT.CLEAN_AUDIT_TRAIL` |

3. Fazer o resize até ao HWM real, se houver espaço livre no fim dos datafiles.

> **Numa PDB**, o `MOVE`/`SHRINK` de tabelas e LOBs do dicionário (`SYS.SCHEDULER$_JOB_OUTPUT`, `SYS.WRI$_ADV_OBJECTS`, ...) dá `ORA-65040`. A limpeza liberta o espaço *dentro* dos segmentos, que é reutilizado, e o SYSAUX deixa de crescer. Para devolver o espaço ao disco, é preciso abrir um SR na Oracle Support.
