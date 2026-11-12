# Conversa: remover-linha

Chat com o Claude, 4 a 9 de novembro de 2026.

---

## 1. Origem

O script nasceu de um projeto maior: um assistente em Python (terminal, estilo instalador Linux) para executar
playbooks Ansible de remoção de utilizadores. Um dos passos desse assistente pedia um IP e apagava do
`defaults/main.yml` de uma role (ex.: `roles/firewall-configuration/defaults/main.yml`) todas as linhas que o
contivessem, antes de correr o playbook.

Ao longo da conversa foram definidas as regras dessa remoção:

| Pedido | Decisão |
|---|---|
| Os IPs estão no ficheiro entre aspas, ex.: `"10.1.2.1"` | A procura ignora as aspas; o IP também pode ser escrito com aspas |
| Apagar só o IP certo | Só o IP exato: `10.1.2.1` não apaga `10.1.2.10` nem `110.1.2.1` |
| Fazer backup antes de alterar | `<ficheiro>.AAAAMMDD_HHMMSS.bak` |
| Não acumular backups | Mantém só o último; os anteriores são apagados **depois** de criar o novo |

## 2. Pedido

**Pergunta:**
> quero apenas o script para remover a linha com o ip de um ficheiro que eu indico no inicio

**Resposta:** criado o `remover_ip.py`, independente do assistente Ansible (sem configuração, sem Ansible, só
Python 3):

1. Pergunta o ficheiro (volta a perguntar se não existir).
2. Pergunta o IP (aceita aspas; volta a perguntar se for inválido).
3. Mostra as linhas encontradas e pede confirmação.
4. Faz backup, mantém só o último e remove as linhas.

Opções para uso sem perguntas: `-f`, `-i`, `-y`, `--dry-run`, `--sem-backup`, `--manter-backups`.

## 3. Repositório

**Pergunta:**
> adicionar este script no meu repositorio, geral/remover-linha com tudo o que é necessário

**Resposta:** adicionado em `geral/remover-linha` com o script, o README, um ficheiro de exemplo e testes
`pytest` (o repositório tem um workflow que corre `flake8` e `pytest`). Atualizados o `geral/README.md` e o
`README.md` da raiz.
