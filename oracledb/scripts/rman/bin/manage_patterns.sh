#!/usr/bin/env bash
# Mostra e acrescenta padrões nas listas de erro (tipos_error.txt) e de sucesso (tipos_success.txt).
set -uo pipefail
umask 027

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)/config"
ERROR_FILE="${CONFIG_DIR}/tipos_error.txt"
SUCCESS_FILE="${CONFIG_DIR}/tipos_success.txt"

while true; do
    echo
    echo "Que lista pretende ver/alterar?"
    echo "  1) Erro (${ERROR_FILE##*/})"
    echo "  2) Sem erro / sucesso (${SUCCESS_FILE##*/})"
    echo "  0) Sair"
    read -r -p "Opção: " opt
    case "$opt" in
        1) file="$ERROR_FILE" ;;
        2) file="$SUCCESS_FILE" ;;
        0|"") exit 0 ;;
        *) echo "Opção inválida."; continue ;;
    esac
    touch -- "$file"
    echo "--- Conteúdo atual de ${file##*/} ---"
    if [[ -s "$file" ]]; then nl -ba -- "$file"; else echo "(vazio)"; fi
    echo "-------------------------------------"
    read -r -p "Novo padrão a acrescentar (vazio = nenhum): " pattern
    [[ -z "$pattern" ]] && continue
    if grep -qxF -- "$pattern" "$file"; then
        echo "O padrão já existe."
        continue
    fi
    # Garante quebra de linha final antes de acrescentar
    [[ -s "$file" && -n "$(tail -c1 -- "$file")" ]] && echo >> "$file"
    printf '%s\n' "$pattern" >> "$file"
    echo "Padrão acrescentado."
done
