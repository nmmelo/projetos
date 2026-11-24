# Conversa: portal Oracle em Python (Docker)

Chat de 5 a 6 de novembro de 2026. As perguntas estão como foram escritas; as respostas
estão resumidas, mas mantêm os comandos e as conclusões. Os ficheiros finais estão nesta pasta.
O script de shrink que o portal executa está em [`oracledb/query/shrink_tablespace`](../../oracledb/query/shrink_tablespace).

---

## 1. Portal em Python para o shrink

**Pergunta:**
> é possível colocar neste script e colocar num portal desenvolvido em python , que permita escolher a base de dados, tendo para isso uma zona de configuração de base de dados, onde colocamos o host, porta, SID, ServiceName, PDB, user, password (encriptada) e se é user normal ou sysdba, devendo usar para isso algum driver em python que permita fazer a ligação da base de dados

**Resposta:**

| Componente | Escolha |
|---|---|
| Web | Flask + Gunicorn + Nginx |
| Driver Oracle | `python-oracledb` em modo *thin* (sem Oracle Instant Client) |
| Passwords | Cifradas com Fernet (`data/fernet.key`) |
| Execução | Em segundo plano; passo atual lido de `V$SESSION.ACTION`; log e cancelamento (`KILL SESSION`) |

---

## 2. Primeiros problemas com o docker-compose v1

| Erro | Causa e solução |
|---|---|
| `yaml.parser.ParserError` | Indentação: `volumes:` com 2 espaços em vez de 4 |
| `Utilizador ou password invalidos` | O `docker-compose` **v1** não converte `$$` em `$` no `.env`. O `manage.py hash-password` passou a mostrar as duas versões, e o portal aceita ambas |

---

## 3. Utilizadores, perfis, backups e contentor único

**Pergunta (resumo):**
> permitir criar utilizadores, perfil ... escolher que base de dados podemos ver e o que fazer ... apenas um container com o pyhton e o nginx, tambem deve ter o openssh ... vscode ... mariadb ou postgres ... git ... ping, ip, vim, nano, cron ... minha area ... O admin é o único utilizador local, ficheiro ... backup ... Home page, onde temos o numero de base de dados.

**Resposta:**

| Pedido | Implementação |
|---|---|
| Home | Nº de bases de dados, execuções em curso e nas últimas 24 h, último backup |
| Perfis | Bases de dados visíveis + permissões (`tablespaces.ver`, `shrink.analise`, `shrink.executar`, `config.bds`, `config.portal`). Para novas funcionalidades basta registar a ação em `app/auth.py` |
| Admin | Único utilizador local, em `data/admin.json` (`manage.py set-admin-password`) |
| Configuração | Abas **Bases de dados** e **Portal** (Geral, Armazenamento, Utilizadores, Perfis, Backups) |
| Armazenamento | SQLite, MariaDB ou PostgreSQL, local ou remoto, com cópia dos dados |
| Backups | Criar, importar (juntar/substituir), descarregar, remover ficheiro e registo; `manage.py backup --prune` no cron |
| Contentor | Nginx + Gunicorn + OpenSSH + cron + git, geridos pelo supervisord |

---

## 4. SSH com utilizadores pessoais

**Pergunta:**
> quero incluir as chaves publica de dois utilizadores, o nmmelo e o adminssc no container, com sudoers.

**Resposta:** um ficheiro `data/ssh/users/<utilizador>.pub` por pessoa. No arranque, o contentor cria a conta
(UID fixo em `.uid`), dá-lhe sudo `NOPASSWD` e adiciona-a aos grupos `sshusers` e `portal`.

Problema encontrado no NAS Synology (`/volume1`): `Could not open user 'nmmelo' authorized keys ...: Permission denied`.
O `sshd` lê as chaves com a identidade do utilizador e a ACL da partilha impedia a leitura. As chaves passaram
para `/etc/ssh/authorized_keys/<utilizador>`, dentro do contentor (`AuthorizedKeysFile`). Também foram
corrigidos os fins de linha Windows (CRLF) e as permissões das homes.

---

## 5. Tudo no mount do anfitrião

**Pergunta:**
> quero que app seja um mount e não seja copiado para a vm, porque a ideia é puder atualizar, se o container for destruído, pelo menos essa informação é guardada.

**Resposta:** a imagem só tem o sistema. O código (`app/`), as configurações (`docker/*.conf` e
`entrypoint.sh`, através de ligações simbólicas), os dados e as homes vêm todos da pasta do anfitrião.
Alterações ao `requirements.txt` são instaladas no arranque. O `.dockerignore` evita enviar `data/` para o build.

```yaml
volumes:
  - ./:/srv/portal          # código + data/
  - ./data/home:/home       # homes dos utilizadores
```

---

## 6. Ubuntu 26.04 e instalação sem Docker

**Perguntas:**
> e debian a distribuição, eu quero ubuntu 26.04
> pode ter no readme.md, o procedimento para instalar e configurar num servidor e não num container ...

**Resposta:**

| No Ubuntu 26.04 | Adaptação |
|---|---|
| Utilizador `ubuntu` com o UID 1000 na imagem | Removido |
| Python 3.14 + PEP 668 | venv em `/opt/venv` |
| sudo-rs por omissão | Instalado o `sudo` clássico, para o `sudoers.d` |

O README passou a ter a instalação num servidor: pacotes, utilizador `portal`, venv, `.env`, password do
admin, serviço systemd (`deploy/oracle-portal.service`), Nginx (`deploy/nginx-oracle-portal.conf`), HTTPS,
firewall e cron.

---

## 7. MariaDB como armazenamento

**Erro:**
> Erro: (1142, "SELECT command denied to user 'portal_oracle'@'172.17.0.1' for table `portal-oracle`.`settings`")

**Resposta:** a ligação funcionava, mas faltava o `GRANT` para o host de onde o contentor liga, com o nome da
base de dados entre backticks por causa do hífen:

```sql
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP
   ON `portal-oracle`.* TO 'portal_oracle'@'172.%';
```

---

## 8. Script de instalação num servidor (sem Docker)

Chat de 19 de novembro de 2026.

