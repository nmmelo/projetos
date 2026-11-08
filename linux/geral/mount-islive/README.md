# mount-islive

Valida, de X em X minutos, se os mounts do `/etc/fstab` (NFS, CIFS, etc.) estão **realmente vivos**.
Por vezes, numa falha de rede, o mount continua a aparecer como montado mas deixa de responder.
O script escreve a data atual num ficheiro de teste dentro do mount. Se não conseguir, **refaz o mount**
e, quando o mount volta a funcionar, envia um **email por SMTP** com a hora da falha e a hora do teste com sucesso.

## Ficheiros

| Ficheiro | Descrição |
|---|---|
| [`setup.sh`](setup.sh) | Assistente de configuração: mounts, intervalo, SMTP, password, emails. Grava `config/.env` e instala o agendamento |
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
 escreve "data hora" em <mount>/teste.txt e lê de volta  (timeout TOUCH_TIMEOUT)
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
| Erro ao escrever | `não foi possível escrever em teste.txt` |
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

[1/3] Mounts
  Mount(s) a validar, separados por espaço (ex.: /mnt/nfs /backup): /mnt/nfs_backup
    ok  /mnt/nfs_backup -> srv-nas:/export/backup nfs
  Intervalo entre testes, em minutos [5]: 5
  Nome do ficheiro de teste [teste.txt]:
  Timeout do teste de escrita, em segundos [15]:
  Enviar também alerta se o remount falhar (sim/nao) [sim]:

[2/3] Servidor SMTP
  Servidor SMTP: smtp.exemplo.pt
  Segurança (none/starttls/ssl) [none]: starttls
  Porta [587]:
  Validar o certificado TLS (sim/nao) [nao]:
  Utilizador SMTP (Enter se não houver autenticação): alertas@exemplo.pt
  Password: ********
  Repetir password: ********
    password cifrada (AES-256) com a chave /opt/mount-islive/config/smtp.key

[3/3] Emails
  Remetente [mount-islive@srv01]: alertas@exemplo.pt
  Destinatário(s), separados por vírgula: dba@exemplo.pt, sistemas@exemplo.pt

  Configuração gravada em /opt/mount-islive/config/.env (chmod 600)
  Timer systemd instalado: mount-islive.timer (a cada 5 min)

  Enviar um email de teste agora (sim/nao) [sim]:
  Executar já uma verificação dos mounts (sim/nao) [sim]:
```

O setup avisa se um mount não está no `/etc/fstab`, porque o remount usa `mount <caminho>` a partir do fstab.

### Execuções seguintes: mostra os valores atuais

```text
=== mount-islive: reconfiguração ===
----------------------------------------------------------------------
 Configuração atual (/opt/mount-islive/config/.env)
----------------------------------------------------------------------
 MOUNTS             /mnt/nfs_backup
 INTERVAL_MIN       5
 ...
 SMTP_PASSWORD_ENC  (guardada, cifrada)
 MAIL_TO            dba@exemplo.pt,sistemas@exemplo.pt
----------------------------------------------------------------------
 Agendamento : timer systemd ativo
----------------------------------------------------------------------
  Enter mantém o valor entre [ ].
```

Na password: **Enter** mantém a atual, escrever uma nova substitui-a e `-` apaga-a.

### Opções do setup

| Comando | Ação |
|---|---|
| `sudo ./setup.sh` | Configurar ou reconfigurar |
| `sudo ./setup.sh --show` | Mostrar a configuração e o agendamento |
| `sudo ./setup.sh --test-mail` | Enviar um email de teste |
| `sudo ./setup.sh --uninstall` | Remover o timer/cron (mantém a configuração e os logs) |

## Configuração (`config/.env`)

| Variável | Omissão | Descrição |
|---|---|---|
| `MOUNTS` | — | Mount(s) a validar, separados por espaço |
| `INTERVAL_MIN` | `5` | Minutos entre testes (1–1440) |
| `TEST_FILE` | `teste.txt` | Ficheiro escrito em cada mount com a data do teste |
| `TOUCH_TIMEOUT` | `15` | Segundos até considerar que o mount não responde |
| `NOTIFY_ON_FAIL` | `sim` | Enviar um alerta (uma vez) se o remount falhar |
| `SMTP_SERVER` / `SMTP_PORT` | — / `25` | Servidor SMTP |
| `SMTP_SECURITY` | `none` | `none`, `starttls` (587) ou `ssl` (465) |
| `SMTP_TLS_VERIFY` | `nao` | Validar o certificado do servidor (`nao` para CAs internas) |
| `SMTP_USER` | — | Utilizador; vazio = sem autenticação |
| `SMTP_PASSWORD_ENC` | — | Password cifrada pelo setup |
| `MAIL_FROM` / `MAIL_TO` | — | Remetente e destinatários (separados por vírgula) |

### Password SMTP

Não é possível guardar a password como *hash*: um hash não se reverte e o servidor SMTP precisa da
password original para autenticar. Por isso o setup **cifra-a** com AES-256-CBC + PBKDF2 (`openssl`),
usando uma chave aleatória em `config/smtp.key`. A password nunca fica em texto no `.env` e só é decifrada
no momento do envio. Os dois ficheiros ficam com `chmod 600`, só acessíveis ao root.

## Agendamento

| Sistema | O que é instalado |
|---|---|
| Com systemd | `/etc/systemd/system/mount-islive.service` + `mount-islive.timer` (`OnUnitActiveSec=<INTERVAL_MIN>min`, arranca 2 min após o boot) |
| Sem systemd | `/etc/cron.d/mount-islive` (`*/N * * * *`, máximo 59 min) |

```bash
systemctl list-timers mount-islive.timer     # próxima execução
journalctl -u mount-islive.service -n 50     # últimas execuções
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

Testado com um mount `tmpfs` no `/etc/fstab` e um servidor SMTP local com autenticação:

| Cenário | Resultado |
|---|---|
| 1.ª configuração e reconfiguração (Enter mantém os valores) | `.env` e `smtp.key` criados com chmod 600, password cifrada, cron atualizado |
| Mount desmontado | Remount pelo fstab e email "ON" com 1 tentativa |
| Escrita pendurada (FIFO no lugar do `teste.txt`) | Timeout ao fim de 3 s, remount e email "ON" |
| Remount a falhar 2 ciclos e depois a recuperar | 1 email de alerta, sem repetir, e depois email "ON" com 3 tentativas |
| `--show`, `--test-mail`, `--uninstall` | OK |

O agendamento por systemd não foi testado neste ambiente, que não tem systemd; foi testado o caminho do cron.

## Notas

- Se o mount usar `hard` no NFS, um processo pendurado pode ficar no estado `D` mesmo depois do `timeout`.
  O `umount -f -l` liberta o caminho para o novo mount.
- O `teste.txt` fica no mount com a data do último teste com sucesso, o que é útil para confirmar do lado do servidor.
