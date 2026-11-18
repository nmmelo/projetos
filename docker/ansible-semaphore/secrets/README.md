# secrets/ (opcional)

Por omissão tudo vem das variáveis da stack do Portainer. Esta pasta só é usada se descomentar o mount `/secrets` no
`docker-compose.yml`; os ficheiros aqui têm prioridade sobre as variáveis. Nada daqui vai para o Git além deste README.

| Ficheiro | Substitui a variável |
|---|---|
| `<ANSIBLE_KEY_FILE>` | `ANSIBLE_PRIVATE_KEY_B64` |
| `ansible.cfg` | `ANSIBLE_CFG_B64` / ansible.cfg gerado |
| `.vault_pass` | `ANSIBLE_VAULT_PASSWORD` |
| `authorized_keys` | junta-se a `SSH_PUBKEY` |