**Pergunta:**
> no projeto portal-oracle, pode por vezes ser necessário ter outro nome, por exemplo nome-portal, nesse sentido na opção de instalação num servidor (não docker), podemos ter um script que faça a instalação e configuração inicial, para não ser necessário alterar todas as referencias nos ficheiros, na qual diz logo qual a base de dados, se é local ou remote, se for remote, por exemplo dá os scripts ou indica onde está o sql para correr no postgres ou mysql para criar tudo o que for necessário, no caso do postgres o pg_hba.conf para permitir acesso remoto, qual o nginx se queremos SSL, na qual indicamos onde está o certificado, podemos manter os passos todos no README.MD na mesma para acrescentar a parte de ativar o SSL e o que é preciso de fazer, pode ser uma opção, para quem não percebe. [...] Podemos também aproveitar o projeto do extrair uma pasta do git, para não estar a fazer o download inteiro do repositorio projeto.

**Resposta:**

| Ficheiro | O que faz |
|---|---|
| `instalar.sh` | Instalação interativa: nome da instância (pasta, utilizador, serviço, site do Nginx e cron derivados do nome), código local ou só a pasta do portal no Git (sparse-checkout), base de dados SQLite/MariaDB/PostgreSQL local ou remota, Nginx com HTTP, certificado próprio ou Let's Encrypt, systemd, administrador, cron e firewall. Reconfigurar e `--desinstalar` |
| `deploy/*.tpl` | Templates do serviço systemd e do Nginx (HTTP e HTTPS) com marcadores `@NOME@`, `@DIR@`, ... (substituem os ficheiros com o nome fixo) |
| `deploy/bd/preparar-postgresql.sh` | No servidor PostgreSQL: utilizador e base de dados, linha no `pg_hba.conf`, `listen_addresses`, reload/restart e firewall só para o IP do portal |
| `deploy/bd/preparar-mariadb.sh` | No servidor MariaDB: base de dados, utilizador `'<util>'@'<IP>'` com GRANT, `bind-address` e firewall |
| `deploy/bd/*.sql` | SQL para quem prefere fazer à mão, com os passos de rede em comentário |
| `manage.py set-storage` / `show-storage` | Definir e testar a base de dados do portal pela linha de comandos |

Outras decisões:

- O código instalado passa a ser do `root` (só leitura para o utilizador de serviço), porque o root volta a correr o `instalar.sh` e os templates; `data/` e `.venv` ficam do utilizador de serviço.
- O `extrair_pasta_git.sh` passou a aceitar argumentos (`-r -b -p -d`), e a mesma lógica está embutida no `instalar.sh`, que pode ser descarregado sozinho.
- O README mantém o passo a passo manual, agora com variáveis (`NOME`, `DIR`, ...) e com o HTTPS explicado (validar certificado e chave, juntar a cadeia, template HTTPS, cookies seguros, testes).

---

## 9. Aparência configurável: título, rodapé, ícone e tema

Chat de 19 de novembro de 2026.

**Pergunta:**
> quero que seja possivel alterar o nome que aparece no login ou no portal, neste momento é Oracle · Gestão de espaço, ter na configuração uma zona portal, onde podemos alterar o titulo, rodape, faicon, o tema, podemos ter 2 ou 3 temas diferentes.

**Resposta:** nova aba **Configuração → Portal → Aparência** (o título saiu da aba Geral):

| O quê | Como ficou |
|---|---|
| Título, texto no login, rodapé | Campos de texto; `{ano}` no rodapé é trocado pelo ano atual |
| Favicon | Upload PNG/ICO/SVG até 32 KB, guardado na tabela `settings` (entra nos backups), servido em `/favicon.ico`. Tipo verificado pelo conteúdo; SVG com scripts recusado e servido com CSP. Ícone por omissão na cor do tema |
| Temas | Claro, Escuro e Oracle. O CSS passou a ter todas as cores em variáveis; cada tema é um bloco `html[data-tema="..."]` |
| Repor | Botão "Repor por omissão" |

Ficheiros: `app/aparencia.py` (temas e validações), `app/templates/config/appearance.html`, `app/static/favicon.svg`, `app/static/style.css`, rota `/favicon.ico` em `app/views/main.py`. Capturas em `docs/`.

---

## 10. Erro "Control server error: Permission denied" no Gunicorn

Chat de 19 de novembro de 2026.

**Pergunta:**
> [ERROR] Control server error: [Errno 13] Permission denied: '/opt/dba-portal/.gunicorn/guni

**Resposta:** o Gunicorn 25.1 passou a criar um control socket (`gunicornc`) em `$XDG_RUNTIME_DIR/gunicorn.ctl` ou em `$HOME/.gunicorn/`. Na instalação sem Docker o HOME do utilizador de serviço é a pasta do código, que o `instalar.sh` deixa só de leitura para o serviço. Correção no `deploy/portal.service.tpl`: `RuntimeDirectory=<nome>` e `Environment=XDG_RUNTIME_DIR=/run/<nome>`. Para instalações existentes: voltar a correr o `instalar.sh` ou `systemctl edit <nome>` com essas linhas (ver README, "Problemas conhecidos"). No Docker não acontece (HOME gravável).

---

## 11. instalar.sh: configuração guardada, menu e ajuda

Chat de 19 de novembro de 2026.

**Pergunta:**
> mas ao voltar fazer instalar.sh, devia mostrar as configurações já feitas, ou seja devem ser guardadas, podemos também incluir ajuda tipo instalar.sh --help [...]

**Resposta:**

| Antes | Agora |
|---|---|
| Respostas em `/opt/<nome>/.instalacao`, só lidas se se escrevesse o mesmo nome | Registo em `/etc/portal-oracle/<nome>.conf`; o script lista as instâncias e propõe a que corresponde à pasta onde corre |
| Gravadas só no fim (perdiam-se se a instalação falhasse) | Gravadas logo após a confirmação, marcadas como incompleta até ao fim |
| Não mostrava nada | Mostra a configuração guardada e um menu: rever, reaplicar sem perguntas, atualizar pelo Git, desinstalar, sair |
| `--ajuda` com o cabeçalho do ficheiro | `--help` completo; novas opções `NOME`, `--lista`, `--mostrar`, `--reaplicar`, `--atualizar` |

O ficheiro antigo `.instalacao` é lido e migrado.

---

## 12. Módulos, manutenção e sincronização sem indisponibilidade

Chat de 19 de novembro de 2026.

