# Documento de Procedimento Técnico e Resumo de Operações

**Projeto:** Password Crypt Manager (`secpass.sh`)  
**Data:** 13 de Outubro de 2026  
**Ambiente:** Linux (Ubuntu/Debian/RHEL/Oracle Linux) / Bash / OpenSSL  
**Branch de Trabalho:** `nmmelo`  
**Versão Atual:** `1.1.0`  
**Autor:** Equipa de Engenharia / Antigravity AI  

---

## 1. Objetivo e Âmbito do Documento

O presente documento consolida o histórico técnico, decisões de arquitetura e validações operacionais do utilitário **Password Crypt Manager (`secpass.sh`)**.

O sistema foi concebido para responder à necessidade de armazenamento e consulta segura de palavras-passe locais através de cifra simétrica com OpenSSL, permitindo a separação de privilégios com o utilizador `root` e a integração com scripts de automação (como tarefas de backup RMAN, tarefas agendadas via cron e rotinas de manutenção).

---

## 2. Histórico Cronológico de Intervenções e Operações

### Operação 1: Implementação, Instalação e Validação do Utilitário `secpass.sh` (v1.0.0)

- **Requisitos e Especificação:**
  - Implementação integral das funcionalidades definidas no `README.md`:
    - `init-key`: Inicialização interativa de chave mestra de cifra em ficheiro protegido.
    - `encrypt`: Cifra de palavras-passe em formato Base64 com algoritmo AES-256-CBC e PBKDF2.
    - `decrypt`: Decifração de senhas para ficheiro em claro de destino.
    - `view`: Decifração temporária em memória para stdout sem escrever em disco (para exportação direta em variáveis como `DB_PASSWORD=$(sudo ... view ...)`).
    - `install`: Procedimento para instalar o utilitário em `/opt/secpass/secpass.sh` com permissões `0750` (`root:root`).
    - Políticas de segurança: Restrição ao utilizador `root`, permissões `0600` para ficheiros de chave e credenciais, e captura oculta de senhas sem eco no terminal.
    - Registo do fluxo operacional com agentes de IA (`Agents.md` / `AGENTS.md`) e controlo de versões (`Version`).

- **Ações Realizadas:**
  1. **Desenvolvimento do Script `secpass.sh`:**
     - Implementação dos subcomandos com parsing de opções curtas e longas (`-k`/`--key`, `-o`/`--output`, `-i`/`--input`, `-d`/`--dir`).
     - Integração de comandos OpenSSL com parâmetros estritos:
       ```bash
       openssl enc -aes-256-cbc -pbkdf2 -salt -base64 -pass "file:$key_file" -out "$output_file"
       ```
     - Tratamento específico de erros:
       - Validação de permissões com aviso `Permission denied. Apenas o utilizador root pode manipular a chave. Execute com sudo.`
       - Deteção do erro `bad decrypt` quando a chave utilizada não coincide com a chave que cifrou o ficheiro original.
     - Suporte transparente para terminais interativos (`read -s` com confirmação) e para fluxos de pipe/automação via stdin.
     - Limpeza de variáveis de memória em saída ou interrupção (`trap cleanup EXIT INT TERM`).
     - Criação do subcomando `install` para criação do diretório `/opt/secpass`, instalação do executável (`0750 root:root`) e do ficheiro `Version`.

  2. **Definição de Diretrizes para Agentes (`Agents.md` & `AGENTS.md`):**
     - Criação do manual de boas práticas operacionais, fluxo de segurança criptográfica, regras de isolamento de credenciais e fluxo de git na branch `nmmelo`.

  3. **Configuração de Controlo de Versão (`Version`):**
     - Inicialização do ficheiro `Version` com a versão semântica `1.0.0`.

  4. **Execução de Testes e Validação Completa (README.md - Secção 6):**
     - Validação do comando `init-key` com geração da chave e permissões `0600` (`root:root`).
     - Validação do comando `encrypt` com criação de ficheiro `.enc` e permissões `0600` (`root:root`).
     - Validação do comando `view` com decifração em memória e injeção em variável de ambiente via subshell.
     - Validação do comando `decrypt` com exportação para ficheiro em texto claro com permissões restritas.
     - Validação da rejeição e mensagem de erro ao decifrar com chave incorreta (`bad decrypt`).
     - Validação da instalação em `/opt/secpass/secpass.sh` com permissões `0750` (`root:root`).
     - Execução direta dos comandos de validação da Secção 6 com `/root/cifra.key` e `/etc/db_pass.enc`.

