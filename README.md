# Projetos

Repositório para guardar os projetos importantes já feitos: ficheiros finais, documentação e, quando
útil, o registo da conversa que lhes deu origem. Cada projeto tem a sua própria pasta, com um
`README.md` que explica como usá-lo.

## Lista de projetos

| Projeto | Pasta | Descrição | Data |
|---|---|---|---|
| MariaDB em Docker | [`docker/mariadbv1`](docker/mariadbv1) | docker-compose que cria um contentor MariaDB 11.4 na porta 3306, com um user admin com permissões de DBA restrito ao IP 10.10.101.25, passwords em hash no `.env` e pastas `data` e `conf` montadas | 2026-11-06 |

## Organização

```
.
├── README.md            # Este ficheiro (objetivo e lista de projetos)
└── <área>/
    └── <projeto>/
        ├── README.md    # Como usar o projeto
        ├── CONVERSA.md  # Registo da conversa (opcional)
        └── ...          # Ficheiros do projeto
```

| Área | Conteúdo |
|---|---|
| `docker/` | Serviços em contentores (docker-compose, stacks) |

## Como acrescentar um projeto

1. Criar a pasta em `<área>/<projeto>` (ex.: `docker/gitlab`). Se for uma nova versão de um
   projeto existente, usar um sufixo (ex.: `mariadbv2`).
2. Incluir um `README.md` com o objetivo, os requisitos e os passos de utilização.
3. Acrescentar uma linha à tabela **Lista de projetos** acima.

## Regras

- Nunca fazer commit de passwords ou hashes reais: usar valores de exemplo (ou `.env.example`).
- Não guardar dados gerados em runtime (ex.: `data/`): ignorá-los com `.gitignore`.