**Pergunta:**
> o problema, é que cada vez que faço sincronização do portal, depois o portal fica indisponivel, deve ser os ficheiros que vem do repositorio, também como é uma instalação no servidor, tudo o que seja docker, durante o processo de instalação, pode ser removido para garantir que não fica lixo. Também ter a opção nas configurações, a opção de colocar o portal em manutenção, tendo um editor html. Podemos também colocar tudo num modulo de oracle, ou seja o portal vai servir para mais coisas, a ideia é ter vários modulos, podemos fazer parecido ao projeto de portal no repositorio workspace, onde temos diversos modulos, podemos ativar ou desativar, cada modulo ativo ativa os menus, por exemplo oracle, deve aparecer uma aba oracle e depois diversas abas, com as funções, por exemplo definições, onde temos as configurações de base de dados. como vamos ter modulos, os perfis podem estar associados aos modulos.

**Resposta:**

| Tema | O que mudou |
|---|---|
| Portal indisponível depois de sincronizar | Causas: cópia como root muda o dono de `data/`; o Gunicorn continua com o código antigo; ficheiros antigos ficam. O serviço corrige as permissões ao arrancar (`ExecStartPre=+chown`), o `instalar.sh` substitui o código em vez de copiar por cima, e há `--verificar` (diagnóstico) e `--reaplicar` |
| Ficheiros do Docker no servidor | `Dockerfile`, `docker-compose.yml`, `.dockerignore` e `docker/` são removidos na instalação e na atualização |
| Reinício | O Nginx mostra "O portal está a reiniciar" (`app/static/indisponivel.html`) em vez de 502 |
| Manutenção | Configuração → Manutenção: ativar/desativar, editor HTML com pré-visualização, HTML filtrado, 503 para quem não gere o portal, aviso no login; `manage.py maintenance` e `instalar.sh --manutencao` |
| Módulos | `app/modulos.py` (registo) e `app/modules/oracle/` (Blueprint `/oracle`). Configuração → Módulos ativa/desativa; menu Oracle com abas Tablespaces, Shrink, Execuções e Definições (bases de dados) |
| Perfis por módulo | Permissão de acesso `modulo.oracle` e ações `oracle.*`; formulário agrupado por módulo, com as bases de dados dentro do Oracle. Perfis e backups antigos são convertidos; URLs antigos redirecionam |

Modelo seguido: o portal do repositório workspace (tabela de módulos ativáveis, menus por módulo, manutenção com editor HTML onde só os administradores entram, e script de linha de comandos).

---

## 13. Shrink em direto: barra "ORACLE", passos e output linha a linha

Chat de 19 de novembro de 2026.

**Pergunta:**
> fiz um analise de shrink, contudo não dá nada, devia ser mais dinamico, por exemplo como no projeto portal, onde temos a barra tesla a carregar e aparece em baixo o ouput dinamico, neste caso podia ser a barra oracle.

**Resposta:** o bloco PL/SQL corria de uma vez e o `DBMS_OUTPUT` só era lido no fim, por isso, durante a execução, a página só mostrava o passo atual (ex. "2 calculo HWM (db file sequential read)").

| O quê | Como |
|---|---|
| Percentagem | O bloco regista o avanço de cada passo em `V$SESSION_LONGOPS` (`SET_SESSION_LONGOPS`, permilagem; em TODOS, uma fatia por tablespace) |
| Output em direto | Cada `p()` também envia a linha por `DBMS_PIPE` (chamada dinâmica: sem o privilégio, o bloco compila e só o output em direto fica desligado) |
| Monitor | A segunda ligação lê a cada 2 s o passo, a percentagem e as linhas do pipe e grava-os na execução (nova coluna `progress`). O output final continua a vir do `DBMS_OUTPUT` |
| Página | Painel ao estilo do "TESLA a carregar…": ORACLE animado, barra com percentagem, passos, tempo decorrido; output com cores e "Acompanhar o fim" |

Privilégio opcional para as linhas em direto: `GRANT EXECUTE ON SYS.DBMS_PIPE TO <utilizador>`.

---

## 14. Execuções com filtros, agente do portal e módulo Sistema

Chat de 19 de novembro de 2026.

**Pergunta:**
> temos as execuções antigas, deviamos poder ter uma opção de ver a execução, ou seja o output depois de ter acabo, ou apagar por exemplo os que falharam, podemos também fazer filtro por sucesso, falhado, erro, aviso, por tablespace ou palavra, devendo estar numa tabela dinamica e ao clicar num registo, abrir uma janela e mostrar onde falhou. Como este processo pode demorar, gostava de saber se fizer refresh ao browser a execução para, ou continua, senão podemos criar um agente, serviço que faça isso e vá dado informação ao portal, para não para isso. Podemos ter um modulo com o nome de sistema, basicamente vai dar informação sobre o servidor onde está o portal a correr, com o CPU, RAM, DISCO, parecido a imagem em questão, podendo aparecer os serviços como o nginx, o dba-portal, o novo agente que vai ser criado, mostrado os serviços, podendo fazer stop, restart ou start. sendo que o refresh deve ser feito de forma dinamica quase realista. pode ser feito pelo agente, por exemlo o agente do portal que fica responsavel por isso. Podemos organizar os cartões do componentes de sistema.

**Resposta:**

- **Refresh do browser:** não para a execução. O que a parava era reiniciar o portal; com o agente, já não.
- **Execuções:** tabela dinâmica (pesquisa no servidor, também no output; filtros por resultado com contagens, tablespace, BD e modo; ordenação; páginas), janela com o output, navegação pelas linhas de erro/aviso, apagar uma, as selecionadas ou as falhadas (`oracle.execucoes.apagar`). Novas colunas `runner`, `n_erros`, `n_avisos`.
- **Agente** (`app/agente.py`, serviço `<nome>-agente`, ou programa `agente` no supervisord): fila de execuções (PENDENTE), sinal de vida, métricas a cada 2 s, ações sobre serviços (`sudo -n systemctl`, com `/etc/sudoers.d/<nome>-agente` limitado aos serviços escolhidos no `instalar.sh`). Testado: o portal foi reiniciado a meio de uma execução e ela terminou no agente.
- **Módulo Sistema:** cartões ao estilo da imagem (recursos com barras e gráfico, servidor, agente, um por serviço com Start/Restart/Stop, discos, últimas ações), atualizados a cada 2 s, arrastáveis e minimizáveis (disposição guardada no browser). Definições: serviços a mostrar.

## 15. HWM mais rápido, erro 500 nas Execuções, módulo SMTP e sininho

Chat de 19 de novembro de 2026.

