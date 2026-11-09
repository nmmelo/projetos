# Password Crypt Manager (`secpass.sh`)

Utilitário em Bash para gestão de segredos e credenciais locais através de cifra simétrica com `openssl`.

O sistema opera com separação de privilégios: a chave simétrica de cifra fica restringida ao utilizador `root`, permitindo que palavras-passe sejam encriptadas, armazenadas em ficheiros protegidos e consultadas/decifradas a pedido para integração em rotinas de automação (ex.: scripts RMAN, backups, tarefas agendadas via *cron*).

---

## 1. Arquitetura e Segurança

* **Algoritmo de Cifra:** AES-256 em modo CBC com derivação por PBKDF2 e *salt* (`aes-256-cbc -pbkdf2`).
* **Ficheiro de Chave/Cifra (`key.secret`):** Ficheiro de texto restrito ao utilizador `root` com permissões `0600` / `0400`.
* **Ficheiro de Palavra-Passe Cifrada (`pass.enc`):** Armazena o conteúdo codificado em Base64.
* **Segurança de Entrada:** As senhas e a frase de cifra são introduzidas de forma oculta (sem *echo* no terminal) para prevenir fugas em histórico de comandos (`history`) ou inspeção de processos (`ps`).

---

## 2. Requisitos

* Sistema Operativo: Linux (Oracle Linux, RHEL, Ubuntu, Debian, etc.)
* Utilitários de base: `bash`, `openssl`, `coreutils` (`chmod`, `chown`, `stat`)

---

## 3. Estrutura de Ficheiros

```text
/opt/secpass/
├── secpass.sh          # Script de gestão (permissões 0750, root:root)
├── key.secret          # Chave de cifra gerada (permissões 0600, root:root)
└── app_pass.enc        # Palavra-passe cifrada resultante (permissões 0600, root:root)
```

---

## 4. Ajuda e Modos de Execução

O utilitário disponibiliza dois níveis de ajuda no terminal:

1. **Modo Resumido (Execução sem Parâmetros):**
   Ao executar o comando sem argumentos, o script apresenta automaticamente a listagem resumida dos comandos disponíveis:
   ```bash
   ./secpass.sh
   ```

2. **Ajuda Detalhada (`-help` / `--help` / `help`):**
   Apresenta todas as operações, opções detalhadas, exemplos práticos e políticas de segurança:
   ```bash
   ./secpass.sh -help
   ```

3. **Ajuda Específica de um Comando:**
   Todos os subcomandos suportam a opção `-help` para consultar a sintaxe isolada:
   ```bash
   ./secpass.sh init-key -help
   ./secpass.sh encrypt -help
   ./secpass.sh decrypt -help
   ./secpass.sh view -help
   ./secpass.sh install -help
   ```

---

## 5. Funcionalidades do Script

| Opção | Operação | Descrição |
| :--- | :--- | :--- |
| `init-key` | Inicialização da Cifra | Solicita uma frase secreta e cria o ficheiro de cifra com permissões estritas para `root`. Se o caminho não for especificado, assume a pasta do executável e `key.secret`. |
| `encrypt` | Cifrar Senha | Lê a chave de cifra, solicita a senha e grava-a cifrada num ficheiro de destino. |
| `decrypt` | Decifrar para Ficheiro | Decifra o ficheiro de senha cifrada e gera o ficheiro de texto em claro. |
| `view` | Visualização Segura | Decifra o ficheiro temporariamente em memória e imprime a senha na consola (ou envia para *stdout*). |
| `install` | Instalação no Sistema | Instala o utilitário em `/opt/secpass/secpass.sh` com permissões `0750` (`root:root`). |
| `version` | Versão do Utilitário | Exibe a versão atual do utilitário. |

---

## 6. Manual de Utilização

### 6.1. Inicializar a Chave de Cifra (`init-key`)
*Requer privilégios de `root`.*

Cria o ficheiro que servirá de chave mestre para as operações criptográficas:

```bash
# Opção A: Com caminho e ficheiro explícitos
sudo ./secpass.sh init-key -k /opt/secpass/key.secret

# Opção B: Sem especificar o caminho (assume por defeito <pasta_do_script>/key.secret)
sudo ./secpass.sh init-key
```
* **Comportamento por defeito:** Se o parâmetro `-k` não for indicado, o script sugere o caminho onde se encontra o executável e o nome `key.secret` (ex.: `/opt/secpass/key.secret` ou pasta local).
* Em modo interativo no terminal, o utilizador pode confirmar o valor por defeito premindo Enter ou digitar um caminho e nome de ficheiro alternativos.
* O utilizador é de seguida interpelado para introduzir e confirmar a frase de cifra mestre de forma oculta.
* O ficheiro é criado automaticamente com dono `root:root` e máscara `0600` (`chmod 600`).

