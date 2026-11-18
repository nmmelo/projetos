# secrets/

Esta pasta fica só no anfitrião (em `DATA_DIR/secrets`) e é montada em `/secrets` (só leitura). Nada daqui vai para o
Git além deste README e do exemplo do `ansible.cfg`.

| Ficheiro | Obrigatório | Para quê |
|---|---|---|
| `ansible.cfg` | Sim | Configuração do Ansible (copiar de `ansible.cfg.example`) → `/etc/ansible/ansible.cfg` |
| `.vault_pass` | Se usar vault | Password do Ansible Vault → `/etc/ansible/.vault_pass` (grupo `ansible`) |
| `<ANSIBLE_KEY_FILE>` | Sim | Chave privada das ligações aos servidores → `~/.ssh/<nome>` do utilizador principal e do extra |
| `<ANSIBLE_KEY_FILE>.pub` | Não | Chave pública correspondente (copiada para junto da privada) |
| `authorized_keys` | Não | Chaves públicas do utilizador principal (alternativa a `SSH_PUBKEY`) |

```bash
chmod 700 secrets
chmod 600 secrets/*
```