**Pergunta:**
> a execução está a demorar muito, podemos HWM, podemos aplicar as melhorias feitas noutro projeto, neste repositorio, que faz o mesmo, contudo é feito atraves de script e não de portal, [...] oracledb, query, shrink tablespace. eu sai do menu execuções e depois quando volto dá a pagina, Internal Server Error [...] Criar também o modulo de smtp, neste caso será para usar o interno, será com tls, podes ver o projeto do rman no repositorio, onde definimos tudo, se é com TLS, se validamos, com autenticação ou não, neste caso será para avisar por exemplo quando uma execução acabar. [...] no geral o botão teste, com uma barra enviar, e o output do envio a acabar com sucesso. no smtp, ter uma aba geral, notificações, logs e também será responsavel o agente do portal [...] outra aba com regras [...] também ter o simbolo de notificação, que fica no portal, quando houver notificação fica com o numero de notificações ao clicar no icon, abre e mostra as notificações [...]

**Resposta:**

- **HWM:** com ligação Normal o bloco usava `DBA_EXTENTS` (lento). Agora usa, por esta ordem, `X$KTFBUE` (SYSDBA), a vista `SYS.X_$KTFBUE` (como no script) ou uma estimativa por `DBA_FREE_SPACE`, com recurso a `DBA_EXTENTS` só se o resize der ORA-03297. O *Testar ligação* mostra o método e o SQL do grant.
- **Internal Server Error:** acontecia em Execuções com uma execução em curso (versão anterior à tabela dinâmica). Corrigido; os erros 500 passam a ter página própria e referência no log.
- **Módulo SMTP** (ativar em Configuração → Módulos): abas Geral (Nenhuma/STARTTLS/SSL, validar certificado, autenticação opcional, teste com barra e diálogo SMTP), Regras (email), Notificações (sininho) e Logs (reenviar). Eventos: execução Oracle, serviço, disco acima do limiar, ligação SMTP. O agente envia a fila e valida a ligação.
- **Sininho** no topo, com o número de notificações por ler e a lista ao clicar.

## 16. Filtros das Execuções, painel do Sistema e Home com cartões editáveis

Chat de 19 de novembro de 2026.

**Pergunta:**
> como podemos ver pelas imagens, a parte de filtragem pode ser melhorada no que toca a layout e no sistemas, está um pouco confuso, não se se é o layout, tem que ser melhorado, como na imagem gerada. No home também tem que ser refresh de e em x segundos. Também podemos organizar os cartões, tendo um botão de editar com um icon, podemos adicionar ou remover como na imagem

**Resposta:**

- **Causa do aspeto "sem formatação":** o browser usava o `style.css` antigo (o Nginx guarda `/static/` em cache 1 h). O CSS e o JS passam a ter `?v=<versão>`, que muda em cada atualização.
- **Execuções:** filtros num só bloco (pesquisa com ícone, listas com rótulo, Limpar filtros) e os resultados num seletor com cor e contagem, com as ações de apagar à direita.
- **Sistema:** cartões ao estilo pedido (mosaicos com barra, estado Saudável/Atenção, serviços numa lista com ▶ ⟳ ■), intervalo de atualização escolhido (pausa a 30 s).
- **Home:** painel de cartões dos módulos (Oracle, Sistema, SMTP, Notificações, Portal), atualização automática (manual, 10 s, 30 s, 1 min, 5 min) e botão **Editar** (lápis) para esconder, adicionar e arrastar cartões; disposição guardada por utilizador. O mesmo editor no Sistema.

## 17. Instalar e atualizar em Docker numa pasta

Chat de 19 de novembro de 2026.

**Perguntas:**
> como posso fazer o deploy por docker, como consigo fazer o download da ultima versão

> mas queria fazer para uma pasta

> sim podes por

**Resposta:** novo script `portal-docker.sh`. Descarrega só a pasta `docker/portal-oracle` (sparse checkout, `--depth 1`) para uma pasta temporária e copia-a para a pasta de destino, sem tocar em `data/` nem no `.env`. Na 1.ª vez (`-d PASTA -r URL`) cria o `.env`, arranca o contentor e pede a password do `admin`; depois basta `-d PASTA` (a origem fica em `.origem` e a versão em `.versao`). O `instalar.sh` remove-o nas instalações sem Docker.

## 18. Portas HTTP e SSH no Docker

Chat de 19 de novembro de 2026.

**Pergunta:**
> este script não me permite alterar a porta de http e ssh no docker-compose, eu altero mas depois quando corro o portal-docker.sh, altera sempre para 2222 e 8080

**Resposta:** o `docker-compose.yml` vem do repositório e é substituído em cada atualização. As portas passaram para o `.env` (`PORTAL_HTTP_PORT`, `PORTAL_SSH_PORT`), que o script nunca altera; também se mudam com `portal-docker.sh -H <http> -S <ssh>`. Na 1.ª atualização, as portas alteradas à mão num `docker-compose.yml` antigo passam sozinhas para o `.env`. Outras alterações locais vão para um `docker-compose.override.yml`, que o script também preserva.

## 19. Erro das portas no Docker Compose e README

Chat de 19 de novembro de 2026.

**Perguntas:**
> como posso colocar as chaves no caso do docker, por exemplo as chaves publicas e privada

> ERROR: The Compose file './docker-compose.yml' is invalid because: services.portal.ports contains an invalid type, it should be a number, or an object

> alterar o readme.me para ter estas alterações. retirar qualquer referencia a [...]

**Resposta:**

- **Erro do Compose:** uma porta no `.env` não era só um número (texto do exemplo, aspas, espaços, comentário ou CRLF). O `portal-docker.sh` passa a limpar e validar `PORTAL_HTTP_PORT`/`PORTAL_SSH_PORT` e corre `config -q` antes de arrancar.
- **Chaves:** públicas em `data/ssh/users/<utilizador>.pub` (ou `data/ssh/authorized_keys` para o `portal`); privada de preferência por *agent forwarding*, ou em `data/home/<utilizador>/.ssh/` com `700`/`600`.
- **README:** obter o script na 1.ª vez, portas no `.env`, validações, chaves no Docker e os dois erros na tabela de problemas.

## 20. 403 Forbidden no Docker

**Pergunta:**
> está no ar , mas dá 403 Forbidden

**Resposta:** com uma `umask` restrita no servidor (027/077), o `git clone` do `portal-docker.sh` criava o código só legível pelo root; dentro do contentor, o Nginx (www-data) e o Gunicorn (portal) não o liam. O script passa a usar `umask 022` e a corrigir as permissões do código em cada execução (sem mexer em `data/` nem no `.env`).

## 21. "No container found for portal_1"

**Pergunta:**
> docker-compose exec -u portal portal python manage.py set-admin-password — ERROR: No container found for portal_1

