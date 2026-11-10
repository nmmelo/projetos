# mount-islive

Valida, de X em X minutos, se os mounts do `/etc/fstab` (NFS, CIFS, etc.) estão **realmente vivos**.
Por vezes, numa falha de rede, o mount continua a aparecer como montado mas deixa de responder.
O script escreve a data atual num ficheiro de teste dentro do mount (na raiz ou numa **subpasta à escolha**,
para os casos em que não há permissão de escrita na raiz do mount). Se não conseguir, **refaz o mount**
e, quando o mount volta a funcionar, envia um **email por SMTP** com a hora da falha e a hora do teste com sucesso.

## Ficheiros

| Ficheiro | Descrição |
|---|---|
| [`setup.sh`](setup.sh) | Assistente de configuração: número de mounts, cada mount e a pasta do ficheiro de teste, intervalo, SMTP (DNS/IP, porta, TLS, autenticação), emails e agendamento (systemd, cron ou manual). Grava `config/.env` |
| [`bin/check_mount.sh`](bin/check_mount.sh) | Teste de escrita, remount e notificações (corre pelo agendamento) |
| [`bin/send_mail.py`](bin/send_mail.py) | Envio de email por SMTP (Python 3, `smtplib`: sem TLS, STARTTLS ou SSL, com ou sem autenticação) |
| [`config/.env.example`](config/.env.example) | Exemplo da configuração |
| [`CONVERSA.md`](CONVERSA.md) | Resumo do chat que deu origem ao projeto |

Criados na instalação, não versionados (ver `.gitignore`):

| Ficheiro | Conteúdo |
|---|---|
| `config/.env` | Configuração ativa (chmod 600) |
| `config/smtp.key` | Chave para cifrar a password SMTP (chmod 600) |
| `state/` | Estado de cada mount: hora da falha, último OK, tentativas |
| `logs/mount-islive.log` | Histórico dos testes, falhas, remounts e emails |

## Como funciona

```
 timer systemd / cron (a cada INTERVAL_MIN)
        │
        ▼
 escreve "data hora" em <pasta de teste>/teste.txt e lê de volta  (timeout TOUCH_TIMEOUT)
 (a pasta de teste é o mount ou uma subpasta dele; o remount é feito sempre ao mount)
        │
   ┌────┴─────┐
  OK        FALHA ── guarda hora da falha ── umount -f -l ── mount <mount> (fstab)
   │                                                       │
   │                                          ┌────────────┴────────────┐
   │                                     teste OK                  continua em baixo
   │                                          │                         │
   │                          email "recuperado - ON"        email "ALERTA" (1 só vez)
   │                          (falha + hora do sucesso)      tenta de novo no próximo ciclo
   ▼
 (se havia uma falha anterior → email "recuperado - ON")
```

| Situação detetada | Motivo indicado no email |
|---|---|
| O caminho deixou de estar montado | `não está montado` |
| A escrita ou a leitura fica pendurada (servidor de rede sem resposta) | `sem resposta em Ns (timeout)` |
| A pasta de teste não existe no mount | `a pasta ... não existe no mount` |
| Erro ao escrever | `não foi possível escrever em <pasta>/teste.txt` |
| O valor lido não é o que foi escrito | `o conteúdo lido ... não corresponde` |

Um mount pendurado não bloqueia o servidor: cada teste tem `timeout` e há um `flock` que impede
execuções sobrepostas.

## Instalação

```bash
sudo cp -r mount-islive /opt/
cd /opt/mount-islive
sudo ./setup.sh
```

Requisitos: `bash`, `python3`, `openssl`, `util-linux` (`findmnt`, `mountpoint`, `flock`) e `coreutils` (`timeout`).
Corre como **root**, porque precisa de montar e desmontar.

### 1.ª execução: pede tudo

