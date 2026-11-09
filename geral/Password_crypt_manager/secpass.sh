#!/usr/bin/env bash
# ==============================================================================
# Password Crypt Manager (secpass.sh)
# Utilitário em Bash para gestão de segredos e credenciais locais através de
# cifra simétrica com openssl (AES-256-CBC com PBKDF2 e salt).
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION_FILE="${SCRIPT_DIR}/Version"

if [[ -f "${VERSION_FILE}" ]]; then
    VERSION="$(tr -d '[:space:]' < "${VERSION_FILE}")"
else
    VERSION="1.1.0"
fi

# ------------------------------------------------------------------------------
# Funções Auxiliares de Limpeza e Segurança
# ------------------------------------------------------------------------------
cleanup() {
    # Garante que variáveis sensíveis em memória sejam descartadas ao terminar
    unset master_pass master_pass_confirm secret_pass secret_pass_confirm decrypted 2>/dev/null || true
}
trap cleanup EXIT INT TERM

check_root_permission() {
    if [[ $(id -u) -ne 0 ]] && [[ "${SECPASS_ALLOW_NON_ROOT:-0}" != "1" ]]; then
        echo "Erro: Permission denied. Apenas o utilizador root pode manipular a chave. Execute com sudo." >&2
        exit 1
    fi
}

check_dependencies() {
    local missing=()
    for cmd in openssl chmod chown stat mkdir; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            missing+=("$cmd")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "Erro: Dependências em falta no sistema: ${missing[*]}" >&2
        exit 1
    fi
}

ensure_secure_dir() {
    local target_dir="$1"
    if [[ ! -d "$target_dir" ]]; then
        mkdir -p "$target_dir"
        chmod 750 "$target_dir"
        if [[ $(id -u) -eq 0 ]]; then
            chown root:root "$target_dir" 2>/dev/null || true
        fi
    fi
}

apply_strict_file_perms() {
    local file_path="$1"
    chmod 600 "$file_path"
    if [[ $(id -u) -eq 0 ]]; then
        chown root:root "$file_path" 2>/dev/null || true
    fi
}

read_secret_hidden() {
    local prompt_msg="$1"
    local result_var="$2"
    local secret_input=""

    if [[ -t 0 ]]; then
        # Terminal interativo
        read -r -s -p "$prompt_msg" secret_input
        echo >&2
    else
        # Stdin por pipe / não interativo
        if ! IFS= read -r secret_input; then
            secret_input=""
        fi
    fi

    printf -v "$result_var" '%s' "$secret_input"
}

# ------------------------------------------------------------------------------
# Ajuda e Utilização
# ------------------------------------------------------------------------------
show_usage() {
    cat <<EOF
Password Crypt Manager (secpass.sh) v${VERSION}

Utilização: $0 <comando> [opções]

Comandos disponíveis:
  init-key   Inicializa um novo ficheiro de chave/cifra mestre
  encrypt    Cifra uma palavra-passe e guarda-a num ficheiro protegido
  decrypt    Decifra uma palavra-passe guardada para um ficheiro de texto
  view       Decifra e exibe a palavra-passe em stdout (sem guardar em disco)
  install    Instala o secpass.sh e estrutura padrão no sistema (ex: /opt/secpass)
  version    Exibe a versão do utilitário
  help       Exibe este menu detalhado de ajuda

Para ajuda detalhada:
  $0 -help (ou $0 --help)

Para ajuda específica de um comando:
  $0 <comando> -help
EOF
}

