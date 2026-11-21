# docker-cleanup

Script Bash para limpar o Docker num Synology (ou em qualquer Linux) de forma periódica, pelo Agendador de
tarefas do DSM ou pelo cron, **sem apagar containers (parados ou a correr) nem volumes**.

Nasceu de um problema concreto: a interface do Docker/Container Manager do DSM demorava muito a abrir e o
`docker system df` respondia `a disk usage operation is already running`. A causa era a acumulação de imagens
antigas (por exemplo, as versões que o Watchtower deixa para trás quando atualiza imagens `:latest`), build
cache e logs de containers.

## O que faz

| Passo | O quê | Regra |
|---|---|---|
| 1 | Imagens | `docker image prune -a` só para imagens **sem nenhum container associado** (nem parado) e com mais de 7 dias |
| 2 | Build cache | `docker builder prune -a` com mais de 7 dias |
| 3 | Redes | `docker network prune` só para redes que nenhum container usa (as dos parados ficam) |
| 4 | Logs de containers | Esvazia (`truncate`) os `*-json.log` acima de 100 MB, sem apagar o ficheiro |

**Não faz:** `docker system prune` (apaga containers parados), `docker volume prune` (apaga dados) nem
`docker rm`.

## Proteções

| Proteção | Como |
|---|---|
| Só corre como root | Sai se não for `root`/`sudo` |
| Uma execução de cada vez | `flock` em `/tmp/docker-cleanup.lock` |
| Daemon encravado | Testa o `docker info` com timeout de 30 s antes de começar |
| Operações penduradas | Cada operação tem `timeout` (600 s por omissão) |
| Verificação final | Compara o número de containers antes e depois e regista um aviso se mudou |
| Teste sem alterações | Opção `--dry-run` |

## Configuração

No início do script:

| Variável | Omissão | Descrição |
|---|---|---|
| `DOCKER` | `/usr/local/bin/docker` | Binário do Docker |
| `DOCKER_ROOT` | `/volume1/@docker` | Pasta de dados do Docker (`docker info -f '{{.DockerRootDir}}'`) |
| `IDADE_MIN` | `168h` | Idade mínima das imagens, cache e redes a limpar (7 dias) |
| `LOG_MAX_MB` | `100` | Tamanho a partir do qual o log de um container é esvaziado |
| `LOG_DIR` | `/volume1/scripts/logs` | Pasta do registo deste script |
| `LOG_KEEP_LINES` | `2000` | Linhas mantidas no registo |
| `TIMEOUT` | `600` | Segundos máximos por operação |
| `DRY_RUN` | `0` | `1` = só mostra o que faria |

## Instalação no Synology

1. Copiar o script para o NAS e dar permissão de execução:

   ```bash
   sudo mkdir -p /volume1/scripts
   sudo cp docker-cleanup.sh /volume1/scripts/
   sudo chmod +x /volume1/scripts/docker-cleanup.sh
   ```

2. Testar sem alterar nada:

   ```bash
   sudo /volume1/scripts/docker-cleanup.sh --dry-run
   ```

3. Agendar em **Painel de Controlo → Agendador de tarefas → Criar → Tarefa agendada → Script definido pelo
   utilizador**:

   | Campo | Valor |
   |---|---|
   | Utilizador | `root` |
   | Agendamento | Semanal, por exemplo domingo às 04:00 |
   | Comando | `/volume1/scripts/docker-cleanup.sh` |

4. Ver o resultado em `/volume1/scripts/logs/docker-cleanup.log`.

Noutro Linux, basta ajustar `DOCKER`, `DOCKER_ROOT` e `LOG_DIR` e agendar no cron do root:

```cron
0 4 * * 0 /opt/scripts/docker-cleanup.sh
```

## Exemplo de registo

```
2026-11-22 04:00:01 | ===== Inicio da limpeza (dry-run=0) =====
2026-11-22 04:00:01 | Espaco antes:
2026-11-22 04:00:03 |   TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE
2026-11-22 04:00:03 |   Images          41        14        38.2GB    24.9GB (65%)
2026-11-22 04:00:03 | 1/4 Imagens sem uso (> 168h)
2026-11-22 04:01:12 |   Total reclaimed space: 22.7GB
2026-11-22 04:01:12 | 2/4 Build cache (> 168h)
2026-11-22 04:01:15 |   Total reclaimed space: 1.3GB
2026-11-22 04:01:15 | 3/4 Redes sem uso (> 168h)
2026-11-22 04:01:16 | 4/4 Logs de containers > 100MB
2026-11-22 04:01:16 |   Truncado jenkins (412M)
2026-11-22 04:01:17 | Containers intactos: 27
2026-11-22 04:01:19 | ===== Fim =====
```

## Notas

- **Pacote Docker vs Container Manager:** no DSM 7.1 ou anterior o pacote chama-se `Docker` (serviço
  `pkg-Docker-dockerd`, configuração em `/var/packages/Docker/etc/dockerd.json`); no DSM 7.2 chama-se
  `ContainerManager` (serviço `pkg-ContainerManager-dockerd`). Confirmar com
  `sudo synopkg list | grep -i -E "docker|container"`.
- **Parar o pacote para todos os containers.** Só voltam sozinhos os que têm `restart: always` ou
  `unless-stopped`.
- **Watchtower:** para não acumular imagens antigas, definir `WATCHTOWER_CLEANUP=true`.
- **Limitar os logs na origem** (aplica-se aos containers criados ou recriados depois), no `dockerd.json`:

  ```json
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
  ```

- **Containers parados há muito tempo** continuam a segurar as suas imagens. O script não os apaga: remover à
  mão os que já não fazem falta (`sudo docker rm <nome>`), depois de fazer backup dos volumes.