```text
=== mount-islive: primeira configuração ===

[1/4] Mounts
  Nome do ficheiro de teste [teste.txt]:
  Quantos mounts quer validar [0]: 2
  Mount 1 de 2 (caminho, ex.: /mnt/nfs): /mnt/nfs_backup
    ok  /mnt/nfs_backup -> srv-nas:/export/backup nfs
    Pasta onde gravar o teste.txt (dentro de /mnt/nfs_backup) [/mnt/nfs_backup]: /mnt/nfs_backup/monitor
    !!  /mnt/nfs_backup/monitor não existe
    Criar a pasta agora? (sim/nao) [sim]:
    ok  escrita em /mnt/nfs_backup/monitor validada
  Mount 2 de 2 (caminho, ex.: /mnt/nfs): /mnt/cifs_dados
    ok  /mnt/cifs_dados -> //srv-files/dados cifs
    Pasta onde gravar o teste.txt (dentro de /mnt/cifs_dados) [/mnt/cifs_dados]:
    ok  escrita em /mnt/cifs_dados validada
  Intervalo entre testes, em minutos [5]:
  Timeout do teste de escrita, em segundos [15]:
  Enviar também alerta se o remount falhar (sim/nao) [sim]:

[2/4] Servidor SMTP
  Servidor SMTP (DNS ou IP): smtp.exemplo.pt
  TLS (nao / starttls / ssl) [nao]: starttls
  Porta [587]:
  Validar o certificado TLS (sim/nao) [nao]:
  O servidor exige autenticação (sim/nao) [nao]: sim
  Username: alertas@exemplo.pt
  Password: ********
  Repetir password: ********
    password guardada cifrada (não fica em texto no .env)

[3/4] Emails
  Remetente [mount-islive@srv01]: alertas@exemplo.pt
  Destinatário(s), separados por vírgula: dba@exemplo.pt, sistemas@exemplo.pt

[4/4] Agendamento
  Opções:
    systemd : cria e ativa mount-islive.service + mount-islive.timer (a cada 5 min)
    cron    : o setup acrescenta ao crontab do root a linha:
                */5 * * * * /opt/mount-islive/bin/check_mount.sh >/dev/null 2>&1
    manual  : não instala nada; mostra a linha para colocar no crontab
  Tipo (systemd / cron / manual) [systemd]: cron
  -> vai ser acrescentada ao crontab do root a linha:
       */5 * * * * /opt/mount-islive/bin/check_mount.sh >/dev/null 2>&1

  Configuração gravada em /opt/mount-islive/config/.env (chmod 600)
  Linha adicionada ao crontab do root

  Enviar um email de teste agora (sim/nao) [sim]:
  Executar já uma verificação dos mounts (sim/nao) [sim]:

----------------------------------------------------------------------
 Agendamento (cron): o que fazer / como confirmar
----------------------------------------------------------------------
  Linha instalada no crontab do root. Confirmar: sudo crontab -l
    */5 * * * * /opt/mount-islive/bin/check_mount.sh >/dev/null 2>&1
  Serviço cron: NÃO está a correr. Falta iniciá-lo:
    Debian/Ubuntu : service cron start      (container: 'cron' no entrypoint)
    RHEL/Oracle L.: service crond start     (container: 'crond' no entrypoint)
  Ver de novo a qualquer momento: sudo /opt/mount-islive/setup.sh --cron
----------------------------------------------------------------------
```