show_help() {
    cat <<EOF
Password Crypt Manager (secpass.sh) - Versão ${VERSION}

Utilitário em Bash para gestão de segredos e credenciais locais através de
cifra simétrica com OpenSSL (AES-256-CBC com derivação PBKDF2 e salt).

OPERAÇÕES E SINTAXE:

1. Inicializar Chave de Cifra:
   $0 init-key [-k <caminho_chave>]
   Opções:
     -k, --key          Caminho do ficheiro da chave a criar (omissão: ${SCRIPT_DIR}/key.secret)
     -h, --help, -help  Exibe ajuda deste comando
   Nota:
     Se o parâmetro -k for omitido, assume por defeito a pasta onde está o executável
     com o ficheiro 'key.secret'. Em modo interativo, permite confirmar ou alterar
     o caminho durante a execução.

2. Cifrar Palavra-Passe:
   $0 encrypt -k <caminho_chave> -o <caminho_cifrado>
   Opções:
     -k, --key          Caminho do ficheiro da chave mestre existente (obrigatório)
     -o, --output       Caminho do ficheiro cifrado de destino (obrigatório)
     -h, --help, -help  Exibe ajuda deste comando
   Nota:
     Caso executado sem parâmetros, avisa que requer parâmetros e sugere -help.

3. Decifrar para Ficheiro de Texto:
   $0 decrypt -k <caminho_chave> -i <caminho_cifrado> -o <caminho_texto>
   Opções:
     -k, --key          Caminho do ficheiro da chave mestre (obrigatório)
     -i, --input        Caminho do ficheiro cifrado de origem (obrigatório)
     -o, --output       Caminho do ficheiro em claro de destino (obrigatório)
     -h, --help, -help  Exibe ajuda deste comando
   Nota:
     Caso executado sem parâmetros, avisa que requer parâmetros e sugere -help.

4. Visualizar Palavra-Passe (stdout / variáveis):
   $0 view -k <caminho_chave> -i <caminho_cifrado>
   Opções:
     -k, --key          Caminho do ficheiro da chave mestre (obrigatório)
     -i, --input        Caminho do ficheiro cifrado de origem (obrigatório)
     -h, --help, -help  Exibe ajuda deste comando
   Nota:
     Caso executado sem parâmetros, avisa que requer parâmetros e sugere -help.

   Exemplo de automação:
     DB_PASS=\$($0 view -k /opt/secpass/key.secret -i /opt/secpass/db.enc)

5. Instalação no Sistema:
   $0 install [-d <diretório_destino>]
   Opções:
     -d, --dir          Diretório de instalação (omissão: /opt/secpass)
     -h, --help, -help  Exibe ajuda deste comando

POLÍTICAS DE SEGURANÇA:
  - Ficheiros de chave e de senhas cifradas são protegidos com permissões 0600.
  - Apenas o utilizador root (ou via sudo) deve manipular os segredos de sistema.
  - A leitura de senhas no terminal é feita de forma oculta sem eco.
  - O utilitário deteta falhas de chave incorreta e avisa sobre 'bad decrypt'.
EOF
}

