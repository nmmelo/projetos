# migrate-php-fpm

Script Bash interativo para migrar o PHP do Apache de **mod_php** para **php-fpm** em Ubuntu,
virtualhost a virtualhost, com backup, validação, reload e log de tudo o que é feito.

## Ficheiros

| Ficheiro | Descrição |
|---|---|
| `migrate-php-fpm.sh` | O script |
| `migrate-php-fpm.conf.example` | Predefinições (copiar para `migrate-php-fpm.conf`) |
| `logs/` | Criada na 1.ª execução: um log por execução |
| `migrate-php-fpm.env` | Criado pelo `--verbose`: todos os parâmetros e decisões por virtualhost |
| `CONVERSA.md` | Resumo do pedido que deu origem ao projeto |

## Utilização

```bash
sudo ./migrate-php-fpm.sh                 # menu
sudo ./migrate-php-fpm.sh --dry-run       # simulação: mostra tudo, não altera nada
sudo ./migrate-php-fpm.sh -m auto         # migração AUTO sem passar pelo menu
sudo ./migrate-php-fpm.sh -v              # verbose: simula e grava as decisões no env
```

Sem `sudo`/root o script pára e indica o comando correto.

| Opção | Descrição | Por defeito |
|---|---|---|
| `-a, --available DIR` | Pasta sites-available | `/etc/apache2/sites-available` |
| `-e, --enabled DIR` | Pasta sites-enabled | `/etc/apache2/sites-enabled` |
| `-p, --php X.Y` | Versão do PHP | A do mod_php ativo (`mods-enabled/phpX.Y.load`), senão `php -v` |
| `-d, --pool-dir DIR` | Pasta dos pools | `/etc/php/X.Y/fpm/pool.d` |
| `-l, --log FICHEIRO` | Ficheiro de log | `logs/migrate-php-fpm_<data>.log` |
| `-m, --modo MODO` | Sem menu: `validar`, `delete`, `migrar`, `auto`, `ini`, `testar`, `desativar` | menu |
| `--manter-tipo` | `php_value` fica `php_value[...]` | passa a `php_admin_value[...]` |
| `--dry-run` | Simulação | — |
| `-v, --verbose` | Simulação detalhada (não altera nada) e grava todos os parâmetros e decisões no ficheiro env | — |
| `--env FICHEIRO` | Ficheiro env | `migrate-php-fpm.env` na pasta do script |
| `--usar-env` / `--novo-env` / `--apagar-env` / `--sem-env` | O que fazer ao env existente sem perguntar | pergunta |

## Menu

| Opção | O que faz |
|---|---|
| 1. Validar virtualhosts | Tabela com todos os ficheiros do sites-available: se está no sites-enabled, ServerName, portas, DocumentRoot (e se existe), n.º de diretivas `php_*` e estado (`mod_php`, `php-fpm`, `sem DocumentRoot`). Avisa links quebrados no sites-enabled |
| 2. Marcar não usados | Lista os que estão no sites-available e não no sites-enabled e renomeia-os para `<ficheiro>.delete` (todos, um a um ou nenhum). Mostra também os já marcados |
| 3. Migrar (um a um) | Para cada site do sites-enabled pergunta se quer migrar, mostra cada parâmetro e o novo nome do pool, pré-visualiza as alterações e só aplica com confirmação |
| 4. Migrar (AUTO) | Faz tudo com os valores por defeito; depois de cada virtualhost mostra o que foi feito e espera Enter (ou `a` para abortar) |
| 5. php.ini e extensões | Compara `apache2/php.ini` com `fpm/php.ini` e as extensões ativas nos dois `conf.d` |
| 6. Validar | `apache2ctl configtest` + `php-fpmX.Y -t`, com o ficheiro e a linha do erro |
| 7. Desativar mod_php | Confirma que não fica nada pendente, `a2dismod phpX.Y`, troca `mpm_prefork` → `mpm_event`, valida, restart e indica o que falta para desinstalar |
| 8. Ficheiro env | Ver o conteúdo, passar a usar, criar novo (o anterior fica em `.bak-<data>`) ou apagar |

## Verbose e ficheiro env

`-v` / `--verbose` faz o mesmo percurso da migração (um a um ou AUTO) **sem alterar nada**: mostra o diff do vhost,
o pool, a tabela do que seria feito (backup, pool, error_log, `.htaccess`, validação, reload) e as linhas `[env]` com os
valores lidos do env. Todas as predefinições e as decisões de cada virtualhost ficam em `migrate-php-fpm.env`:

