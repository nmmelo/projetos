#!/usr/bin/env bash
#
# Script para clonar/extrair apenas uma pasta específica de um repositório Git
# diretamente para um diretório de destino (sem histórico .git).
#

set -euo pipefail

echo "=== Extrator de Pasta de Repositório Git ==="
echo ""

# 1. Pedir os inputs ao utilizador
read -r -p "URL do repositório Git (HTTPS ou SSH): " REPO_URL
if [[ -z "${REPO_URL}" ]]; then
    echo "Erro: O URL do repositório é obrigatório."
    exit 1
fi

read -r -p "Branch (deixe em branco para 'main'): " BRANCH
BRANCH="${BRANCH:-main}"

read -r -p "Caminho da pasta dentro do repositório (ex: scripts/backup): " REMOTE_FOLDER
# Remover barras iniciais ou finais extras
REMOTE_FOLDER="${REMOTE_FOLDER#/}"
REMOTE_FOLDER="${REMOTE_FOLDER%/}"

if [[ -z "${REMOTE_FOLDER}" ]]; then
    echo "Erro: O caminho da pasta no repositório é obrigatório."
    exit 1
fi

read -r -p "Diretório de destino local (deixe em branco para pasta atual '.'): " DEST_DIR
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
