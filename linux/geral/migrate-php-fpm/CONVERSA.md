# Conversa: migrate-php-fpm

Chat de 17 de novembro de 2026.

---

## 1. Pedido

**Pergunta** (domínios substituídos por exemplos):
> O objetivo é migrar o mod_php do apache para php-fpm. Para isso temos que validar os virtualhosts que existem no
> sites-available e as mesmas referências no sites-enabled, sendo que apenas os que existam no sites-enabled serão
> migrados para o php-fpm pool config. No script podemos indicar o caminho dos sites-enabled e sites-available (por
> defeito é igual em todos os servidores).
>
> É para confirmar um a um: diz que existe no sites-enabled o portal XXXXX, se queremos migrar, faz um backup do
> ficheiro na pasta sites-available (todas as alterações são no sites-available). Mostra o que é necessário retirar,
> por exemplo os php_admin, tudo o que com o fim do mod_php vai dar erro; esses parâmetros têm de ser colocados com a
> sintaxe correta no novo ficheiro /etc/php/X.X/fpm/pool.d/nome.conf. O nome tem a ver com o ServerName ou alias, não
> podendo haver dois; aparece o nome por defeito e podemos colocar um novo. Também podemos indicar o local do pool.d,
> por defeito o da versão do PHP usada.
>
> Existe sempre um parâmetro incluído: `php_admin_value[include_path] = ".:/usr/share/php:/var/nome/conn:/var/nome/codelib:/var/nome/internet/000-config:/var/nome/internet/999-error"`,
> mudando apenas a pasta conforme o domínio (ex. exemplo1.pt → /var/exemplo1, exemplo2.pt → /var/www). Cada parâmetro
> do sites-available fica igual por defeito, respeitando a sintaxe, que é diferente
> (`php_value include_path "..."` no Apache, `php_admin_value[include_path] = "..."` no php-fpm). Podem aparecer
> variáveis de env (`env[TNS_ADMIN]`, `env[LD_LIBRARY_PATH]`). Validar o DocumentRoot de cada virtualhost.
>
> Em cada virtualhost, nas portas 80 e 443, colocar o `SetHandler "proxy:unix:/run/php/phpversão-fpm-nome.sock|fcgi://localhost/"`,
> um por virtualhost. Também em modo AUTO: faz tudo e em cada virtualhost mostra o que foi feito antes de avançar
> (Enter), podendo abortar. No final mostra um resumo e guarda cada operação num log (onde está o script). Faz a
> validação das configurações (com a linha em caso de erro de sintaxe), pergunta se faz reload do Apache e do php-fpm
> (do php-fpm sempre que acaba cada virtualhost). No início valida se o php-fpm está instalado e pergunta se instala
> na mesma versão do PHP. No final pode desativar o mod_php e indicar o que falta para o desinstalar (Ubuntu).
>
> Os virtualhosts do sites-available que não estejam no sites-enabled ficam com um marcador `.delete`. No início
> aparece o menu: validar virtualhosts, mostrar available/enabled para marcar `.delete`, fazer a migração.
> Se não for root indica que tem de ser com sudo. Commit no repositório, pasta migrate-php-fpm.

**Resposta:** foi criado `linux/geral/migrate-php-fpm`:

| Pedido | Implementação |
|---|---|
| Caminhos do sites-available / sites-enabled | `-a` / `-e` (defeito `/etc/apache2/...`) ou `migrate-php-fpm.conf` |
| Validar virtualhosts | Opção 1: tabela com ativo/inativo, ServerName, portas, DocumentRoot (e se existe), n.º de diretivas e estado |
| Só os do sites-enabled; alterações no sites-available | A migração percorre o sites-enabled e altera o ficheiro para onde o link aponta |
| Um a um com confirmação | "Existe no sites-enabled o portal X. Migrar? [s/N/a=abortar]" |
| Backup | `<ficheiro>.bak-<data>` no sites-available |
| Mostrar o que tem de sair | Tabela linha / Apache / php-fpm; as linhas `php_*` ficam comentadas com `#MIGRADO-FPM#` |
| Sintaxe do php-fpm | `php_value x y` → `php_admin_value[x] = y`, `php_flag` → `php_admin_flag` (`--manter-tipo` para não converter) |
| Parâmetro fica igual por defeito | Enter = manter, `e` = editar, `r` = remover |
| Nome do pool pelo ServerName/alias, único | Nome por defeito do ServerName; se já existir propõe outro; no modo um a um pode mudar-se |
| Local do pool.d | `-d`; por defeito `/etc/php/<versão do mod_php>/fpm/pool.d` |
| include_path sempre incluído | Base `/var/<pasta>` tirada do DocumentRoot; se o vhost já tinha, fica o dele (`p` escolhe o padrão) |
| Variáveis de env | `SetEnv` do vhost e `export` do `/etc/apache2/envvars` → `env[...]` |
| SetHandler em 80 e 443 | Dentro de `<FilesMatch ".+\.ph(ar|p|tml)$">` em cada `<VirtualHost>`, um socket por site |
| Modo AUTO | Opção 4: mostra o que foi feito em cada vhost e espera Enter / `a` |
| Resumo e log | Tabela final; log com perguntas, respostas, comandos, diff e pool em `logs/` |
| Validação com a linha do erro | `apache2ctl configtest` e `php-fpmX.Y -t`; mostra o ficheiro e a linha; oferece reverter |
| Reload | php-fpm após cada vhost (confirma o socket); Apache + php-fpm no fim |
| php-fpm instalado? | Se não, pergunta e instala `phpX.Y-fpm` |
| Desativar mod_php | Opção 7: bloqueia com diretivas pendentes, `a2dismod`, `mpm_event`, restart e passos para desinstalar |
| Marcador `.delete` | Opção 2: renomeia para `<ficheiro>.delete` (todos ou um a um) |
| sudo | Sem root pára e mostra o comando com `sudo` |

Extras: `.htaccess` com `php_value` convertidos para `.user.ini`, comparação `php.ini` e extensões apache2 vs fpm,
`--dry-run`, deteção de vhosts já migrados e aviso de sites sem php-fpm antes de desativar o mod_php.

---

## 2. Verbose e ficheiro env

**Pergunta:**
> Quero acrescentar a opção verbose, ou seja não faz nada apenas simula, todos os parâmetros ficam registados num
> ficheiro de env, podemos sempre apagar o ficheiro ou dizer para criar um novo env.

**Resposta:**

| Pedido | Implementação |
|---|---|
| Verbose só simula | `-v` / `--verbose` implica `--dry-run`; mostra diff, pool, tabela "o que seria feito" e os valores `[env]` usados |
| Parâmetros num ficheiro env | `migrate-php-fpm.env` (ou `--env`): predefinições globais + por virtualhost `MIGRAR`, `POOL`, `P_<parâmetro>`, `ENV_<variável>`, `HTACCESS` |
| Reutilizar | Se o env existir pergunta `usar / criar novo / apagar / ignorar`; com usar, os valores são o defeito no modo um a um e as decisões no AUTO |
| Apagar ou criar novo | `--apagar-env`, `--novo-env` (o anterior fica em `.bak-<data>`), `--usar-env`, `--sem-env` e menu 8 (ver / usar / novo / apagar) |