| Passo | Notas |
|---|---|
| Mounts | Indica-se primeiro **quantos** e depois cada um. O setup avisa se um mount não está no `/etc/fstab`, porque o remount usa `mount <caminho>` a partir do fstab |
| Pasta de teste | Por cada mount: onde gravar o `teste.txt` (omissão: o próprio mount). Tem de ser o mount ou uma subpasta. Se o mount estiver montado, o setup valida se a pasta existe (oferece-se para a criar) e se tem permissão de escrita |
| SMTP | Servidor por **DNS ou IP**, TLS (`nao`, `starttls` ou `ssl`; a porta sugerida muda conforme a escolha), e **username/password só se houver autenticação** |
| Agendamento | `systemd`, `cron` ou `manual`. Antes da escolha mostra o que cada opção faz (com a linha exata do crontab); no fim mostra o que falta fazer e como confirmar (ver [Agendamento](#agendamento)) |

### Execuções seguintes: mostra os valores atuais

```text
=== mount-islive: reconfiguração ===
----------------------------------------------------------------------
 Configuração atual (/opt/mount-islive/config/.env)
----------------------------------------------------------------------
 MOUNTS             2 mount(s)
   /mnt/nfs_backup                teste em /mnt/nfs_backup/monitor/teste.txt
   /mnt/cifs_dados                teste em /mnt/cifs_dados/teste.txt
 INTERVAL_MIN       5
 ...
 SMTP_AUTH          sim
 SMTP_USER          alertas@exemplo.pt
 SMTP_PASSWORD_ENC  (guardada - não é mostrada)
 MAIL_TO            dba@exemplo.pt,sistemas@exemplo.pt
 SCHEDULER          systemd
----------------------------------------------------------------------
 Agendamento : timer systemd ativo
  crontab do root (sudo crontab -e):
    */5 * * * * /opt/mount-islive/bin/check_mount.sh >/dev/null 2>&1
----------------------------------------------------------------------
  Enter mantém o valor entre [ ].
```

Para acrescentar um mount, basta aumentar o número: os que já existem aparecem como valor por omissão.
Na password, **Enter** mantém a atual e escrever uma nova substitui-a. A password nunca é mostrada.

### Opções do setup

| Comando | Ação |
|---|---|
| `sudo ./setup.sh` | Configurar ou reconfigurar |
| `sudo ./setup.sh --show` | Mostrar a configuração, o agendamento e a linha do crontab |
| `sudo ./setup.sh --cron` | Mostrar só a linha a colocar no crontab |
| `sudo ./setup.sh --test-mail` | Enviar um email de teste |
| `sudo ./setup.sh --uninstall` | Remover o timer/cron (mantém a configuração e os logs) |

## Configuração (`config/.env`)

| Variável | Omissão | Descrição |
|---|---|---|
| `MOUNTS` | — | Mount(s) a validar, separados por espaço (é neles que é feito o `umount`/`mount`) |
| `TEST_DIRS` | = `MOUNTS` | Pasta do ficheiro de teste de cada mount, pela mesma ordem (o mount ou uma subpasta) |
| `INTERVAL_MIN` | `5` | Minutos entre testes (1–1440) |
| `TEST_FILE` | `teste.txt` | Nome do ficheiro escrito em cada pasta de teste com a data do teste |
| `TOUCH_TIMEOUT` | `15` | Segundos até considerar que o mount não responde |
| `NOTIFY_ON_FAIL` | `sim` | Enviar um alerta (uma vez) se o remount falhar |
| `SMTP_SERVER` | — | Servidor SMTP (nome DNS ou IP) |
| `SMTP_PORT` | `25` / `587` / `465` | Porta (a omissão depende do TLS) |
| `SMTP_SECURITY` | `nao` | TLS: `nao`, `starttls` ou `ssl` |
| `SMTP_TLS_VERIFY` | `nao` | Validar o certificado do servidor (`nao` para CAs internas) |
| `SMTP_AUTH` | `nao` | `sim` = usa username e password |
| `SMTP_USER` | — | Username (só com autenticação) |
| `SMTP_PASSWORD_ENC` | — | Password cifrada pelo setup (só com autenticação) |
| `MAIL_FROM` / `MAIL_TO` | — | Remetente e destinatários (separados por vírgula) |
| `SCHEDULER` | `systemd` / `cron` | `systemd`, `cron` ou `manual` |

### Password SMTP: porque não é um hash

Um hash não se pode reverter, e o servidor SMTP precisa da **password original** para autenticar
(AUTH PLAIN/LOGIN). Uma password guardada só em hash deixaria o script sem forma de enviar emails.

Por isso a password é **cifrada** e não é recuperável pelo utilizador:

| Proteção | Como |
|---|---|
| Nunca fica em texto | Cifrada com AES-256-CBC + PBKDF2 (200 000 iterações) com uma chave aleatória em `config/smtp.key` |
| Nunca é mostrada | O setup e o `--show` só indicam "(guardada - não é mostrada)"; não há nenhuma opção para a ver |
| Só o root lhe acede | `config/`, `.env` e `smtp.key` com `chmod 700`/`600` |
| Só é decifrada no envio | Fica em memória durante o envio e não é escrita em disco nem nos logs |

Se o servidor permitir envio **sem autenticação** a partir do IP da máquina (relay interno), responda
`nao` à autenticação: nesse caso não é guardada nenhuma password.

## Agendamento

| Tipo | Quando usar | O que faz |
|---|---|---|
| `systemd` | Servidores normais (omissão quando existe systemd) | Cria `mount-islive.service` + `mount-islive.timer` (`OnUnitActiveSec=<INTERVAL_MIN>min`, arranca 2 min após o boot) |
| `cron` | Sistemas sem systemd, ex.: **containers** | Acrescenta a linha ao crontab do root (`crontab`); se o comando não existir, cria `/etc/cron.d/mount-islive`. Avisa se o serviço cron não estiver a correr |
| `manual` | Quando o agendamento é feito noutro sítio | Não instala nada e mostra os passos: `crontab -e` + linha, ou o comando para criar `/etc/cron.d/mount-islive` |

No fim do setup aparece sempre o bloco **"Agendamento: o que fazer / como confirmar"** com a opção escolhida
(linha instalada, como confirmar, e se o serviço cron está a correr ou o que falta para o iniciar).

A linha do crontab pode ser vista a qualquer momento com `sudo ./setup.sh --cron`:

```text
  crontab do root (sudo crontab -e):
    */5 * * * * /opt/mount-islive/bin/check_mount.sh >/dev/null 2>&1
  ou ficheiro /etc/cron.d/mount-islive (com utilizador):
    */5 * * * * root /opt/mount-islive/bin/check_mount.sh >/dev/null 2>&1
```

| Intervalo | Expressão cron |
|---|---|
| 1 a 59 min | `*/N * * * *` |
| Múltiplos de 60 (até 23 h) | `0 */H * * *` |
| 1440 (1 dia) | `0 0 * * *` |
| Outros (ex.: 90) | Não representável em cron: usar systemd |

Num container, o serviço cron tem de estar a correr (ex.: `cron` ou `crond` no entrypoint), e o container
precisa de permissões para montar (`--privileged` ou `--cap-add SYS_ADMIN`). O script define o seu próprio
`PATH`, por isso funciona com o ambiente mínimo do cron.

```bash
systemctl list-timers mount-islive.timer     # systemd: próxima execução
journalctl -u mount-islive.service -n 50     # systemd: últimas execuções
sudo /opt/mount-islive/bin/check_mount.sh -v # executar já, com detalhe
```

## Emails

**Mount recuperado** (enviado sempre que um mount volta a ficar ON):

```text
Assunto: [srv01] Mount /mnt/nfs_backup recuperado - ON

O mount /mnt/nfs_backup no servidor srv01 esteve em baixo e já está novamente ON.

  Servidor                      : srv01
  Mount                         : /mnt/nfs_backup
  Último teste OK antes da falha: 08/11/2026 09:55:00
  Falha detetada                : 08/11/2026 10:00:00
  Motivo                        : sem resposta em 15s (timeout)
  Teste com sucesso (ON)        : 08/11/2026 10:00:18
  Tempo em baixo (aprox.)       : 0h 00m 18s
  Tentativas de remount         : 1
```

**Alerta** (só com `NOTIFY_ON_FAIL=sim`, enviado uma vez quando o remount falha; o script continua a
tentar em cada ciclo e envia o email de "ON" quando o mount recuperar):

```text
Assunto: [srv01] ALERTA: mount /mnt/nfs_backup em baixo
```

## Log

```text
2026-11-08 09:56:53 OK /mnt/nfs_backup
2026-11-08 09:57:22 FALHA /mnt/nfs_backup: sem resposta em 15s (timeout). A refazer o mount (tentativa 1)...
2026-11-08 09:57:24 OK /mnt/nfs_backup: recuperado após remount (teste com sucesso às 2026-11-08 09:57:24)
2026-11-08 09:57:24 Email enviado: [srv01] Mount /mnt/nfs_backup recuperado - ON
```

## Testes feitos

Testado com mounts `tmpfs` no `/etc/fstab` e um servidor SMTP local com autenticação:

| Cenário | Resultado |
|---|---|
| 1.ª configuração com 2 mounts e autenticação | `.env` e `smtp.key` com chmod 600, password cifrada, linha no cron |
| Reconfiguração para 3 mounts (2 mantidos com Enter), servidor por IP, password mantida, agendamento `manual` | Valores mantidos, cron removido, linha do crontab mostrada |
| Email de teste com autenticação | O servidor recebeu o username e a password corretos |
| 2 de 3 mounts desmontados, corrido com `env -i` (ambiente do cron) | Os 2 voltaram a montar e foram enviados 2 emails "ON" |
| Mount desmontado / escrita pendurada (FIFO) / remount a falhar 2 ciclos | Email "ON" / timeout + remount / 1 alerta e depois "ON" |
| Sem autenticação (`SMTP_AUTH=nao`) | Email enviado sem AUTH |
| Pasta de teste numa subpasta inexistente | O setup oferece-se para a criar e valida a escrita |
| Pasta de teste fora do mount (ex.: `/tmp/x`) | Recusada pelo setup |
| Pasta de teste apagada no mount | Remount + alerta com o motivo "a pasta ... não existe no mount" |
| `.env` antigo sem `TEST_DIRS` | Usa o próprio mount, como antes |
| Agendamento `cron` (sem `crontab`) e `manual` | Mostrada a linha antes da escolha e os passos/confirmação no fim |

O agendamento por systemd não foi testado neste ambiente, que não tem systemd.

## Notas

- Se o mount usar `hard` no NFS, um processo pendurado pode ficar no estado `D` mesmo depois do `timeout`.
  O `umount -f -l` liberta o caminho para o novo mount.
- Se não houver permissão de escrita na raiz do mount, indique uma subpasta onde haja (ex.: `/mnt/nfs_backup/monitor`).
  A subpasta tem de estar no mesmo mount: se ela própria for outro mount, o teste valida esse outro.
- O `teste.txt` fica na pasta de teste com a data do último teste com sucesso, o que é útil para confirmar do lado do servidor.
