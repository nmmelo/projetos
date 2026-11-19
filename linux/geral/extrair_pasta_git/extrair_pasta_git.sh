#!/usr/bin/env bash
#
# Script para clonar/extrair apenas uma pasta específica de um repositório Git
# diretamente para um diretório de destino (sem histórico .git).
#
# Uso:
#   ./extrair_pasta_git.sh                                   interativo (pergunta tudo)
#   ./extrair_pasta_git.sh -r URL [-b BRANCH] -p PASTA [-d DESTINO]
# O que nao for passado como argumento e perguntado.
#

set -euo pipefail

REPO_URL=""; BRANCH=""; REMOTE_FOLDER=""; DEST_DIR=""; ARGS=0
while getopts ":r:b:p:d:h" opt; do
    case "${opt}" in
        r) REPO_URL="${OPTARG}"; ARGS=1 ;;
        b) BRANCH="${OPTARG}"; ARGS=1 ;;
        p) REMOTE_FOLDER="${OPTARG}"; ARGS=1 ;;
        d) DEST_DIR="${OPTARG}"; ARGS=1 ;;
        h) sed -n '6,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Opção inválida: -${OPTARG}" >&2; exit 2 ;;
    esac
done

echo "=== Extrator de Pasta de Repositório Git ==="
echo ""

# 1. Pedir os inputs ao utilizador (so os que nao vieram como argumento)
[[ -n "${REPO_URL}" ]] || read -r -p "URL do repositório Git (HTTPS ou SSH): " REPO_URL
if [[ -z "${REPO_URL}" ]]; then
    echo "Erro: O URL do repositório é obrigatório."
    exit 1
fi

if [[ -z "${BRANCH}" && ${ARGS} -eq 0 ]]; then
    read -r -p "Branch (deixe em branco para 'main'): " BRANCH
fi
BRANCH="${BRANCH:-main}"

[[ -n "${REMOTE_FOLDER}" ]] || read -r -p "Caminho da pasta dentro do repositório (ex: scripts/backup): " REMOTE_FOLDER
# Remover barras iniciais ou finais extras
REMOTE_FOLDER="${REMOTE_FOLDER#/}"
REMOTE_FOLDER="${REMOTE_FOLDER%/}"

if [[ -z "${REMOTE_FOLDER}" ]]; then
    echo "Erro: O caminho da pasta no repositório é obrigatório."
    exit 1
fi

if [[ -z "${DEST_DIR}" && ${ARGS} -eq 0 ]]; then
    read -r -p "Diretório de destino local (deixe em branco para pasta atual '.'): " DEST_DIR
fi
DEST_DIR="${DEST_DIR:-.}"

echo ""
echo "A preparar extração:"
echo "  Repositório : ${REPO_URL}"
echo "  Branch      : ${BRANCH}"
echo "  Pasta Remota: ${REMOTE_FOLDER}"
echo "  Destino     : ${DEST_DIR}"
echo ""

# 2. Criar diretório temporário isolado e garantir limpeza ao sair
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "${TEMP_DIR}"' EXIT

# 3. Clonar apenas a estrutura sem ficheiros (sparse-checkout)
echo "A contactar repositório..."
git clone --quiet --filter=blob:none --no-checkout --depth 1 --branch "${BRANCH}" "${REPO_URL}" "${TEMP_DIR}"

cd "${TEMP_DIR}"
git sparse-checkout init --cone
git sparse-checkout set "${REMOTE_FOLDER}"
git checkout --quiet "${BRANCH}"

# 4. Validar se a pasta existe no repositório
if [[ ! -d "${TEMP_DIR}/${REMOTE_FOLDER}" ]]; then
    echo "Erro: A pasta '${REMOTE_FOLDER}' não foi encontrada na branch '${BRANCH}'."
    exit 1
fi

# 5. Copiar o conteúdo da pasta para o destino
mkdir -p "${DEST_DIR}"
cp -a "${TEMP_DIR}/${REMOTE_FOLDER}/." "${DEST_DIR}/"

echo ""
echo "Concluído com sucesso! Os ficheiros de '${REMOTE_FOLDER}' foram extraídos para '${DEST_DIR}'."