```ini
# ---- globais
PHP_VER="8.4"
POOL_DIR="/etc/php/8.4/fpm/pool.d"
PM_MAX_CHILDREN="20"
...
# ---- portal.exemplo.pt  (/etc/apache2/sites-available/portal.conf)
VH_portal_exemplo_pt__MIGRAR="s"
VH_portal_exemplo_pt__POOL="portal"
VH_portal_exemplo_pt__P_include_path="manter"
VH_portal_exemplo_pt__P_memory_limit="valor:512M"
VH_portal_exemplo_pt__P_upload_max_filesize="remover"
VH_portal_exemplo_pt__ENV_TNS_ADMIN="s"
VH_portal_exemplo_pt__HTACCESS="s"
```

| Chave | Valores |
|---|---|
| `MIGRAR` | `s` / `n` |
| `POOL` | nome do pool (ficheiro, socket e error_log) |
| `P_<parâmetro>` | `manter`, `remover`, `padrao` (só `include_path`), `valor:<novo valor>` |
| `ENV_<variável>` | `s` / `n` (colocar `env[...]` no pool) |
| `HTACCESS` | `s` / `n` (converter para `.user.ini`) |

Na execução seguinte, se o env existir, o script pergunta: `u=usar | n=criar novo | a=apagar | i=ignorar`.
Com **usar**, os valores do env passam a ser os valores por defeito no modo um a um (Enter aceita-os) e são as
decisões aplicadas no modo AUTO. As opções da linha de comando têm prioridade sobre o env.

Fluxo típico:

```bash
sudo ./migrate-php-fpm.sh -m migrar -v      # 1. simula um a um e grava as decisões
vi migrate-php-fpm.env                      # 2. (opcional) rever / corrigir à mão
sudo ./migrate-php-fpm.sh -m auto --usar-env   # 3. migra a sério com essas decisões
```

O ficheiro fica com `chmod 600`; pode apagá-lo a qualquer momento (`rm`, `--apagar-env` ou menu 8) ou pedir um novo
(`--novo-env`).

## O que acontece em cada virtualhost

1. **Pré-requisitos** (uma vez): Apache, php-fpm da mesma versão do PHP (se não existir pergunta se instala `phpX.Y-fpm`),
   módulos `proxy`, `proxy_fcgi` e `setenvif`, pasta `pool.d` e o `include=` do `php-fpm.conf`.
2. Só são migrados os sites que estão no **sites-enabled**; as alterações são feitas no ficheiro do **sites-available**.
3. Os blocos `<VirtualHost>` do mesmo ServerName (80 e 443) dão **um pool**.
4. **Backup** do ficheiro: `sites-available/<ficheiro>.bak-<data>`.
5. As diretivas `php_value`, `php_flag`, `php_admin_value` e `php_admin_flag` (também dentro de `<Directory>`)
   são comentadas com `#MIGRADO-FPM#` e passam para o pool com a sintaxe do php-fpm:

   | Apache (mod_php) | php-fpm (pool) |
   |---|---|
   | `php_value include_path ".:/usr/share/php:..."` | `php_admin_value[include_path] = ".:/usr/share/php:..."` |
   | `php_value memory_limit 256M` | `php_admin_value[memory_limit] = 256M` |
   | `php_flag display_errors Off` | `php_admin_flag[display_errors] = off` |
   | `SetEnv TNS_ADMIN "/caminho"` | `env[TNS_ADMIN] = "/caminho"` |

   No modo um a um cada parâmetro aceita: Enter = fica igual, `e` = editar, `r` = remover, `a` = abortar.
6. Em cada `<VirtualHost>` (80 e 443) é acrescentado antes de `</VirtualHost>`:

   ```apache
   # migrate-php-fpm <data>: PHP pelo php-fpm (pool portal)
   <FilesMatch ".+\.ph(ar|p|tml)$">
       SetHandler "proxy:unix:/run/php/php8.4-fpm-portal.sock|fcgi://localhost/"
   </FilesMatch>
   <Files ".user.ini">
       Require all denied
   </Files>
   ```

   O `SetHandler` fica dentro de `<FilesMatch>` para só os `.php` irem para o php-fpm (imagens, css e js continuam a ser servidos pelo Apache).
7. **Pool** em `/etc/php/X.Y/fpm/pool.d/<nome>.conf`:
   - `<nome>` vem do ServerName (sem `www.` e só o primeiro nome: `portal.exemplo.pt` → `portal`) ou do 1.º ServerAlias;
   - não pode repetir (ficheiro ou socket já existente): o script propõe outro e no modo um a um pode indicar-se um novo;
   - secção `[ServerName]`, socket `/run/php/phpX.Y-fpm-<nome>.sock`, `error_log` em `/var/log/<nome>.php.error.log`
     (o ficheiro é criado com dono `www-data`);
   - `include_path` padrão, sempre incluído, com a base tirada do DocumentRoot (`/var/<pasta>`):

     ```ini
     php_admin_value[include_path] = ".:/usr/share/php:/var/<pasta>/conn:/var/<pasta>/codelib:/var/<pasta>/internet/000-config:/var/<pasta>/internet/999-error"
     ```

     Se o vhost já tinha `include_path`, por defeito fica o do vhost (no modo um a um `p` escolhe o padrão).
   - variáveis de ambiente: `SetEnv` do vhost e `export` do `/etc/apache2/envvars` (Oracle: `TNS_ADMIN`, `LD_LIBRARY_PATH`, `ORACLE_*`, `NLS_*`).