---

### Operação 2: Melhorias de Usabilidade, Ajuda Resumida vs Detalhada (-help), Caminho por Omissão em `init-key` e Validação Estrita (v1.1.0)

- **Requisitos e Necessidades:**
  1. Suporte à flag `-help` (além de `--help`, `-h` e `help`) para exibição da documentação detalhada.
  2. Execução sem qualquer argumento (`./secpass.sh`) deve apresentar automaticamente uma lista resumida das opções disponíveis, reservando a ajuda detalhada para quando solicitado via `-help`.
  3. No comando `init-key`, caso o utilizador não forneça o caminho da chave via `-k`:
     - Assumir por omissão a diretoria onde reside o executável (`$SCRIPT_DIR`) e o ficheiro padrão `key.secret`.
     - Permitir ao utilizador, em execução interativa, confirmar premindo Enter ou digitar um caminho e nome alternativos.
  4. Nos restantes comandos (`view`, `encrypt`, `decrypt`), caso sejam chamados sem parâmetros ou com parâmetros obrigatórios em falta:
     - Indicar explicitamente o erro com mensagem clara.
     - Informar o utilizador para executar acompanhado de `-help` (ex.: `./secpass.sh <cmd> -help` ou `./secpass.sh -help`).
  5. Refletir todas as alterações em `README.md`, `Agents.md`, atualizar a versão em `Version` para `1.1.0`, registar no `procedimento.md` e sincronizar via Git na branch `nmmelo`.

- **Ações Realizadas:**
  1. **Atualização do Script `secpass.sh`:**
     - `show_usage()`: Reformulado para exibir o catálogo resumido de comandos com saídas explicativas concisas, indicando claramente os comandos para ajuda detalhada (`-help`).
     - `show_help()`: Enriquecido com a especificação de todas as opções, exemplos, notas sobre parâmetros padrão e políticas de proteção de ficheiros.
     - `main()`: A invocação sem argumentos (`$# -eq 0`) dispara `show_usage` e termina com código 0. Reconhecimento de `-help`, `--help`, `-h` e `help` para disparar `show_help`.
     - `cmd_init_key()`: Adicionado suporte à omissão de `-k`, inicializando `default_key="${SCRIPT_DIR}/key.secret"`. Em terminais interativos (`[[ -t 0 ]]`), é apresentado prompt amigável permitindo aceitar o padrão ou indicar outro caminho.
     - `cmd_encrypt()`, `cmd_decrypt()`, `cmd_view()`: Validação inicial com verificação de `$# -eq 0` e validação de argumentos obrigatórios; em caso de omissão, saída com mensagem de erro explicativa direcionando para `<cmd> -help`.
     - Tratamento homogéneo de `-help` em todos os subcomandos.
  2. **Atualização da Documentação e Versão:**
     - `Version`: Atualizado para `1.1.0`.
     - `README.md`: Atualizado com secções dedicadas aos modos de ajuda resumida vs detalhada, caminhos por defeito e validações direcionadas a `-help`.
     - `Agents.md`: Adicionadas as regras de CLI e usabilidade.
  3. **Validação Operacional Executada:**
     - `./secpass.sh` sem argumentos: Apresenta resumo e termina com código 0.
     - `./secpass.sh -help`: Apresenta documentação detalhada completa e termina com código 0.
     - `./secpass.sh view`, `./secpass.sh encrypt`, `./secpass.sh decrypt` sem argumentos: Emitem erro com código 1 e instrução de `-help`.
     - Subcomandos com `-help` (ex.: `./secpass.sh view -help`): Apresentam documentação contextual e terminam com código 0.
     - `./secpass.sh init-key` sem `-k`: Cria com sucesso `${SCRIPT_DIR}/key.secret` com permissões `0600`.
     - Ciclo de cifragem e visualização com `encrypt` e `view` validado com sucesso.
