# Conversa: extrair_pasta_git

Chat de 19 de novembro de 2026.

---

## 1. Pedido

**Pergunta:**
> Colocar este script no repositório projetos em linux, geral, extrair_pasta_git.

Foi fornecido o script `extrair_pasta_git.sh`: pede os dados interativamente, usa `sparse-checkout`
num diretório temporário (compatível com HTTPS e SSH) e extrai apenas o conteúdo da pasta remota
para o destino escolhido, limpando os ficheiros temporários no final.

**Resposta:** foi criado o projeto `linux/geral/extrair_pasta_git`:

| Ficheiro | Conteúdo |
|---|---|
| `extrair_pasta_git.sh` | Script fornecido, com permissão de execução |
| `README.md` | Requisitos, execução, dados pedidos, funcionamento e notas |
| `CONVERSA.md` | Este resumo |

Foram também atualizados o `README.md` de `linux/geral` e o `README.md` da raiz.

---

## 2. Guardar as configurações

**Pergunta:**
> Melhorar o script para guardar as configurações.

**Resposta:** o script passou para a versão 2.0, com perfis:

| Alteração | Detalhe |
|---|---|
| Perfis | Um ficheiro `<perfil>.conf` por perfil em `~/.config/extrair_pasta_git/` (pasta 700, ficheiros 600) |
| Menu | No arranque lista os perfis; os valores do perfil escolhido aparecem como predefinição |
| Guardar | Depois de uma extração com sucesso pergunta se guarda (nome por omissão: última pasta remota) |
| Opções | `-c` executa o perfil sem perguntas, `-s` guarda, `-e` edita, `-l` lista, `-x` apaga |
| Compatibilidade | Mantêm-se os argumentos `-r -b -p -d` (usados no README do Portal Oracle); com `-c` sobrepõem os valores do perfil |
| Segurança | Leitura linha a linha (sem `source`) e aviso se o URL tiver credenciais |
