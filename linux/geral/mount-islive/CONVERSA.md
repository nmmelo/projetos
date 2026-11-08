# Conversa: mount-islive

Chat com o Claude, 8 de novembro de 2026.

---

## 1. Pedido

**Pergunta:**
> Quero guardar no Claude em Linux/geral um novo projeto com o nome mount-islive, basicamente quero um script que faça a validação se um mount que está no fstab está no estado mount, por vezes por falha de rede vai a baixo mas o mount permanece ativo apensar de não dar, nesse sentido quero fazer de x em x minutos um touch de um ficheiro teste.txt com a data desse touch, caso não consiga deve refazer o mount e voltar a fazer o mount , depois deve enviar uma notificação para um email por smtp a informar que o mount estava em baixo , hora e data é que agora está on de novo coma hora do teste com sucesso. Para isso deve haver um setup que permita colocar as configurações de smtp, sendo que a password por ser por hash, qual o mount , de quanto em quanto tempo queremos e qual o email ou emails para notificar, sendo que a primeira vez o setup faz toda a configuração e depois uma segunda vez mostra os valores que já foram colados sendo que devem ficar num ficheiro .env por exemplo

**Resposta:** foi criado o projeto `linux/geral/mount-islive`:

| Pedido | Implementação |
|---|---|
| Testar de X em X minutos | Timer systemd (ou cron) instalado pelo `setup.sh` com `INTERVAL_MIN` |
| Touch do `teste.txt` com a data | Escreve `AAAA-MM-DD HH:MM:SS` no ficheiro e lê de volta, com timeout (apanha mounts pendurados) |
| Refazer o mount | `umount -f -l` + `mount <caminho>` a partir do fstab |
| Email quando volta a ficar ON | Hora da falha, motivo, hora do teste com sucesso, duração e tentativas |
| Setup: 1.ª vez pede tudo, depois mostra os valores | Os valores atuais aparecem entre `[ ]` e Enter mantém-nos; existe também `--show` |
| Password "por hash" | Cifrada (AES-256 + PBKDF2) com uma chave local, porque o SMTP precisa da password original e um hash não se reverte |
| Vários emails | `MAIL_TO` separado por vírgulas |
| Ficheiro `.env` | `config/.env` com chmod 600 |

Extras: alerta único quando o remount falha (`NOTIFY_ON_FAIL`), `flock` contra execuções sobrepostas,
vários mounts, `--test-mail` e `--uninstall`.

Testado com um `tmpfs` no fstab e um servidor SMTP local (cenários no [`README.md`](README.md)).
