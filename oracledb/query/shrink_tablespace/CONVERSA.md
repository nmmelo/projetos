# Conversa: shrink de tablespaces Oracle

Chat com o Claude, de 3 a 6 de novembro de 2026. As perguntas estão como foram escritas; as respostas
estão resumidas, mas mantêm os comandos e as conclusões. O script final e o README estão nesta pasta.
O portal web que executa este script está em [`docker/portal-oracle`](../../../docker/portal-oracle).

---

## 1. Script de validação e shrink

**Pergunta:**
> preciso de um script para oracle database, para validar um tablespace para fazer shrink, caso não seja possivel , queria reorganizar o memso

**Resposta:** foi criado o `shrink_tablespace.sql` (SQL*Plus + PL/SQL):

| Passo | Ação |
|---|---|
| Diagnóstico | Tamanho, ocupado, HWM e espaço recuperável por datafile |
| Shrink direto | `ALTER DATABASE DATAFILE ... RESIZE` até ao HWM |
| Reorganização | `MOVE ONLINE` / `REBUILD ONLINE` dos segmentos no fim do ficheiro e novo resize |

Modos `ANALISE` (só mostra os comandos) e `EXECUTAR`. O README explica requisitos, parâmetros e cuidados.

---

## 2. Multitenant: `ORA-20001: Tablespace ... nao existe`

**Pergunta:**
> era isto, podiamos pedir tb ALTER SESSION SET CONTAINER = <NOME_DA_PDB>;

**Resposta:** a sessão estava no `CDB$ROOT`. O script passou a listar as PDBs e a pedir em qual está o
tablespace. Depois da troca de contentor é preciso voltar a fazer `SET SERVEROUTPUT ON`: sem isso, o output
do PL/SQL perde-se sem aviso (foi a causa de um "não aparece nada" mais tarde).

---

## 3. Desempenho: o cálculo do HWM demorava

**Perguntas:**
> está a demorar tempo, ===== 2. Datafiles ANTES ...
> A calcular o HWM de cada datafile ...

**Resposta:**

| Problema | Solução |
|---|---|
| `DBA_EXTENTS` muito lenta (junta extents com o dicionário) e lida várias vezes | Uma só leitura e, como SYS, leitura direta de `SYS.X$KTFBUE` |
| Espaço livre por datafile | `DBA_FREE_SPACE` |
| Saber em que passo vai | `DBMS_APPLICATION_INFO`: `V$SESSION.MODULE = 'SHRINK_TS'`, passo em `ACTION` |

Houve um erro de HWM = 2 MB (filtro `con_id` no `X$KTFBUE` dentro da PDB). Foi corrigido, com validação: se o
HWM calculado for inferior ao ocupado, o script recalcula com `DBA_EXTENTS`. O resize nunca pede menos do que o
espaço ocupado.

Resultado no TS_SIMPLEX_DAT: **4850 MB → 2396 MB** (2454 MB recuperados).

---

## 4. Um tablespace ou todos

**Pergunta:**
> é possivel fazer a todos os tablespaces, ou seja ter a possibilidade de escolher um ou todos, menos de system, TEMP e UNDO

**Resposta:** `p_ts = TODOS` processa todos os tablespaces permanentes exceto SYSTEM/SYSAUX (UNDO e TEMP
nunca entram). Ignora os que têm menos espaço livre do que o ganho mínimo e termina com um RESUMO GLOBAL.

Também foram dadas queries para listar os tablespaces por tamanho, ocupado e livre (`DBA_FREE_SPACE`, ou a
alternativa rápida `DBA_TABLESPACE_USAGE_METRICS`).

---

## 5. Caso real: TS_GMO_LOB (16 GB com 217 MB de dados)

| Execução | O que aconteceu |
|---|---|
| 1.ª | O `MOVE ONLINE LOB` reduziu o LOB de 7992 MB para ~200 MB, mas o segmento novo ficou **no fim do ficheiro** (o antigo e o novo coexistem durante o MOVE) |
| 2.ª | O LOB foi para o início; o resize para 252 MB deu `ORA-03297` |
| Diagnóstico | HWM real (`X$KTFBUE`) = 1099 MB: segmentos **`TEMPORARY`** órfãos (`GMO.462.170`, `GMO.462.194`), restos do MOVE, que o `DBA_EXTENTS` não mostrava |
| Limpeza | `ALTER SESSION SET EVENTS 'immediate trace name DROP_SEGMENTS level <ts#+1>'` (com o `ts#` calculado num bloco PL/SQL) e depois resize |

**Pergunta:**
> dá para melhorar o script de shrink para ver estas situações

**Resposta:** o script passou a tratar automaticamente estas situações:
- **2.ª passagem** de reorganização, quando o HWM continua acima do alvo;
- **limpeza dos `TEMPORARY`**, só se não houver MOVE/REBUILD/CREATE INDEX a correr em `GV$SESSION`;
- **resize com tentativas** em caso de `ORA-03297`.

---

## 6. SYSAUX com muitos GB

**Pergunta:**
> Teno o SYSAUX com muitos GB

**Diagnóstico** (`V$SYSAUX_OCCUPANTS`):

| Ocupante | MB | Causa |
|---|---|---|
| JOB_SCHEDULER | 30 789 | Job `SYS.PURGE_LOG` **desativado desde 2023-07-01**; o LOB SecureFile `SCHEDULER$_JOB_OUTPUT.OUTPUT` tinha 30 GB |
| SM/ADVISOR | 21 682 | `AUTO_STATS_ADVISOR_TASK` (MOS 2305512.1) |
| SM/AWR, SM/OPTSTAT | ~1 000 cada | Normal |

**Ações:**
- Scheduler: `SET_SCHEDULER_ATTRIBUTE('log_history', 30)`, `PURGE_LOG`, `ENABLE('SYS.PURGE_LOG')` e `store_output = FALSE` nos jobs com muito output.
- Advisor: `EXECUTION_DAYS_TO_EXPIRE`, `DROP_ADVISOR_TASK` + `INIT_PACKAGE`.

**Limite:** numa PDB, o `MOVE`/`SHRINK` destes objetos do SYS dá **`ORA-65040`**. A limpeza liberta o espaço
dentro dos segmentos (é reutilizado e o SYSAUX deixa de crescer), mas para o devolver ao disco é preciso um
SR na Oracle Support. Recuperável de imediato: ~2,9 GB no datafile 475 (resize até ao HWM).

Ver se o purge está ativo:
```sql
SELECT enabled, state, last_start_date, next_run_date, failure_count
FROM   dba_scheduler_jobs WHERE owner = 'SYS' AND job_name = 'PURGE_LOG';
```