# ------------------------------------------------------------------------------
# Comando: init-key
# ------------------------------------------------------------------------------
cmd_init_key() {
    local key_file=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -k|--key)
                if [[ $# -lt 2 || "$2" == -* ]]; then
                    echo "Erro: A opção '$1' requer o caminho do ficheiro de chave como argumento." >&2
                    echo "Para mais informações execute: $0 init-key -help (ou $0 -help)" >&2
                    exit 1
                fi
                key_file="$2"
                shift 2
                ;;
            -h|--help|-help|help)
                cat <<EOF
Uso: $0 init-key [-k <caminho_chave>]

Cria um novo ficheiro de chave mestre com permissões estritas 0600.
Se o parâmetro -k for omitido, assume por defeito a diretoria do executável
e o ficheiro 'key.secret', permitindo confirmação ou alteração interativa.

Opções:
  -k, --key              Caminho e nome do ficheiro da chave (omissão: ${SCRIPT_DIR}/key.secret)
  -h, --help, -help      Exibe esta ajuda
EOF
                exit 0
                ;;
            *)
                echo "Erro: Opção inválida para init-key: $1" >&2
                echo "Para mais informações execute: $0 init-key -help (ou $0 -help)" >&2
                exit 1
                ;;
        esac
    done

    # Se não foi fornecido o caminho da chave, usar omissão com prompt interativo
    if [[ -z "$key_file" ]]; then
        local default_key="${SCRIPT_DIR}/key.secret"
        if [[ -t 0 ]]; then
            read -r -p "Indique o caminho e nome do ficheiro de chave [${default_key}]: " user_input_key
            if [[ -n "${user_input_key:-}" ]]; then
                key_file="$user_input_key"
            else
                key_file="$default_key"
            fi
        else
            key_file="$default_key"
        fi
    fi

    check_root_permission
    check_dependencies

    if [[ -f "$key_file" ]]; then
        echo "Aviso: O ficheiro de chave '$key_file' já existe." >&2
        local sobrescrever=""
        read_secret_hidden "Deseja sobrescrever a chave existente? (digite 'SIM' para confirmar): " sobrescrever
        if [[ "$sobrescrever" != "SIM" ]]; then
            echo "Operação cancelada pelo utilizador." >&2
            exit 1
        fi
    fi

    ensure_secure_dir "$(dirname "$key_file")"

    local pass1=""
    local pass2=""

    read_secret_hidden "Introduza a frase de cifra mestre: " pass1
    if [[ -z "$pass1" ]]; then
        echo "Erro: A frase de cifra não pode estar vazia." >&2
        exit 1
    fi

    if [[ -t 0 ]]; then
        read_secret_hidden "Confirme a frase de cifra mestre: " pass2
        if [[ "$pass1" != "$pass2" ]]; then
            echo "Erro: As frases de cifra introduzidas não coincidem." >&2
            exit 1
        fi
    else
        if IFS= read -r pass2 && [[ -n "$pass2" ]]; then
            if [[ "$pass1" != "$pass2" ]]; then
                echo "Erro: As frases de cifra introduzidas não coincidem." >&2
                exit 1
            fi
        fi
    fi

    # Gravar o segredo mestre de forma estrita
    (
        umask 077
        printf "%s" "$pass1" > "$key_file"
    )
    apply_strict_file_perms "$key_file"

    echo "[SUCESSO] Chave de cifra inicializada com sucesso em: $key_file"
}

