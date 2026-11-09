# Repository Agent Instructions - Password Crypt Manager

## 1. Project Overview & Scope
Utilitário em Bash (`secpass.sh`) para gestão de segredos e credenciais locais através de cifra simétrica com `openssl` (AES-256-CBC com derivação PBKDF2 e salt).

## 2. Security & Coding Rules
- **Privilégios e Permissões:**
  - Apenas o utilizador `root` (ou execução via `sudo`) deve manipular chaves mestras e segredos sensíveis do sistema.
  - O ficheiro de chave (`key.secret`), ficheiros cifrados (`.enc`) e texto decifrado devem ter obrigatoriamente permissões estritas `0600` e dono `root:root`.
  - O script em produção (`/opt/secpass/secpass.sh`) deve possuir permissões `0750` e dono `root:root`.
- **Cifra e Derivação:**
  - Utilizar sempre o OpenSSL com a norma `aes-256-cbc -pbkdf2 -salt -base64`.
- **Proteção de Memória e Terminal:**
  - Leitura de palavras-passe em terminal interativo sempre sem eco (`read -s`).
  - Nunca expor credenciais em logs, histórico de comandos (`history`), variáveis expostas globalmente ou ficheiros temporários em claro.
  - Limpar variáveis sensíveis de memória (`unset`) imediatamente após a execução ou através de traps de encerramento (`EXIT`, `INT`, `TERM`).
- **Automação Segura:**
  - O comando `view` deve imprimir estritamente a senha decifrada em `stdout` para permitir integração limpa em subshells e variáveis de ambiente (ex.: `DB_PASSWORD=$(sudo /opt/secpass/secpass.sh view ...)`).
  - Todas as mensagens de erro, avisos e confirmações devem ser encaminhadas para `stderr` (ou para o ficheiro de log operacional apropriado).
- **Usabilidade e Interface CLI:**
  - Execução sem argumentos (`$# -eq 0`) deve devolver o resumo das opções (`show_usage`) com código de saída 0.
  - A flag `-help` (bem como `--help`, `-h` e `help`) deve exibir a ajuda detalhada (`show_help`).
  - No comando `init-key`, caso o parâmetro `-k` seja omitido, assumir por omissão a diretoria onde está o executável (`$SCRIPT_DIR`) e o nome `key.secret`, permitindo alteração interativa durante a execução ou aceitação do padrão.
  - Em comandos que exigem parâmetros (`view`, `encrypt`, `decrypt`), caso sejam executados sem argumentos ou com argumentos em falta, emitir mensagem informativa de erro em `stderr` direcionando para `-help`.

## 3. Git Workflow
- Trabalhar sempre na branch `nmmelo`.
- Rever as alterações com `git status` e `git diff` antes de submeter.
- Adicionar os ficheiros modificados e novos com `git add .`.
- Rever as alterações em preparação com `git diff --cached`.
- Comitar com mensagem clara, concisa e descritiva.
- Enviar o commit para o repositório remoto: `git push origin nmmelo`.

## 4. Documentação e Auditoria Obrigatória
- **procedimento.md:**
  - Guardar o resumo detalhado de todas as operações, melhorias e resoluções no ficheiro `procedimento.md`.
  - Registar sempre: problema / requisito inicial, causa e solução técnica detalhada.
- **Version:**
  - Manter o número de versão sincronizado entre `Version`, `procedimento.md` e `README.md`.
