# extrair_pasta_git

Extrai **apenas uma pasta** de um repositório Git para um diretório local, sem trazer o histórico (`.git`).
Usa `sparse-checkout` com clone parcial (`--filter=blob:none --depth 1`) num diretório temporário,
copia o conteúdo da pasta para o destino e apaga os ficheiros temporários no final (mesmo em caso de erro).

Funciona com URLs HTTPS e SSH (GitHub, GitLab, Gitea, etc.).

As configurações podem ser guardadas em **perfis** e reutilizadas: escolhendo o perfil no menu
(os valores guardados aparecem como predefinição) ou executando-o diretamente sem perguntas com `-c <perfil>`.

## Ficheiros

| Ficheiro | Descrição |
|---|---|
| [`extrair_pasta_git.sh`](extrair_pasta_git.sh) | Script de extração (interativo, por argumentos ou por perfil) |
| [`CONVERSA.md`](CONVERSA.md) | Resumo do chat que deu origem ao projeto |

## Requisitos

| Requisito | Notas |
|---|---|
| `bash` | 4 ou superior |
| `git` | 2.25 ou superior (suporte a `sparse-checkout`) |
| Acesso ao repositório | Credenciais HTTPS ou chave SSH já configuradas para repositórios privados |

## Como executar

```bash
chmod +x extrair_pasta_git.sh
./extrair_pasta_git.sh                     # interativo
```

Sem perguntas, com argumentos (o que faltar é perguntado):

```bash
./extrair_pasta_git.sh -r https://github.com/<utilizador>/<repositorio>.git -b main \
                       -p docker/portal-oracle -d /tmp/portal-oracle
```

| Argumento | Descrição |
|---|---|
| `-r URL` | URL do repositório |
| `-b BRANCH` | Branch (por omissão `main`) |
| `-p PASTA` | Pasta dentro do repositório |
| `-d DESTINO` | Diretório de destino (por omissão `.`) |
| `-c PERFIL` | Executa o perfil sem perguntas; `-r -b -p -d` sobrepõem os valores do perfil |
| `-s PERFIL` | Guarda os valores usados no perfil depois de uma extração com sucesso |
| `-e PERFIL` | Edita o perfil (Enter mantém o valor atual), sem extrair |
| `-l` | Lista os perfis guardados |
| `-x PERFIL` | Apaga o perfil |
| `-h` | Ajuda |

> A mesma lógica está embutida no `instalar.sh` do [Portal Oracle](../../../docker/portal-oracle), para obter só a pasta do portal sem descarregar o repositório inteiro.

## Perfis

No modo interativo (sem argumentos) o script lista os perfis guardados; escolhe-se pelo n.º ou nome
(Enter para um novo), confirmam-se ou alteram-se os valores e, depois de uma extração com sucesso,
pergunta se guarda as configurações num perfil.

```bash
./extrair_pasta_git.sh -l                                           # listar perfis
./extrair_pasta_git.sh -c playbooks                                 # extrair com o perfil, sem perguntas
./extrair_pasta_git.sh -c playbooks -d /tmp/teste                   # perfil, mas com outro destino
./extrair_pasta_git.sh -r <url> -p ansible/playbooks -d /opt/pb -s playbooks   # extrair e guardar
./extrair_pasta_git.sh -e playbooks                                 # alterar o perfil
./extrair_pasta_git.sh -x playbooks                                 # apagar o perfil
```

| Item | Valor |
|---|---|
| Pasta | `~/.config/extrair_pasta_git/` (ou `$XDG_CONFIG_HOME/extrair_pasta_git/`) |
| Pasta alternativa | Variável de ambiente `EXTRAIR_PASTA_GIT_CONFIG=/caminho` |
| Ficheiro | `<perfil>.conf`, um por perfil |
| Permissões | Pasta `700`, ficheiros `600` |
| Nome do perfil | Letras, números, `.`, `_` e `-` (no modo interativo, por omissão o nome da última pasta remota) |

Exemplo de `<perfil>.conf`:

```ini
# Perfil extrair_pasta_git: playbooks
# Atualizado em: 2026-11-19 13:50:22
REPO_URL=git@github.com:<utilizador>/<repositorio>.git
BRANCH=main
REMOTE_FOLDER=ansible/playbooks
DEST_DIR=/opt/ansible/playbooks
```

O ficheiro é lido linha a linha (não é feito `source`), por isso não é executado código que lá esteja.
O perfil só é guardado depois de uma extração com sucesso (exceto com `-e`).

## Dados pedidos

| Pergunta | Exemplo | Valor por omissão |
|---|---|---|
| URL do repositório Git | `https://github.com/<utilizador>/<repositorio>.git` ou `git@github.com:<utilizador>/<repositorio>.git` | Obrigatório |
| Branch | `main`, `develop` | `main` |
| Caminho da pasta no repositório | `ansible/playbooks` (barras iniciais/finais são removidas) | Obrigatório |
| Diretório de destino local | `/opt/scripts/backup` | `.` (pasta atual) |

## Como funciona

| Passo | Ação |
|---|---|
| 1 | Carrega o perfil (se houver), aplica os argumentos e pede/confirma URL, branch, pasta remota e destino |
| 2 | Cria um diretório temporário (`mktemp -d`) com limpeza automática (`trap ... EXIT`) |
| 3 | Clone sem checkout, sem blobs e com profundidade 1 (só o último commit) |
| 4 | `sparse-checkout --cone` limitado à pasta pedida e checkout da branch |
| 5 | Valida se a pasta existe na branch; se não existir, termina com erro |
| 6 | Copia o conteúdo (`cp -a`, mantém permissões e datas) para o destino, criando-o se necessário |
| 7 | Guarda o perfil (`-s`, ou pergunta no modo interativo) |

## Notas

- Ficheiros com o mesmo nome no destino são **substituídos**.
- É copiado o **conteúdo** da pasta, não a pasta em si (ex.: `scripts/backup/*` → `<destino>/*`).
- O resultado não é um repositório Git: para atualizar, volte a correr o script (ex.: `-c <perfil>`).
- Credenciais no URL HTTPS (`https://utilizador:token@...`) ficariam gravadas no perfil: o script avisa e pede confirmação (sem terminal, não guarda). Prefira SSH ou um credential helper do Git.