**Resposta:** o contentor não estava a correr (parou no arranque, ou não foi criado porque já existia outro contentor `oracle-portal`). O nome passou a ser configurável (`PORTAL_CONTAINER` no `.env`), e o `portal-docker.sh` deteta um contentor com o mesmo nome de outra pasta e espera que o contentor fique a correr, mostrando o log se não ficar.

## 22. Firewalld no Sistema, língua, agendamento e edição de módulos

Chat de 20 de novembro de 2026.

**Pergunta:**
> permitir por exemplo no caso do firewalld, iniciar , parar e restart, tendo também ao pé do icon notificação, a opção de mudar de lingua, neste caso deve ter portuges, ingles pode ser paracido ao projeto portal no repositorio workspace, onde temos com icon. Nas configuração, podemos ter ouma aba com agendamento, onde temos se está ativo, temos lá os modulos que tem backup, outros modulos que possam aparecer, para isso na parte dos modulos podemos ter editar e lá podemos alterar o nome, ver quem tem acesso (um ou mais perfis), o agendamento, ou seja ativo, notificação. Na aparencia temos sub abas, uma com geral, outra com linguagem, onde temos as linguas, podendo ver a tradução , podendo alterar se quisermos, podemos também export em xml e alterar lá e voltar a importar o xml, fazendo primeiro uma validação.

**Resposta:**

- **Firewalld:** `instalar.sh --servicos <nome> +firewalld` atualiza o sudoers do agente (validado com `visudo`); Sistema → Definições sugere os serviços instalados e mostra se cada um está autorizado; no painel, 🔒 em vez dos botões quando não está.
- **Língua:** bandeiras (português, inglês) ao lado do sininho, por utilizador e na página de entrada; tradução no browser a partir de `app/i18n/en.xml` (cerca de 1000 textos).
- **Aparência:** sub-abas Geral e Linguagem (línguas, língua por omissão, acrescentar línguas, ver/alterar traduções, exportar XML, importar com validação e revisão das diferenças antes de aplicar).
- **Agendamento:** nova aba, executado pelo agente: backup do portal, limpeza do histórico, verificação de tablespaces (Oracle) e validação SMTP; frequência, notificação (evento "Tarefa agendada terminada"), parâmetros, executar agora e histórico.
- **Módulos → Editar:** nome, perfis com acesso, agendamentos e regras de notificação do módulo.

## 23. Autenticação LDAP/AD, Oracle → Bases de dados, Firewall e agente cliente

Chat de 20 de novembro de 2026. Pedido grande, feito por fases (cada uma com o seu commit).

**Pergunta (resumo das partes):**
> depois quero outro modulo, autenticação, onde podemos ter AD ou OPENLDAP, onde podemos configurar o acesso dos utilizadores, [...] o administrador, é sempre local [...] na configuração, podemos ter uma aba a dizer autenticação, onde aparece local com o nº de utilizadores com isso, LDAP a mesma coisa, sendo que ao clicar no LDAP, abre uma janela onde temos que configurar o metodo com todos os parametros e também podemos ter a opção de testar [...] pode ser necessário colocar o certificado do servidor LDAP ou podemos ignorar a validação.

> Também no aba oracle, quero outra subaba a dizer base de dados [...] versão, [...] SID, Servicename, se tem DataGuard ativo, qual o estado dela online/mount/close [...] ao clicar na base de dados [...] o espaço livre no ASM [...] os jobs, o recycle bin [...] Os backup por expdb e também os backup de rman [...] criar um utilizador dboperator [...] quais os grants necessários [...] os schemas [...] os criados pelo portal ficam com uma cor diferente [...]

> [...] agente cliente, ou seja poder instalar o agente como um serviço no cliente, do genero zabbix [...] token [...] SSH [...] know_hosts [...] .env com toda a informação sensivel encriptada [...] uma tag ou regra que colocamos um nome, ele gera um id [...] o agente vai buscar a ultima linha ou log [...] nada de referencias ao [...]

> [...] nos serviços do portal tenha o serviço firewall, ou pode ser um modulo, onde vamos poder criar as regras e ver regras e ipsets [...] logs [...] geral com o estado geral da firewalld ou ufw [...] definições [...]

**Resposta — fase 1, módulo Autenticação:** tipo de autenticação por utilizador (Local ou LDAP; o administrador é sempre local), Configuração → Autenticação com os cartões Local e LDAP (número de utilizadores) e a janela de configuração (driver OpenLDAP ou Active Directory, STARTTLS/LDAPS, certificado da CA ou sem validação, conta de serviço, filtro, grupo obrigatório, criação no primeiro login) com **Testar ligação** passo a passo; registo de acessos.

**Resposta — fase 2, Oracle → Bases de dados:** tabela com estado (verde aberta, laranja MOUNT/a mudar, vermelho fechada/sem ligação, com o texto ao passar o rato), versão, SID, service name, Data Guard e log mode; janela por BD com Geral, ASM, Data Guard, Jobs, Recyclebin (por schema, com detalhe), Schemas (criados pelo portal a roxo, com filtro), Backups RMAN e Data Pump. Utilizador dedicado `DBOPERATOR`: privilégios com o motivo, SQL para PDB e CDB$ROOT e botão **Criar** pela ligação guardada (e passar a usá-lo).

**Resposta — fase 3, módulo Firewall:** firewalld ou ufw (deteção automática), abas Geral (estado, zonas ativas, recarregar, histórico de alterações), Regras (serviços, portas, origens, regras ricas por zona; regras numeradas no ufw), IPsets (criar, apagar, entradas), Logs (serviço e pacotes recusados) e Definições (versão, LogDenied, modo pânico). Permissões com `instalar.sh --firewall <nome> on`.

**Resposta — fase 4, agente cliente (módulo Clientes):** clientes em Configuração → Clientes (nome, IP/DNS, VM, Debian ou Red Hat, utilizador SSH) com token, comandos de instalação por distribuição e **Testar** (DNS, porta SSH, SSH com a chave do portal e known_hosts próprio, último contacto). Agente em `/opt/agente-portal/` (Python 3 sem dependências) com `.env` cifrado, serviço systemd, socket local e fila; `setup.sh` verifica o utilizador SSH (`authorized_keys`, permissões) e acrescenta a chave do portal. Regras de notificação com id gerado e log opcional (última linha); `agente-portal notificar <ID> ...` para os scripts (rman, shrink, montagens). O intervalo de sincronização vê-se no portal e muda-se só no cliente. Traduções em inglês dos textos novos.

