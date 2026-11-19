#!/usr/bin/env bash
#
# Script para clonar/extrair apenas uma pasta específica de um repositório Git
# diretamente para um diretório de destino (sem histórico .git).
#
# As configurações podem ser guardadas em perfis (um ficheiro por perfil) e
# reutilizadas: escolhendo o perfil no menu ou com -c <perfil> (sem perguntas).
#

set -euo pipefail

VERSAO="2.0"
CONFIG_DIR="${EXTRAIR_PASTA_GIT_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/extrair_pasta_git}"

REPO_URL=""
BRANCH=""
REMOTE_FOLDER=""
DEST_DIR=""
PERFIL=""

# ------------------------------------------------------------------------------
# Ajuda
# ------------------------------------------------------------------------------
uso() {
    cat <<EOF
Uso: $(basename "$0") [opções]

Sem opções        Modo interativo: escolher um perfil guardado ou criar um novo.

Extração por argumentos (o que faltar é perguntado):
  -r URL          URL do repositório (HTTPS ou SSH)
  -b BRANCH       Branch (por omissão 'main')
  -p PASTA        Pasta dentro do repositório
  -d DESTINO      Diretório de destino (por omissão '.')

Perfis:
  -c PERFIL       Executa o perfil sem perguntas (-r -b -p -d sobrepõem os valores do perfil)
  -s PERFIL       Guarda os valores usados no perfil indicado depois de uma extração com sucesso
  -e PERFIL       Edita o perfil (mostra os valores atuais como predefinição), sem extrair
  -l              Lista os perfis guardados
  -x PERFIL       Apaga o perfil

  -h              Mostra esta ajuda

Perfis guardados em: ${CONFIG_DIR}
(alterar com a variável de ambiente EXTRAIR_PASTA_GIT_CONFIG)
EOF
}

erro() {
    echo "Erro: $*" >&2
    exit 1
}

# ------------------------------------------------------------------------------
# Perfis
# ------------------------------------------------------------------------------
validar_nome_perfil() {
    [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]] || erro "Nome de perfil inválido '$1' (use letras, números, '.', '_' ou '-')."
}

ficheiro_perfil() {
    echo "${CONFIG_DIR}/$1.conf"
}

