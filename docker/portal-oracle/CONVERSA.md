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

