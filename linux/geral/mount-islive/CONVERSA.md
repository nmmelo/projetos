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

---

## 2. Vários mounts, SMTP, crontab e password

**Pergunta:**
> Pode ser mais que um mount, no setup indicamos o número de setup. No smtp posso indicar o DNS ou ip, username e password caso seja com autenticação, porta e se tem tls. No count , pode também ter a opção de colocar no crontab para sistemas como o container que não tem systemd, pode dar qual o comando a colocar no contrab. Tb quero que a primeira vez coloca a password e depois guarda a mesma em hash, não sendo possível recuperar a password.

**Resposta:**

| Pedido | Implementação |
|---|---|
| Número de mounts | O setup pergunta "Quantos mounts" e depois cada mount (`Mount 1 de N`), validando no fstab e recusando repetidos |
| SMTP | Servidor por DNS ou IP, TLS (`nao`/`starttls`/`ssl`) com a porta sugerida conforme o TLS, e pergunta se há autenticação; só nesse caso pede username e password (`SMTP_AUTH`) |
| Crontab | Novo passo "Agendamento": `systemd`, `cron` (crontab do root ou `/etc/cron.d`) ou `manual`; mostra sempre a linha do crontab, também com `./setup.sh --cron`; avisa se o cron não estiver a correr (containers) |
| Password em hash | **Não é possível**: o SMTP precisa da password original e um hash não se reverte. A password é pedida na 1.ª vez e guardada **cifrada** (AES-256 + PBKDF2 200 000 iterações), nunca é mostrada e não há opção para a ver; nas vezes seguintes Enter mantém-na. Alternativa sem password: relay SMTP sem autenticação pelo IP |

Testado com 2 e depois 3 mounts, servidor por IP, com e sem autenticação, e `check_mount.sh` corrido com `env -i`
para simular o ambiente do cron.

**Pergunta:**
> Na raiz do mount não consigo escrever: quero manter o mount e ter outro parâmetro a indicar onde é guardado o ficheiro de teste. Quando seleciono a opção de cron devia dar o output do que fazer (só aparece depois de correr de novo o setup).

**Resposta:**

| Pedido | Implementação |
|---|---|
| Pasta do ficheiro de teste | Novo parâmetro `TEST_DIRS` (uma pasta por mount, pela mesma ordem). O setup pergunta-a a seguir a cada mount (omissão: o próprio mount), exige que seja o mount ou uma subpasta, oferece-se para a criar e valida a escrita. O `umount`/`mount` continua a ser feito ao mount. Configurações antigas sem `TEST_DIRS` usam o mount |
| Output do cron | No passo de agendamento mostra o que cada opção faz (com a linha exata), confirma o que vai ser feito com a escolha e, no fim, mostra o bloco "o que fazer / como confirmar" (linha instalada, `crontab -l`, se o serviço cron está a correr e como o iniciar; no `manual`, os passos `crontab -e` ou `/etc/cron.d`) |
| Configuração cifrada | Só a password SMTP é cifrada; o resto do `.env` está em texto, mas com `chmod 600` (só root) |