listar_perfis() {
    local f
    shopt -s nullglob
    for f in "${CONFIG_DIR}"/*.conf; do
        basename "${f}" .conf
    done
    shopt -u nullglob
}

# Lê o perfil linha a linha (sem 'source', para não executar código do ficheiro)
carregar_perfil() {
    local nome="$1" f chave valor
    validar_nome_perfil "${nome}"
    f="$(ficheiro_perfil "${nome}")"
    [[ -f "${f}" ]] || erro "O perfil '${nome}' não existe (${f})."

    REPO_URL=""; BRANCH=""; REMOTE_FOLDER=""; DEST_DIR=""
    while IFS='=' read -r chave valor || [[ -n "${chave}" ]]; do
        [[ -z "${chave}" || "${chave}" == \#* ]] && continue
        case "${chave}" in
            REPO_URL)      REPO_URL="${valor}" ;;
            BRANCH)        BRANCH="${valor}" ;;
            REMOTE_FOLDER) REMOTE_FOLDER="${valor}" ;;
            DEST_DIR)      DEST_DIR="${valor}" ;;
        esac
    done < "${f}"
    PERFIL="${nome}"
}

guardar_perfil() {
    local nome="$1" f
    validar_nome_perfil "${nome}"
    f="$(ficheiro_perfil "${nome}")"

    mkdir -p "${CONFIG_DIR}"
    chmod 700 "${CONFIG_DIR}"
    (
        umask 077
        {
            echo "# Perfil extrair_pasta_git: ${nome}"
            echo "# Atualizado em: $(date '+%Y-%m-%d %H:%M:%S')"
            printf 'REPO_URL=%s\n'      "${REPO_URL}"
            printf 'BRANCH=%s\n'        "${BRANCH}"
            printf 'REMOTE_FOLDER=%s\n' "${REMOTE_FOLDER}"
            printf 'DEST_DIR=%s\n'      "${DEST_DIR}"
        } > "${f}"
    )
    chmod 600 "${f}"
    echo "Perfil '${nome}' guardado em ${f}"
}

apagar_perfil() {
    local nome="$1" f
    validar_nome_perfil "${nome}"
    f="$(ficheiro_perfil "${nome}")"
    [[ -f "${f}" ]] || erro "O perfil '${nome}' não existe."
    rm -f "${f}"
    echo "Perfil '${nome}' apagado."
}

mostrar_perfis() {
    local nome perfis=() i=1
    mapfile -t perfis < <(listar_perfis)
    if (( ${#perfis[@]} == 0 )); then
        echo "Não há perfis guardados em ${CONFIG_DIR}."
        return
    fi
    printf '%-3s %-20s %-12s %-30s %s\n' "N.º" "Perfil" "Branch" "Pasta remota" "Destino"
    for nome in "${perfis[@]}"; do
        carregar_perfil "${nome}"
        printf '%-3s %-20s %-12s %-30s %s\n' "${i}" "${nome}" "${BRANCH}" "${REMOTE_FOLDER}" "${DEST_DIR}"
        i=$((i + 1))
    done
    PERFIL=""
}

# ------------------------------------------------------------------------------
# Perguntas (os valores atuais aparecem como predefinição)
# ------------------------------------------------------------------------------
perguntar() {
    # $1 = texto, $2 = valor atual/predefinido; resultado em RESPOSTA
    local texto="$1" atual="${2:-}"
    if [[ -n "${atual}" ]]; then
        read -r -p "${texto} [${atual}]: " RESPOSTA
        RESPOSTA="${RESPOSTA:-${atual}}"
    else
        read -r -p "${texto}: " RESPOSTA
    fi
}

pedir_dados() {
    perguntar "URL do repositório Git (HTTPS ou SSH)" "${REPO_URL}"
    REPO_URL="${RESPOSTA}"
    [[ -n "${REPO_URL}" ]] || erro "O URL do repositório é obrigatório."

    perguntar "Branch" "${BRANCH:-main}"
    BRANCH="${RESPOSTA}"

    perguntar "Caminho da pasta dentro do repositório (ex: scripts/backup)" "${REMOTE_FOLDER}"
    REMOTE_FOLDER="${RESPOSTA}"

    perguntar "Diretório de destino local" "${DEST_DIR:-.}"
    DEST_DIR="${RESPOSTA}"
}

# Modo por argumentos/perfil: só pergunta o que é obrigatório e falta
pedir_em_falta() {
    [[ -n "${REPO_URL}" ]]      || read -r -p "URL do repositório Git (HTTPS ou SSH): " REPO_URL
    [[ -n "${REMOTE_FOLDER}" ]] || read -r -p "Caminho da pasta dentro do repositório (ex: scripts/backup): " REMOTE_FOLDER
}

normalizar_e_validar() {
    # Remover barras iniciais ou finais extras
    REMOTE_FOLDER="${REMOTE_FOLDER#/}"
    REMOTE_FOLDER="${REMOTE_FOLDER%/}"
    BRANCH="${BRANCH:-main}"
    DEST_DIR="${DEST_DIR:-.}"

    [[ -n "${REPO_URL}" ]]      || erro "O URL do repositório é obrigatório."
    [[ -n "${REMOTE_FOLDER}" ]] || erro "O caminho da pasta no repositório é obrigatório."
}

escolher_perfil() {
    local perfis=() opcao
    mapfile -t perfis < <(listar_perfis)
    (( ${#perfis[@]} > 0 )) || return 0

    echo "Perfis guardados:"
    echo ""
    mostrar_perfis
    echo ""
    read -r -p "Escolha o n.º ou nome do perfil (Enter para novo): " opcao
    [[ -z "${opcao}" ]] && return 0

    if [[ "${opcao}" =~ ^[0-9]+$ ]] && (( opcao >= 1 && opcao <= ${#perfis[@]} )); then
        carregar_perfil "${perfis[$((opcao - 1))]}"
    else
        carregar_perfil "${opcao}"
    fi
    echo ""
    echo "Perfil '${PERFIL}' carregado (Enter mantém o valor entre [ ])."
    echo ""
}

aviso_credenciais() {
    # Devolve 0 se pode guardar
    local resp
    if [[ "${REPO_URL}" =~ ^https?://[^/@]+:[^/@]+@ ]]; then
        echo "Aviso: o URL tem credenciais (utilizador:password/token) e ficariam guardadas no perfil."
        if [[ -t 0 ]]; then
            read -r -p "Guardar mesmo assim? [s/N]: " resp
            [[ "${resp,,}" == "s" ]] && return 0
        fi
        echo "Perfil não guardado."
        return 1
    fi
    return 0
}

perguntar_guardar() {
    local resp
    read -r -p "Guardar estas configurações num perfil? [S/n]: " resp
    [[ "${resp,,}" == "n" ]] && return 0
    aviso_credenciais || return 0

    perguntar "Nome do perfil" "${PERFIL:-$(basename "${REMOTE_FOLDER}")}"
    guardar_perfil "${RESPOSTA}"
    PERFIL="${RESPOSTA}"
}

# ------------------------------------------------------------------------------
# Extração
# ------------------------------------------------------------------------------
extrair() {
    echo ""
    echo "A preparar extração:"
    [[ -n "${PERFIL}" ]] && echo "  Perfil      : ${PERFIL}"
    echo "  Repositório : ${REPO_URL}"
    echo "  Branch      : ${BRANCH}"
    echo "  Pasta Remota: ${REMOTE_FOLDER}"
    echo "  Destino     : ${DEST_DIR}"
    echo ""

    # Criar diretório temporário isolado e garantir limpeza ao sair
    TEMP_DIR=$(mktemp -d)
    trap 'rm -rf "${TEMP_DIR}"' EXIT

    # Clonar apenas a estrutura sem ficheiros (sparse-checkout)
    echo "A contactar repositório..."
    git clone --quiet --filter=blob:none --no-checkout --depth 1 --branch "${BRANCH}" "${REPO_URL}" "${TEMP_DIR}"

    git -C "${TEMP_DIR}" sparse-checkout init --cone
    git -C "${TEMP_DIR}" sparse-checkout set "${REMOTE_FOLDER}"
    git -C "${TEMP_DIR}" checkout --quiet "${BRANCH}"

    # Validar se a pasta existe no repositório
    [[ -d "${TEMP_DIR}/${REMOTE_FOLDER}" ]] \
        || erro "A pasta '${REMOTE_FOLDER}' não foi encontrada na branch '${BRANCH}'."

    # Copiar o conteúdo da pasta para o destino
    mkdir -p "${DEST_DIR}"
    cp -a "${TEMP_DIR}/${REMOTE_FOLDER}/." "${DEST_DIR}/"

    echo ""
    echo "Concluído com sucesso! Os ficheiros de '${REMOTE_FOLDER}' foram extraídos para '${DEST_DIR}'."
}

# ------------------------------------------------------------------------------
# Principal
# ------------------------------------------------------------------------------
ARG_URL=""; ARG_BRANCH=""; ARG_PASTA=""; ARG_DESTINO=""; ARGS=0
USAR_PERFIL=""; GUARDAR_PERFIL=""; EDITAR_PERFIL=""

while getopts ":r:b:p:d:c:s:e:x:lh" opt; do
    case "${opt}" in
        r) ARG_URL="${OPTARG}"; ARGS=1 ;;
        b) ARG_BRANCH="${OPTARG}"; ARGS=1 ;;
        p) ARG_PASTA="${OPTARG}"; ARGS=1 ;;
        d) ARG_DESTINO="${OPTARG}"; ARGS=1 ;;
        c) USAR_PERFIL="${OPTARG}" ;;
        s) GUARDAR_PERFIL="${OPTARG}"; validar_nome_perfil "${OPTARG}" ;;
        e) EDITAR_PERFIL="${OPTARG}" ;;
        x) apagar_perfil "${OPTARG}"; exit 0 ;;
        l) mostrar_perfis; exit 0 ;;
        h) uso; exit 0 ;;
        :) erro "A opção -${OPTARG} precisa de um valor." ;;
        *) echo "Opção inválida: -${OPTARG}" >&2; uso >&2; exit 2 ;;
    esac
done

command -v git >/dev/null 2>&1 || erro "O git não está instalado."

echo "=== Extrator de Pasta de Repositório Git (v${VERSAO}) ==="
echo ""

# Editar um perfil (sem extrair)
if [[ -n "${EDITAR_PERFIL}" ]]; then
    carregar_perfil "${EDITAR_PERFIL}"
    pedir_dados
    normalizar_e_validar
    aviso_credenciais || exit 0
    guardar_perfil "${EDITAR_PERFIL}"
    exit 0
fi

if [[ -n "${USAR_PERFIL}" || ${ARGS} -eq 1 ]]; then
    # Perfil e/ou argumentos: sem menu; os argumentos sobrepõem o perfil
    [[ -n "${USAR_PERFIL}" ]] && carregar_perfil "${USAR_PERFIL}"
    [[ -n "${ARG_URL}" ]]     && REPO_URL="${ARG_URL}"
    [[ -n "${ARG_BRANCH}" ]]  && BRANCH="${ARG_BRANCH}"
    [[ -n "${ARG_PASTA}" ]]   && REMOTE_FOLDER="${ARG_PASTA}"
    [[ -n "${ARG_DESTINO}" ]] && DEST_DIR="${ARG_DESTINO}"
    pedir_em_falta
    normalizar_e_validar
    extrair
    if [[ -n "${GUARDAR_PERFIL}" ]]; then
        echo ""
        if aviso_credenciais; then guardar_perfil "${GUARDAR_PERFIL}"; fi
    fi
else
    # Interativo: menu de perfis, perguntas e opção de guardar
    escolher_perfil
    pedir_dados
    normalizar_e_validar
    extrair
    echo ""
    if [[ -n "${GUARDAR_PERFIL}" ]]; then
        if aviso_credenciais; then guardar_perfil "${GUARDAR_PERFIL}"; fi
    else
        perguntar_guardar
    fi
fi