# ------------------------------------------------------------------------------
# Comando: encrypt
# ------------------------------------------------------------------------------
cmd_encrypt() {
    if [[ $# -eq 0 ]]; then
        echo "Erro: O comando 'encrypt' requer parâmetros obrigatórios." >&2
        echo "Uso: $0 encrypt -k <caminho_chave> -o <caminho_cifrado>" >&2
        echo "Para mais informações execute: $0 encrypt -help (ou $0 -help)" >&2
        exit 1
    fi

    local key_file=""
    local output_file=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -k|--key)
                if [[ $# -lt 2 || "$2" == -* ]]; then
                    echo "Erro: A opção '$1' requer um argumento." >&2
                    echo "Para mais informações execute: $0 encrypt -help (ou $0 -help)" >&2
                    exit 1
                fi
                key_file="$2"
                shift 2
                ;;
            -o|--output)
                if [[ $# -lt 2 || "$2" == -* ]]; then
                    echo "Erro: A opção '$1' requer um argumento." >&2
                    echo "Para mais informações execute: $0 encrypt -help (ou $0 -help)" >&2
                    exit 1
                fi
                output_file="$2"
                shift 2
                ;;
            -h|--help|-help|help)
                cat <<EOF
Uso: $0 encrypt -k <caminho_chave> -o <caminho_cifrado>

Cifra uma palavra-passe e guarda-a no ficheiro de destino com permissões estritas 0600.

Opções:
  -k, --key              Caminho do ficheiro da chave mestre (obrigatório)
  -o, --output           Caminho do ficheiro cifrado de destino (obrigatório)
  -h, --help, -help      Exibe esta ajuda
EOF
                exit 0
                ;;
            *)
                echo "Erro: Opção inválida para encrypt: $1" >&2
                echo "Para mais informações execute: $0 encrypt -help (ou $0 -help)" >&2
                exit 1
                ;;
        esac
    done

    if [[ -z "$key_file" || -z "$output_file" ]]; then
        echo "Erro: Os parâmetros -k (chave) e -o (destino) são obrigatórios." >&2
        echo "Uso: $0 encrypt -k <caminho_chave> -o <caminho_cifrado>" >&2
        echo "Para mais informações execute: $0 encrypt -help (ou $0 -help)" >&2
        exit 1
    fi

    check_root_permission
    check_dependencies

    if [[ ! -f "$key_file" ]]; then
        echo "Erro: Ficheiro de chave não encontrado: $key_file" >&2
        exit 1
    fi

    ensure_secure_dir "$(dirname "$output_file")"

    local pass1=""
    local pass2=""

    read_secret_hidden "Introduza a palavra-passe a cifrar: " pass1
    if [[ -z "$pass1" ]]; then
        echo "Erro: A palavra-passe a cifrar não pode estar vazia." >&2
        exit 1
    fi

    if [[ -t 0 ]]; then
        read_secret_hidden "Confirme a palavra-passe a cifrar: " pass2
        if [[ "$pass1" != "$pass2" ]]; then
            echo "Erro: As palavras-passe introduzidas não coincidem." >&2
            exit 1
        fi
    else
        if IFS= read -r pass2 && [[ -n "$pass2" ]]; then
            if [[ "$pass1" != "$pass2" ]]; then
                echo "Erro: As palavras-passe introduzidas não coincidem." >&2
                exit 1
            fi
        fi
    fi

    (
        umask 077
        printf "%s" "$pass1" | openssl enc -aes-256-cbc -pbkdf2 -salt -base64 -pass "file:$key_file" -out "$output_file"
    )

    apply_strict_file_perms "$output_file"
    echo "[SUCESSO] Palavra-passe cifrada com sucesso em: $output_file"
}

# ------------------------------------------------------------------------------
# Comando: decrypt
# ------------------------------------------------------------------------------
cmd_decrypt() {
    if [[ $# -eq 0 ]]; then
        echo "Erro: O comando 'decrypt' requer parâmetros obrigatórios." >&2
        echo "Uso: $0 decrypt -k <caminho_chave> -i <caminho_cifrado> -o <caminho_texto>" >&2
        echo "Para mais informações execute: $0 decrypt -help (ou $0 -help)" >&2
        exit 1
    fi

    local key_file=""
    local input_file=""
    local output_file=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -k|--key)
                if [[ $# -lt 2 || "$2" == -* ]]; then
                    echo "Erro: A opção '$1' requer um argumento." >&2
                    echo "Para mais informações execute: $0 decrypt -help (ou $0 -help)" >&2
                    exit 1
                fi
                key_file="$2"
                shift 2
                ;;
            -i|--input)
                if [[ $# -lt 2 || "$2" == -* ]]; then
                    echo "Erro: A opção '$1' requer um argumento." >&2
                    echo "Para mais informações execute: $0 decrypt -help (ou $0 -help)" >&2
                    exit 1
                fi
                input_file="$2"
                shift 2
                ;;
            -o|--output)
                if [[ $# -lt 2 || "$2" == -* ]]; then
                    echo "Erro: A opção '$1' requer um argumento." >&2
                    echo "Para mais informações execute: $0 decrypt -help (ou $0 -help)" >&2
                    exit 1
                fi
                output_file="$2"
                shift 2
                ;;
            -h|--help|-help|help)
                cat <<EOF
Uso: $0 decrypt -k <caminho_chave> -i <caminho_cifrado> -o <caminho_texto>

Decifra o ficheiro de senha para um ficheiro de texto em claro com permissões 0600.

Opções:
  -k, --key              Caminho do ficheiro da chave mestre (obrigatório)
  -i, --input            Caminho do ficheiro cifrado de origem (obrigatório)
  -o, --output           Caminho do ficheiro em claro de destino (obrigatório)
  -h, --help, -help      Exibe esta ajuda
EOF
                exit 0
                ;;
            *)
                echo "Erro: Opção inválida para decrypt: $1" >&2
                echo "Para mais informações execute: $0 decrypt -help (ou $0 -help)" >&2
                exit 1
                ;;
        esac
    done

    if [[ -z "$key_file" || -z "$input_file" || -z "$output_file" ]]; then
        echo "Erro: Os parâmetros -k (chave), -i (cifrado) e -o (destino em claro) são obrigatórios." >&2
        echo "Uso: $0 decrypt -k <caminho_chave> -i <caminho_cifrado> -o <caminho_texto>" >&2
        echo "Para mais informações execute: $0 decrypt -help (ou $0 -help)" >&2
        exit 1
    fi

    check_root_permission
    check_dependencies

    if [[ ! -f "$key_file" ]]; then
        echo "Erro: Ficheiro de chave não encontrado: $key_file" >&2
        exit 1
    fi

    if [[ ! -f "$input_file" ]]; then
        echo "Erro: Ficheiro cifrado não encontrado: $input_file" >&2
        exit 1
    fi

    ensure_secure_dir "$(dirname "$output_file")"

    local tmp_err
    tmp_err="$(mktemp)"

    if ! (
        umask 077
        openssl enc -d -aes-256-cbc -pbkdf2 -base64 -pass "file:$key_file" -in "$input_file" -out "$output_file" 2>"$tmp_err"
    ); then
        local err_msg
        err_msg="$(cat "$tmp_err")"
        rm -f "$tmp_err"
        rm -f "$output_file" 2>/dev/null || true

        if [[ "$err_msg" =~ "bad decrypt" ]]; then
            echo "Erro: bad decrypt / OpenSSL error. A chave indicada no parâmetro -k não coincide com a chave originalmente utilizada para gerar o ficheiro cifrado." >&2
        else
            echo "Erro: Falha ao decifrar ficheiro: $err_msg" >&2
        fi
        exit 1
    fi

    rm -f "$tmp_err"
    apply_strict_file_perms "$output_file"
    echo "[SUCESSO] Ficheiro decifrado com sucesso em: $output_file"
}

# ------------------------------------------------------------------------------
# Comando: view
# ------------------------------------------------------------------------------
cmd_view() {
    if [[ $# -eq 0 ]]; then
        echo "Erro: O comando 'view' requer parâmetros obrigatórios." >&2
        echo "Uso: $0 view -k <caminho_chave> -i <caminho_cifrado>" >&2
        echo "Para mais informações execute: $0 view -help (ou $0 -help)" >&2
        exit 1
    fi

    local key_file=""
    local input_file=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -k|--key)
                if [[ $# -lt 2 || "$2" == -* ]]; then
                    echo "Erro: A opção '$1' requer um argumento." >&2
                    echo "Para mais informações execute: $0 view -help (ou $0 -help)" >&2
                    exit 1
                fi
                key_file="$2"
                shift 2
                ;;
            -i|--input)
                if [[ $# -lt 2 || "$2" == -* ]]; then
                    echo "Erro: A opção '$1' requer um argumento." >&2
                    echo "Para mais informações execute: $0 view -help (ou $0 -help)" >&2
                    exit 1
                fi
                input_file="$2"
                shift 2
                ;;
            -h|--help|-help|help)
                cat <<EOF
Uso: $0 view -k <caminho_chave> -i <caminho_cifrado>

Decifra o segredo temporariamente em memória e imprime em stdout sem gravar em disco.

Opções:
  -k, --key              Caminho do ficheiro da chave mestre (obrigatório)
  -i, --input            Caminho do ficheiro cifrado de origem (obrigatório)
  -h, --help, -help      Exibe esta ajuda
EOF
                exit 0
                ;;
            *)
                echo "Erro: Opção inválida para view: $1" >&2
                echo "Para mais informações execute: $0 view -help (ou $0 -help)" >&2
                exit 1
                ;;
        esac
    done

    if [[ -z "$key_file" || -z "$input_file" ]]; then
        echo "Erro: Os parâmetros -k (chave) e -i (cifrado) são obrigatórios." >&2
        echo "Uso: $0 view -k <caminho_chave> -i <caminho_cifrado>" >&2
        echo "Para mais informações execute: $0 view -help (ou $0 -help)" >&2
        exit 1
    fi

    check_root_permission
    check_dependencies

    if [[ ! -f "$key_file" ]]; then
        echo "Erro: Ficheiro de chave não encontrado: $key_file" >&2
        exit 1
    fi

    if [[ ! -f "$input_file" ]]; then
        echo "Erro: Ficheiro cifrado não encontrado: $input_file" >&2
        exit 1
    fi

    local decrypted=""
    local tmp_err
    tmp_err="$(mktemp)"

    if ! decrypted="$(openssl enc -d -aes-256-cbc -pbkdf2 -base64 -pass "file:$key_file" -in "$input_file" 2>"$tmp_err")"; then
        local err_msg
        err_msg="$(cat "$tmp_err")"
        rm -f "$tmp_err"

        if [[ "$err_msg" =~ "bad decrypt" ]]; then
            echo "Erro: bad decrypt / OpenSSL error. A chave indicada no parâmetro -k não coincide com a chave originalmente utilizada para gerar o ficheiro cifrado." >&2
        else
            echo "Erro: Falha ao decifrar em memória: $err_msg" >&2
        fi
        exit 1
    fi

    rm -f "$tmp_err"
    # Imprime estritamente o valor decifrado para stdout
    printf "%s\n" "$decrypted"
}

# ------------------------------------------------------------------------------
# Comando: install
# ------------------------------------------------------------------------------
cmd_install() {
    local target_dir="/opt/secpass"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -d|--dir)
                if [[ $# -lt 2 || "$2" == -* ]]; then
                    echo "Erro: A opção '$1' requer um argumento." >&2
                    echo "Para mais informações execute: $0 install -help (ou $0 -help)" >&2
                    exit 1
                fi
                target_dir="$2"
                shift 2
                ;;
            -h|--help|-help|help)
                cat <<EOF
Uso: $0 install [-d <diretório_destino>]

Instala o utilitário secpass.sh e ficheiro Version no sistema.

Opções:
  -d, --dir              Diretório de instalação (omissão: /opt/secpass)
  -h, --help, -help      Exibe esta ajuda
EOF
                exit 0
                ;;
            *)
                echo "Erro: Opção inválida para install: $1" >&2
                echo "Para mais informações execute: $0 install -help (ou $0 -help)" >&2
                exit 1
                ;;
        esac
    done

    check_root_permission
    check_dependencies

    ensure_secure_dir "$target_dir"
    local dest_script="${target_dir}/secpass.sh"
    local dest_version="${target_dir}/Version"

    cp "${BASH_SOURCE[0]}" "$dest_script"
    chmod 750 "$dest_script"

    if [[ -f "$VERSION_FILE" ]]; then
        cp "$VERSION_FILE" "$dest_version"
        chmod 644 "$dest_version"
    fi

    if [[ $(id -u) -eq 0 ]]; then
        chown root:root "$dest_script" 2>/dev/null || true
        chown root:root "$dest_version" 2>/dev/null || true
    fi

    echo "[SUCESSO] Password Crypt Manager instalado com sucesso em: $dest_script"
    echo "[INFO] Permissões configuradas: 0750 (root:root)"
}

# ------------------------------------------------------------------------------
# Ponto de Entrada Principal
# ------------------------------------------------------------------------------
main() {
    if [[ $# -eq 0 ]]; then
        show_usage
        exit 0
    fi

    local action="$1"
    shift

    case "$action" in
        init-key)
            cmd_init_key "$@"
            ;;
        encrypt)
            cmd_encrypt "$@"
            ;;
        decrypt)
            cmd_decrypt "$@"
            ;;
        view)
            cmd_view "$@"
            ;;
        install)
            cmd_install "$@"
            ;;
        -v|--version|version)
            echo "Password Crypt Manager (secpass.sh) v${VERSION}"
            ;;
        -h|--help|-help|help)
            show_help
            ;;
        *)
            echo "Erro: Comando desconhecido: '$action'" >&2
            echo "Para a lista resumida de opções execute: $0" >&2
            echo "Para ajuda detalhada execute: $0 -help (ou $0 --help)" >&2
            exit 1
            ;;
    esac
}

main "$@"