## 24. IPsets em tabela, largura da página e agente no --lista

**Pergunta:**
> melhorar o aspecto o ipsets, pode ser uma tabela e depois ao clicar vai para a ipsets concreta. Agora nem aparece o dba-portal-agente. Podemos também melhorar o aspecto da janela centra, pode ser dinamico [...]

**Resposta:** IPsets em tabela (tipo, entradas, onde são usados) com página por ipset; botão de largura da página (automática, toda a largura, normal); no `instalar.sh --lista` o agente aparece na coluna AGENTE da instância.

## 25. SMTP e Autenticação dentro de Configuração

**Pergunta:**
> o smtp tem que ficar quando ativo dentro do configuração, e não no menu principal. e aautenticação também dentro de configuração

**Resposta:** SMTP e Autenticação saem do menu principal e passam a abas de Configuração, com as sub-abas de cada um (SMTP: Geral, Regras, Notificações, Logs; Autenticação: Utilizadores e métodos, LDAP, Acessos).

## 26. Firewall mais rápida, alarmes nos clientes e preparação para o Zabbix

**Pergunta:**
> o modulo de firewall está um pouco lento a carregar as rules, ipsets, melhorar mais. nos clientes podemos criar alarmisticas, por exemplo se tiver com o disco A ou B a 90% avisar por notificação e/ou por email, isso podemos definir na criação da regra de alarmistica. No futuro pode integrar com a plataforma zabbix., podemos deixar tudo mais ou menos preparado.

**Resposta:** Firewall: `firewall-cmd` em paralelo, todas as zonas numa chamada, um `--info-ipset` por ipset (já traz as entradas), cache de 15 s limpa a cada alteração e botão Atualizar; várias entradas de ipset de uma vez. Clientes → Alarmes: disco (pontos de montagem com `*`), CPU, memória, carga e sem contacto, com limiar, ciclos, severidade e canais próprios (portal e/ou email). Zabbix: API só de leitura com token (descoberta LLD, métricas com chaves Zabbix, alarmes como triggers), host Zabbix por cliente, endereço/token da API guardados e `agente-portal valor <chave>`.

## 27. Cache das bases de dados e tarefas em segundo plano

**Pergunta:**
> no dba portal, quando seleciono uma base de dados aparece sempre a fazer ligação a base de dados, pode ser uma barra a dizer oracle a carregar, é um processo demorado, podemos fazer cache para o portal [...] pode ficar uma barra em baixo [...] podemos sair e isso continua, até podemos ter mais de que uma tarefa a realizar. depois de feito atualiza a cache ou se tivermos na janela da base de dados faz um auto refresh dos dados..

**Resposta:** Cache no portal (`cache_dados`) para o estado das BDs, a janela de cada BD, Tablespaces e a lista do Shrink: mostra logo a cache e atualiza em segundo plano; "Oracle a carregar…" quando não há dados; barra em baixo com as tarefas em curso (várias, continua noutras páginas); a vista aberta atualiza-se sozinha quando a tarefa acaba; ↻ Atualizar força a leitura.

## 28. Shrink em segundo plano, agendado e cancelamento com ORA-00031

**Pergunta:**
> cancelar o shrink de um tablespace, Não foi possível cancelar: DPY-4011 [...] ORA-00031: session marked for kill [...] podemos colocar a fazer e depois podemos acompanhar na aba execuções [...] na home um cartão com tarefas a fazer ou agendadas, também podemos agendar o shrink por exemplo para ser feito de noite, quando houver menos carga.

**Resposta:** ORA-00031 passa a ser tratado como cancelamento aceite (sessão marcada para terminar). Shrink com "Quando": agora em segundo plano (volta a Execuções com a execução destacada) ou agendado (data/hora, atalhos para a noite; estado AGENDADO corrido pelo agente; filtro Agendadas; cancelável). Cartão Tarefas na Home: em curso, na fila, agendadas e próximas tarefas do Agendamento.

## 29. Limpar notificações e perfil de administrador

**Pergunta:**
> não consigo limpar as notificações, como podemos ver na imagem, no perfil deviamos ter a opção administrador, ou seja esse perfil com a opção administradores conseguem ter acesso total.

**Resposta:** Sininho com "Limpar todas" e ✕ em cada notificação (o "Marcar como lidas" só tirava o destaque). Perfil com a opção "Administrador — acesso total" (todas as permissões, módulos e bases de dados, Armazenamento); só um administrador a pode dar ou retirar.

## 30. Campos do utilizador e correspondência de atributos LDAP/AD

**Pergunta:**
> quero que no utilizador tenha os seguintes campos e com o match para no caso da AD, Atributo utilizador * Atributo nome próprio Atributo último nome Atributo e-mail

**Resposta:** Utilizador com Nome próprio, Último nome e E-mail; na configuração LDAP, "Correspondência de atributos": Atributo utilizador (obrigatório; AD sAMAccountName), nome próprio (givenName), último nome (sn) e e-mail (mail), mais um filtro de objetos opcional. Preenchidos no login, na criação automática e com o botão "Preencher a partir do LDAP / AD".

## 31. Erro TLS (SMTP)

**Pergunta:**
> ERRO: ValueError: server_hostname cannot be an empty string or start with a leading dot.

**Resposta:** O TLS (STARTTLS/LDAPS) passa a receber o nome do servidor (SNI) de cada host; os servidores aceitam `ldaps://host:porta`, `[IPv6]:porta` e separação por vírgulas, ponto e vírgula ou espaços; nomes inválidos dão uma mensagem clara.

**Pergunta:**
> isto era no smtp

**Resposta:** No SMTP a ligação era criada sem o nome do servidor (smtplib usa-o no STARTTLS/SSL): passa a ser indicado antes de ligar; o campo Servidor aceita `smtp://host:porta` (fica só o nome). A alteração no LDAP mantém-se (também indica o nome ao TLS).

## 32. Módulos ativos/inativos, agente Oracle, Sistema → Agentes e Análise, Configuração → Setup

