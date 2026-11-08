# count-code

Script que **conta e detalha as linhas de código de um projeto**: percorre as pastas, identifica a
linguagem de cada ficheiro pela extensão e separa as linhas em **código**, **comentários** e **em branco**.
Mostra os totais por linguagem e, se pedido, por pasta e por ficheiro, ou exporta tudo em JSON.

Origem: `nmmelo/workspace`, branch `nmmelo`, pasta `scripts/` (`count-loc.sh`).

## Ficheiros

| Ficheiro | Descrição |
|---|---|
| [`count-loc.sh`](count-loc.sh) | O script (Bash que executa Python 3 embutido) |
| [`CONVERSA.md`](CONVERSA.md) | Resumo do pedido no chat |

## Requisitos

| Requisito | Notas |
|---|---|
| Bash | Para lançar o script |
| Python 3 | Só biblioteca padrão, não precisa de `pip` |

## Utilização

```bash
chmod +x count-loc.sh
./count-loc.sh [caminho] [opções]
```

| Opção | Descrição |
|---|---|
| `caminho` | Pasta a analisar (predefinição: pasta atual `.`) |
| `-d`, `--dirs` | Detalhe por pasta de primeiro nível |
| `-f`, `--files` | Top 20 ficheiros com mais linhas de código |
| `--json` | Resultado em JSON (com `--files` inclui a lista de todos os ficheiros) |
| `-e`, `--exclude PASTA ...` | Pastas adicionais a ignorar |
| `-h`, `--help` | Ajuda |

### O que conta e o que ignora

| | |
|---|---|
| **Linguagens** | PHP, JavaScript/TypeScript (JSX/TSX), HTML, CSS/SCSS/Sass/Less, SQL, Shell, Python, JSON, XML, YAML, Markdown, INI/Config, Java, C/C++, Go, Rust, Ruby, texto |
| **Pastas ignoradas** | `.git`, `node_modules`, `vendor`, `.secrets`, `.vscode`, `.idea`, `__pycache__`, `dist`, `build`, `coverage`, `.cache`, `tmp`, `temp`, `.next`, `.nuxt` e todas as pastas começadas por `.` |
| **Ficheiros ignorados** | Imagens, PDFs, arquivos comprimidos, fontes, áudio/vídeo, binários, chaves, bases de dados SQLite, ligações simbólicas e extensões desconhecidas |
| **Comentários** | Linhas que começam pelo marcador da linguagem (`#`, `//`, `--`, `;`) e blocos (`/* */`, `<!-- -->`, `"""`, `=begin/=end`) |

## Exemplos

```bash
./count-loc.sh portal                    # resumo por linguagem
./count-loc.sh portal --dirs --files     # + detalhe por pasta e top 20 ficheiros
./count-loc.sh portal -e tests docs      # ignorar também tests/ e docs/
./count-loc.sh portal --json > loc.json  # exportar em JSON
```

### Exemplo real: `docker/portal-oracle` deste repositório

```text
$ ./outros/count-code/count-loc.sh docker/portal-oracle --dirs --files

==============================================================================
 Análise de Linhas de Código: portal-oracle
 Caminho: /home/claude/claude/docker/portal-oracle
==============================================================================

┌────────────────────┬──────────┬────────────┬────────────┬──────────┬────────────┐
│ Linguagem          │ Ficheiros │     Código │ Comentários │   Branco │      Total │
├────────────────────┼──────────┼────────────┼────────────┼──────────┼────────────┤
│ SQL                │        2 │      1 447 │        114 │      130 │      1 691 │
│ Python             │       15 │      1 390 │        172 │      340 │      1 902 │
│ HTML               │       19 │        787 │          0 │       38 │        825 │
│ Markdown           │        2 │        419 │          0 │      162 │        581 │
│ CSS                │        1 │        110 │          3 │       13 │        126 │
│ Shell / Bash       │        1 │        103 │         16 │       13 │        132 │
│ INI / Config       │        4 │         75 │          6 │       10 │         91 │
│ YAML               │        1 │         15 │          0 │        1 │         16 │
│ Plain Text         │        1 │          7 │          0 │        0 │          7 │
├────────────────────┼──────────┼────────────┼────────────┼──────────┼────────────┤
│ TOTAL              │       46 │      4 353 │        311 │      707 │      5 371 │
└────────────────────┴──────────┴────────────┴────────────┴──────────┴────────────┘

 Detalhe por Pasta:
┌────────────────────────┬──────────┬────────────┬────────────┬────────────┐
│ Pasta                  │ Ficheiros │     Código │     Branco │      Total │
├────────────────────────┼──────────┼────────────┼────────────┼────────────┤
│ app                    │       34 │      3 671 │        509 │      4 460 │
│ .                      │        7 │        504 │        175 │        688 │
│ docker                 │        4 │        159 │         19 │        198 │
│ deploy                 │        1 │         19 │          4 │         25 │
└────────────────────────┴──────────┴────────────┴────────────┴──────────┴────────────┘

 Top 20 Maiores Ficheiros de Código:
┌──────────────────────────────────────────────┬──────────────┬──────────┬──────────┐
│ Ficheiro                                     │ Linguagem    │   Código │    Total │
├──────────────────────────────────────────────┼──────────────┼──────────┼──────────┤
│ app/sql/shrink_tablespace.sql                │ SQL          │      766 │      903 │
│ app/sql/shrink_block.sql                     │ SQL          │      681 │      788 │
│ README.md                                    │ Markdown     │      338 │      465 │
│ app/views/config.py                          │ Python       │      304 │      358 │
│ app/store.py                                 │ Python       │      251 │      437 │
│ ...                                          │              │          │          │
└──────────────────────────────────────────────┴──────────────┴──────────┴──────────┘

 Distribuição : 81.0% código  |  5.8% comentários  |  13.2% linhas em branco
 Maior código : app/sql/shrink_tablespace.sql (766 linhas de código)
 Tempo decorr.: 0.007 segundos
==============================================================================
```

### Exemplo em JSON: `docker/mariadbv1`

```text
$ ./outros/count-code/count-loc.sh docker/mariadbv1 --json
{
  "project": "/home/claude/claude/docker/mariadbv1",
  "elapsed_seconds": 0.0019,
  "summary": {
    "total_files": 5,
    "total_code_lines": 447,
    "total_comment_lines": 7,
    "total_blank_lines": 148,
    "total_lines": 602
  },
  "by_language": {
    "Markdown": { "files": 2, "code": 374, "comment": 0, "blank": 138, "total": 512 },
    "YAML":     { "files": 1, "code": 23,  "comment": 0, "blank": 0,   "total": 23 },
    "Shell / Bash": { "files": 2, "code": 50, "comment": 7, "blank": 10, "total": 67 },
    ...
  },
  "by_directory": { ... }
}
```

## Limitações conhecidas

| Situação | Efeito |
|---|---|
| Ficheiros `.env` e `.env.example` | Não são contados (a extensão detetada é vazia ou `.example`) |
| Cabeçalhos `Ficheiros` / `Comentários` | São 1 carácter mais largos que a coluna, por isso a tabela fica ligeiramente desalinhada |
| Rodapé da tabela `--dirs` | Tem uma coluna a mais |
| Comentários no fim de uma linha de código | A linha conta como código |
| Docstrings Python (`"""`) | Contam como comentário |