---

### 6.2. Cifrar uma Palavra-Passe (`encrypt`)

Lê o ficheiro de cifra e cria o artefacto encriptado:

```bash
sudo ./secpass.sh encrypt -k /opt/secpass/key.secret -o /opt/secpass/db_backup.enc
```
* **Validação de parâmetros:** Caso seja invocado sem parâmetros ou falte `-k` ou `-o`, o utilitário emite mensagem de erro e informa que para mais detalhes deve ser executado `./secpass.sh encrypt -help`.
* O script solicita a palavra-passe interativamente sem a mostrar no ecrã.
* Grava o ficheiro cifrado com permissões de acesso restritas (`0600`).

---

### 6.3. Visualizar a Palavra-Passe (`view`)

Útil para validação operacional ou para injeção direta em variáveis de ambiente:

```bash
sudo ./secpass.sh view -k /opt/secpass/key.secret -i /opt/secpass/db_backup.enc
```
* **Validação de parâmetros:** Caso seja invocado sem parâmetros ou falte `-k` ou `-i`, o utilitário emite mensagem de erro e informa que para mais detalhes deve ser executado `./secpass.sh view -help`.

#### Exemplo de integração em automações/scripts:
```bash
# Exportar a senha decifrada para uma variável em tempo de execução sem escrever em disco:
DB_PASSWORD=$(sudo /opt/secpass/secpass.sh view -k /opt/secpass/key.secret -i /opt/secpass/db_backup.enc)
```

---

### 6.4. Decifrar para Ficheiro (`decrypt`)

Exporta o segredo de volta para um ficheiro legível em texto limpo:

```bash
sudo ./secpass.sh decrypt -k /opt/secpass/key.secret -i /opt/secpass/db_backup.enc -o /opt/secpass/db_backup.txt
```
* **Validação de parâmetros:** Caso seja invocado sem parâmetros ou falte `-k`, `-i` ou `-o`, o utilitário emite mensagem de erro e informa que para mais detalhes deve ser executado `./secpass.sh decrypt -help`.

---

## 7. Fluxo de Instalação e Teste Rápido

```bash
# 1. Atribuir permissões de execução ao script
chmod +x secpass.sh

# 2. Criar o segredo mestre (utilizando o caminho por defeito ou indicando explicitamente)
sudo ./secpass.sh init-key -k /root/cifra.key

# 3. Guardar uma senha cifrada
sudo ./secpass.sh encrypt -k /root/cifra.key -o /etc/db_pass.enc

# 4. Validar o valor decifrado
sudo ./secpass.sh view -k /root/cifra.key -i /etc/db_pass.enc
```

---

## 8. Políticas de Resolução de Problemas

* **Erro: `Permission denied` / `Apenas o utilizador root pode manipular a chave`:**
  O ficheiro de cifra contém dados sensíveis do sistema. Deve ser criado e acedido exclusivamente através de `root` ou `sudo`.
* **Erro: `bad decrypt` / `OpenSSL error`:**
  A chave indicada no parâmetro `-k` não coincide com a chave originalmente utilizada para gerar o ficheiro cifrado em questão.
* **Erro: `O comando '<comando>' requer parâmetros obrigatórios`:**
  Execute o comando acompanhado de `-help` (ex.: `./secpass.sh <comando> -help` ou `./secpass.sh -help`) para consultar a sintaxe detalhada.

---

## 9. Execução com Agentes de IA (`AGENTS.md`)

Antes de iniciar a geração de código, o agente de desenvolvimento deve **obrigatoriamente**:

1. **Ler o ficheiro `AGENTS.md`** localizado na raiz do projeto para carregar as convenções de código, diretrizes de segurança, persona operacional e fluxos de aprovação definidos para o ambiente.
2. **Implementar as regras e diretivas descritas no `AGENTS.md`** em todas as alterações subsequentes, garantindo alinhamento estrito entre a especificação deste `README.md` e os padrões operacionais do agente.

---

## 10. Guardar o resumo da operação no ficheiro (`procedimento.md`)
1. Guardar o resumo da operação no ficheiro, para servir de guia.

---

## 11. Alterar a versão no Version
1. Tem de coincidir com as alterações inseridas no `procedimento.md`.
2. Serve para controlo, caso sejam feitas muitas alterações num dia, deve ser registado no `procedimento.md` e `README.md`.