**Pergunta:**
> na configuração e modulo, quero que tenha uma aba com os ativos e outra com inativos, podemos também a parte do ativar ou desativar, ser como no projeto workspace portal. O processo de shrink demora muito, cerca de 20 minutos em alguns casos, podemos melhorar o agente-portal, ou criar um agente-oracle que fica responsavel pelos serviços oracle, podemos também criar uma aba em configuração, Agentes, onde podemos ver os agentes cada um por aba, onde podemos alterar alguns parametros como aumentar os recursos deles, podemos também os processos associados a cada agente, os recuros que estão a usar, e ver o grafico, de 1 hora, 5 horas, 24 horas, 1 semana, 1 mês, melhor a aba Agentes , pode ficar em sistema numa aba Agentes, depois podemos ter uma Aba com analise, onde temos os recursos, processos que estão a ser usados pelo portal, agentes. Podemos no configuração, ter uma aba setup, como no projeto portal. Na aba analise e Agentes podemos ter associados graficos de performance, recursos usados, podendo escolher uma data ou 24 horas atras, 1 semana.

**Resposta:** Configuração → Módulos com abas Ativos/Inativos, vista Cartões (2/3/4 colunas) ou Lista e interruptor que muda sem recarregar. Novo serviço `<nome>-agente-oracle` só para as execuções Oracle (concorrência configurável) e campo **Paralelismo do MOVE/REBUILD** no Shrink (`PARALLEL n`, grau original reposto no fim) — o tempo do shrink está na BD, não no agente. Sistema → **Agentes** (uma aba por agente: estado, parâmetros, recursos do serviço com o comando `--agente-recursos`, processos, gráficos 1 h/5 h/24 h/1 semana/1 mês ou a partir de uma data) e → **Análise** (CPU e memória por componente e do servidor, processos). Métricas recolhidas pelo agente do portal (`psutil`, 35 dias). Configuração → **Setup**: identidade, armazenamento, ficheiros/chaves com dono, modo e comando de correção, serviços, sudoers, runtime, configuração do instalador e comandos úteis.

**Pergunta:**
> `--lista` mostra AG.ORACLE "-" e `--agente-recursos dba-portal agente-oracle ...` dá "o servico dba-portal-agente-oracle nao existe".

**Resposta:** O `--atualizar` substitui o `instalar.sh` no disco, mas o bash continuava a correr a versão antiga (em memória), que ainda não criava o agente Oracle. Corrigir já: `sudo /opt/dba-portal/instalar.sh --reaplicar dba-portal`. Daqui em diante, se o `--atualizar` trouxer um `instalar.sh` diferente, o script continua automaticamente com a versão nova (`--reaplicar`).

## 33. Atualização automática nos Agentes

**Pergunta:**
> quero que nos agentes os dados e informação tenha um refresh, que podemos indicar ou alterar como no home

**Resposta:** Sistema → Agentes e → Análise têm, como a Home, o seletor "Atualizar: manual / 5 s / 10 s / 30 s / 1 min / 5 min" (por omissão 10 s, guardado no browser), a hora da última atualização e o botão ↻. Atualiza estado, recursos, execuções em curso, processos e gráficos (períodos relativos); o formulário de parâmetros não é tocado.

## 34. Pedidos às bases de dados pelo agente cliente

**Pergunta:**
> se calhar a melhor opção será instalar o agente cliente no cliente e ser esse agente a fazer todos os pedidos, ou seja por exemplo se eu for a uma base de dados e se esse servidor tem o agente cliente instalado e a correr, todos os pedidos podem ser feito ao agente, sendo que neste caso o portal fica aguardar pela reposta, tira mais carga ao portal, por exemplo no shrink, também pode ser igual ele manda para o agente-cliente e fica aguardar, sendo que o ouput pode continuar na mesma. podes preparar tudo e depois podemos instalar o agenten num servidor e fazer o primeiro teste, se os dados estiverem a ser feitos pelo agente cliente, deve indicar no servidor ou base de dados essa informação, se não é o portal que está a tratar do processo.

**Resposta:** Agente cliente 1.2.0 com serviço Oracle opcional (`setup.sh --oracle`: venv com python-oracledb, HTTPS na porta 8795, certificado próprio confirmado pela impressão digital, pedidos assinados com o token, hora e nonce). Cada base de dados pode ser associada a um agente (Automático ou Obrigatório): o portal envia os pedidos (estado, inspeção, tablespaces, teste, cancelar) e espera a resposta; o shrink corre no agente, com progresso e output em direto, e continua se o portal reiniciar. "Pedidos feitos por" nas Bases de dados e nas Definições, "Executado por" em cada execução, cartão "Serviço Oracle" no cliente. Sem agente disponível (modo Automático), o portal trata do pedido e mostra o motivo. Corrigido também: a coluna `jobs.runner` era curta para `agente_oracle` em PostgreSQL/MariaDB, e o `setup.sh` perdia o `--id` ao ler o `/etc/os-release`.

## 35. Clientes → Ferramentas, comando agente-portal e authorized_keys

**Pergunta:**
> devia ser possivel ter o setup para download, em clientes, ferramentas ou em configuração, ferramentas., agente-portal estado — bash: agente-portal: command not found — e a chave do portal no <utilizador> no autorized_key ficou na mesma linha do ulltimo, não fez quebra de linha.

**Resposta:** Nova aba Clientes → Ferramentas (download do setup.sh, agente-portal.py e chave.pub, com versão e SHA-256, e comandos prontos a copiar). O setup.sh cria o comando `agente-portal` em /usr/local/bin e /usr/bin (o sudo do RHEL não procura em /usr/local/bin) e usa o Python do venv quando existe. Ao acrescentar a chave, garante a quebra de linha e separa chaves coladas na mesma linha (cópia `.antes-setup`). Sem acesso ao portal, usa o agente-portal.py que estiver ao lado do setup.sh.

## 36. Cliente com abas, atualização automática, bases de dados e Zabbix

**Pergunta:**
> quando estamos no cliente devia ter um refresh como no home, também podiamos associar o cliente a uma base de dados, que é o caso de alguns clientes, mostrado para isso no clientes a informação da base de dados. Também podiamos ter no cliente uma aba com processos, logs, informação sobre o sistema, outra aba com atualizações pendentes, se tiver o zabbix ativo nesse cliente para ir buscar os dados.

**Resposta:** Página do cliente com atualização automática (como a Home) e abas Resumo, Processos, Sistema, Logs, Atualizações, Bases de dados e Zabbix. Agente 1.3.0 envia processos (cada sincronização), sistema (1 h), logs de avisos/erros (5 min) e atualizações pendentes com segurança e reinício (6 h, em segundo plano); botão "Pedir dados agora". Bases de dados: estado do módulo Oracle e associar/desassociar no próprio cliente; a lista de clientes mostra as BDs e as atualizações pendentes. Zabbix: com a API configurada, anfitrião, problemas e itens principais.

## 37. Atualizar o agente pelo portal, ficheiros de log por cliente, Ferramentas recolhíveis

