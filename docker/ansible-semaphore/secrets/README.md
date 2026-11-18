# secrets/ (opcional)

Alternativa às variáveis da stack: os ficheiros ficam numa pasta do anfitrião, carregados por upload (ex. File Station
do Synology), e têm prioridade sobre as variáveis. Nada daqui vai para o Git além deste README.

1. Criar a pasta (ex. `/volume1/docker/ansible-semaphore/secrets`) e carregar os ficheiros.
2. Na stack: `SECRETS_DIR=/volume1/docker/ansible-semaphore/secrets` → **Update the stack**.

| Ficheiro | Substitui a variável |
|---|---|
| `<ANSIBLE_KEY_FILE>` (ex. a chave privada com esse nome) | `ANSIBLE_PRIVATE_KEY_B64` |
| `.vault_pass` | `ANSIBLE_VAULT_PASSWORD` |
| `ansible.cfg` | `ANSIBLE_CFG_B64` / ansible.cfg gerado |
| `authorized_keys` | junta-se a `SSH_PUBKEY` |

A pasta é montada só de leitura em `/secrets`; o contentor copia os ficheiros com as permissões certas em cada arranque.
Restringir o acesso à pasta no DSM (só o utilizador que gere o NAS).
