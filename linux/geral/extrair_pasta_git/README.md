# extrair_pasta_git

Extrai **apenas uma pasta** de um repositório Git para um diretório local, sem trazer o histórico (`.git`).
Usa `sparse-checkout` com clone parcial (`--filter=blob:none --depth 1`) num diretório temporário,
copia o conteúdo da pasta para o destino e apaga os ficheiros temporários no final (mesmo em caso de erro).

Funciona com URLs HTTPS e SSH (GitHub, GitLab, Gitea, etc.).

## Ficheiros

| Ficheiro | Descrição |
|---|---|
| [`extrair_pasta_git.sh`](extrair_pasta_git.sh) | Script interativo de extração |
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
./extrair_pasta_git.sh
```

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
| 1 | Pede URL, branch, pasta remota e destino |
| 2 | Cria um diretório temporário (`mktemp -d`) com limpeza automática (`trap ... EXIT`) |
| 3 | Clone sem checkout, sem blobs e com profundidade 1 (só o último commit) |
| 4 | `sparse-checkout --cone` limitado à pasta pedida e checkout da branch |
| 5 | Valida se a pasta existe na branch; se não existir, termina com erro |
| 6 | Copia o conteúdo (`cp -a`, mantém permissões e datas) para o destino, criando-o se necessário |

## Notas

- Ficheiros com o mesmo nome no destino são **substituídos**.
- É copiado o **conteúdo** da pasta, não a pasta em si (ex.: `scripts/backup/*` → `<destino>/*`).
- O resultado não é um repositório Git: para atualizar, volte a correr o script.
