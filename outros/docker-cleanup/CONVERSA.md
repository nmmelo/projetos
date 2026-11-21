# Conversa: docker-cleanup

Chat de 21 de novembro de 2026.

---

## 1. Ver o container que consome mais

**Pergunta:**
> No portainer como consigo ver o container a consumir mais

**Resposta:** o Portainer não tem uma vista ordenada por consumo; só mostra estatísticas container a container
(ícone *Stats* em Quick Actions). Pelo terminal:

```bash
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}" | sort -k2 -hr
```

Alternativas para monitorizar de forma contínua: `ctop`, cAdvisor + Grafana, Netdata.

## 2. Docker do Synology lento a abrir

**Pergunta:**
> o synology está bem, mas quando abro o docker, demora muito tempo abrir

**Resposta:** a interface pede ao daemon a lista de containers, imagens, volumes e redes e o cálculo de
espaço; com muita acumulação ou discos lentos, isso demora. Causas prováveis: imagens e containers antigos,
logs enormes, hibernação dos discos, pouca RAM ou um container com muito I/O. Primeiro passo:
`sudo docker system df`.

## 3. Cálculo de espaço preso

**Pergunta:**
> `Error response from daemon: a disk usage operation is already running`

**Resposta:** a própria interface tinha lançado um cálculo de espaço que não terminava, e o Docker só aceita um
de cada vez. Alternativa sem passar pelo daemon: `sudo du -sh /volume1/@docker/*`.

## 4. Parar o pacote

**Pergunta:**
> mas se parar o container manager vai parar os container

**Resposta:** sim; só voltam os que têm `restart: always`/`unless-stopped`. Não é preciso parar: fechar a
interface e usar o `du`.

A tentativa de parar o pacote deu erro. Os comandos com `ContainerManager` falharam porque, nesta versão do
DSM, o pacote chama-se **Docker** (`synopkg status Docker`, `systemctl status pkg-Docker-dockerd`). O daemon
estava ativo há mais de 3 meses e os containers a correr; a tentativa só matou os containers temporários do
visualizador de documentos do DSM (recriados automaticamente) e parou um container, que foi arrancado à mão.

## 5. Causa da acumulação

Pela lista do `docker ps -a`: vários containers parados há semanas ou meses (cada um segura a sua imagem) e o
Watchtower a atualizar imagens `:latest`, deixando as versões antigas para trás se não tiver
`WATCHTOWER_CLEANUP=true`.

## 6. `docker system prune -a`

**Pergunta:**
> sudo docker system prune -a , mas este apaga container parados

**Resposta:** sim — apaga containers parados, as imagens deles, redes sem uso e build cache (volumes só com
`--volumes`). Perigoso quando há containers parados que são para manter ou imagens construídas localmente.
Alternativa segura: `docker image prune -a` + `docker builder prune`.

## 7. Script agendado

**Pergunta:**
> podemos criar um script para colocar no synology para executar nas tarefas de x em x tempo, para limpar
> coisas do docker, sem apagar container parados ou a funcionar

**Resposta:** criado o `docker-cleanup.sh` (imagens sem uso, build cache, redes sem uso e logs grandes, tudo
com mais de 7 dias), com `--dry-run`, `flock`, timeouts, verificação do daemon e do número de containers, e
registo com rotação. Agendado no Agendador de tarefas do DSM como root.