**Pergunta:**
> podiamos conseguir fazer o upgrade do agente pelo portal atraves do agente. Nos logs, podemos ter uma opção de scan, em que ele vai buscar todos os logs do /var/log e nos depois podemos escolher qual queremos que sejam colocados, também podemos colocar os logs de forma manual, colocado o path, nome do ficheiro, descrição e uma tag para identificar, no ferramentas as opção estar minizamizada para não ocupar tanto a janela no browser.

**Resposta:** Agente 1.4.0: atualiza-se pelo portal (botão no cliente ou "Atualizar todos" em Ferramentas): descarrega o agente do portal, confirma o SHA-256 indicado pelo portal, guarda a versão anterior, substitui-se e reinicia (adia com shrink a correr). Logs: coluna com as fontes por tag; ficheiros seguidos adicionados à mão (pasta, ficheiro com * e ?, descrição, tag, linhas) ou por scan de /var/log com escolha dos ficheiros; o agente envia as últimas linhas a cada 5 min e só lê dentro de LOGS_PASTAS. Ferramentas com secções recolhíveis (estado guardado no browser) e a lista de versões dos agentes.

## 38. Aba Agente, atualização com output em direto, Agendamento → Eventos

**Pergunta:**
> mas devia dar para selecionar o cliente que queremos, no cliente tb pode ter uma aba com o nome de Agente, qual a versão e se existe uma atualização. Quer pelo cliente ou pela aba ferramenta devia dar o output do que está a fazer. No agendamento em configuração, devemos ter a aba geral com o normal e depois outra aba com eventos, onde podemos ver os eventos. Ao clicar em atualizar devia ser instantaneo e mostrar o output.

**Resposta:** Agente 1.5.0 consulta o portal a cada 5 s (sem portas abertas no servidor): atualizar e pedir dados passam a ser imediatos. Comandos com output guardado e mostrado em direto (descarregar, SHA-256, sintaxe, versão anterior guardada, substituição, reinício, "agente a correr"). Aba Agente no cliente (versões, botão, histórico de comandos com output); Ferramentas com seleção dos clientes e output de cada um. Configuração → Agendamento com abas Geral e Eventos (filtros por tarefa, estado, período e texto; duração e mensagem completa).

## 39. Atualização "pedida" que nunca termina

**Pergunta:**
> ainda está a correr o atualziar, pedida 11-24 10:31

**Resposta:** O pedido da versão anterior do portal era só uma marca, sem prazo: com um agente anterior à 1.4.0 (que não conhece o pedido) ficava "pedida" para sempre. Agora: pedidos sem resposta passam a erro ao fim de 15 min com o motivo; agentes anteriores à 1.4.0 recebem logo a explicação (atualizar uma vez com o setup.sh); botão para cancelar (aba Agente e Ferramentas, com "ver" o output); as marcas antigas sem comando são limpas automaticamente.

## 40. Clientes → Pedidos (todos os pedidos aos agentes, com estado e output)

**Pergunta:**
> nas interaccções com o cliente, diz que fica agendado no proximo contato, contudo não tenho como ver , no clientes, deve haver uma aba com o nome sujetivo onde ficam todos os pedidos, o pedido devia ser feito de forma automatica e logo, podemos depois nessa aba ver o estado do pedido e ao clicar no pedido ver o output.

**Resposta:** Nova aba Clientes → Pedidos: cada interação (pedir dados, scan de /var/log, ficheiro de log novo, atualizar o agente) é um pedido com estado e output, filtros e atualização automática; clicar mostra o output em direto. No cliente, o pedido é enviado logo e o output aparece na página; fecha quando os dados chegam. Agente 1.5.1: o pedido de logs também envia já as linhas de um ficheiro acabado de adicionar. Testado: atualizar 10 s, processos 4 s, atualizações 9 s, scan 4 s, ficheiro novo 4 s.

## 41. Fechar o output, editar/apagar logs, pastas, visualizador de logs, filtros nas atualizações

**Pergunta:**
> já corre, mas quando vejo depois o output de ter feito, não consigo fechar o output, devia ter uma cruz ou algum para voltar a fechar o output. Nos logs devia dar para editar os logs ou mesmo apagar. Sendo que podemos também colocar uma pasta com ficheiros de log e depois abrir um ficheiro, nesse sentido ao abrir, quero que tenha uma interface boa, tipo um editor onde vemos as linhas, e o texto todo formatado, podendo detetar o tipo de log. Podemos também fazer o download. A aba atualizar diz que vai fazer daqui a 6 horas novamente pelo agente, onde isso é configurado e podemos ter filtro por tipo de atualização, Repositório e fazer no maximo 20 por pagina

**Resposta:** ✕ nos painéis de output. Logs: editar e apagar; seguir pastas (o agente lista os ficheiros). Visualizador tipo editor: números de linha, tipo de log detetado (Oracle alert, listener, journald, syslog, Apache/Nginx, auditd, dnf, JSON…), realce, níveis com filtros, pesquisa com navegação, ir para a linha, quebra de linha e download (até 10 MB). Atualizações: filtros por tipo (segurança/correção/melhoria, do dnf updateinfo) e repositório, 20 por página. Os intervalos (as 6 h) configuram-se em Gerir clientes → Recolha de dados. Agente 1.6.0 (ler ficheiros, pastas, intervalos, tipo de atualização).

## 42. Shrink: "Portal (sem ligação a <servidor>:8795 ([Errno 113] No route to host))"

**Pergunta:**
> no shrink está a dar este aviso, Portal sem ligação a <servidor>:8795 ([Errno 113] No route to host) · agente Oracle

**Resposta:** A base de dados está associada ao agente (modo Automático) mas a firewall do servidor da BD rejeita a porta 8795; o shrink foi feito pelo portal (como antes). Abrir a porta só para o IP do portal (firewall-cmd rich-rule ou setup.sh --oracle --abrir-firewall --origens) ou desligar o agente nessa BD. A mensagem passa a indicar a causa provável (113 firewall, 111 serviço parado, timeout rede).

**Pergunta:**
> bash setup-agente.sh --oracle --origens <IP do portal> e isto bash setup-agente.sh --abrir-firewall e nada

**Resposta:** O --abrir-firewall só atuava no mesmo comando que o --oracle. Agora também funciona sozinho (usa a porta e as origens já guardadas no agente), abre a regra em todas as zonas ativas do firewalld (não só na predefinida) e confirma-a, mostrando a regra.