8. **`.htaccess`** no DocumentRoot com `php_value`/`php_flag` (sem mod_php dão erro 500): são convertidos para
   `.user.ini` na mesma pasta (o php-fpm lê-o e mantém o efeito por pasta), com backup e as linhas comentadas.
9. **Validação** do Apache e do php-fpm. Se houver erro mostra o ficheiro e a linha (com as linhas à volta) e pergunta
   se reverte esse virtualhost (repõe o backup e apaga o pool).
10. **Reload do php-fpm** e confirmação de que o socket existe.

No fim: resumo em tabela, validação geral, pergunta se faz reload do Apache e do php-fpm e se desativa já o mod_php.

### Exemplo de pool gerado

```ini
[portal.exemplo.pt]

user = www-data
group = www-data

listen = /run/php/php8.4-fpm-portal.sock
listen.owner = www-data
listen.group = www-data
listen.mode = 0660

pm = dynamic
pm.max_children = 20
pm.start_servers = 4
pm.min_spare_servers = 2
pm.max_spare_servers = 6
pm.max_requests = 500

request_terminate_timeout = 300
php_admin_flag[log_errors] = on
php_admin_value[error_log] = /var/log/portal.php.error.log
php_admin_value[include_path] = ".:/usr/share/php:/var/portal/conn:/var/portal/codelib:/var/portal/internet/000-config:/var/portal/internet/999-error"
; Apache linha 15: php_value memory_limit 256M
php_admin_value[memory_limit] = 256M
; SetEnv linha 12
env[TNS_ADMIN] = "/usr/lib/oracle/21/client64/lib/network/admin"
```

## Desativar o mod_php

A opção 7 recusa avançar se ainda houver diretivas `php_*` ativas (o Apache não arrancava) e avisa os sites ativos
sem php-fpm (sem mod_php o código `.php` seria servido como texto); nesse caso pode ativar a conf `phpX.Y-fpm`,
que envia esses sites para o pool `www`. Depois mostra o que falta:

| Passo | Comando |
|---|---|
| Testar os sites | `curl -I https://<site>/` e `tail -f /var/log/<nome>.php.error.log` |
| Dependências | `apt-cache rdepends --installed libapache2-mod-phpX.Y` |
| Remover o mod_php | `sudo apt purge libapache2-mod-phpX.Y libapache2-mod-php` |
| Limpar | `sudo apt autoremove --purge` |
| Backups | apagar `sites-available/*.bak-*` e `*.delete` quando tudo estiver validado |

## Notas

| Assunto | Nota |
|---|---|
| `php_admin_value` vs `php_value` | Com `php_admin_*` o código não consegue alterar o valor com `ini_set()`. Se alguma aplicação o fizer (ex. `memory_limit`), use `--manter-tipo` ou edite o parâmetro |
| `php.ini` | Com o php-fpm passa a valer `/etc/php/X.Y/fpm/php.ini` e não o `apache2/php.ini`: usar a opção 5 antes de migrar |
| Oracle (oci8) | `env[LD_LIBRARY_PATH]` no pool não chega para o processo master carregar o `oci8.so`: usar `/etc/ld.so.conf.d/oracle.conf` + `ldconfig` ou um override systemd do `phpX.Y-fpm` |
| `.user.ini` | Lido de 5 em 5 minutos (`user_ini.cache_ttl`); o bloco `<Files ".user.ini">` impede que seja descarregado |
| Repetir | Um vhost que já tem `SetHandler "proxy:unix:..."` é marcado como já migrado e ignorado |
| Log | Cada pergunta, resposta, comando, diff do vhost e conteúdo do pool fica em `logs/` |

## Teste

Testado num ambiente simulado (pastas `etc/apache2` e `etc/php` próprias via `migrate-php-fpm.conf`, com
`apache2ctl`, `php-fpm8.3`, `a2enmod`/`a2dismod` e serviços substituídos por scripts de teste): migração um a um e AUTO,
vhost 80+443 com `php_value` dentro de `<Directory>`, `.htaccess` → `.user.ini`, nome de pool repetido,
erro de sintaxe com indicação da linha e reversão, simulação (`--dry-run`), marcação `.delete`, comparação de `php.ini`,
desativação do mod_php bloqueada com diretivas pendentes e execução sem root. Verbose: gravação do env um a um e AUTO,
migração AUTO real com `--usar-env` (pool, valor editado, parâmetro removido, site ignorado e `env[...]` aplicados como
gravados), `--novo-env` com backup do anterior e apagar pelo menu 8.
