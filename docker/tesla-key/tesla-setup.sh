#!/bin/bash
# tesla-setup — passos da Tesla Fleet API dentro do contentor tesla-key
# Configuração: /opt/tesla/tesla.env | chaves: /opt/tesla/keys | tokens: /opt/tesla/tokens.json
set -euo pipefail

BASE="${TESLA_BASE:-/opt/tesla}"
CONF="${BASE}/tesla.env"
KEYS="${BASE}/keys"
TOKENS="${BASE}/tokens.json"
WEBROOT="/var/www/html/.well-known/appspecific"
PUBNAME="com.tesla.3p.public-key.pem"

die()  { echo "ERRO: $*" >&2; exit 1; }
info() { echo "==> $*"; }

carregar_conf() {
    [ -f "$CONF" ] || die "falta $CONF (copiar de tesla.env.example)"
    # shellcheck disable=SC1090
    set -a; . "$CONF"; set +a
    : "${TESLA_DOMAIN:?TESLA_DOMAIN vazio em $CONF}"
    TESLA_REGION="${TESLA_REGION:-eu}"
    case "$TESLA_REGION" in
        eu) AUDIENCE="https://fleet-api.prd.eu.vn.cloud.tesla.com" ;;
        na) AUDIENCE="https://fleet-api.prd.na.vn.cloud.tesla.com" ;;
        *)  die "TESLA_REGION inválida: $TESLA_REGION (eu|na)" ;;
    esac
    TESLA_REDIRECT_URI="${TESLA_REDIRECT_URI:-http://localhost:8080/callback}"
    TESLA_SCOPES="${TESLA_SCOPES:-openid offline_access vehicle_device_data vehicle_location vehicle_cmds vehicle_charging_cmds}"
    TESLA_AUTH_URL="${TESLA_AUTH_URL:-https://auth.tesla.com/oauth2/v3/authorize}"
    TESLA_TOKEN_URL="${TESLA_TOKEN_URL:-https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token}"
}

precisa_credenciais() {
    [ -n "${TESLA_CLIENT_ID:-}" ]     || die "TESLA_CLIENT_ID vazio em $CONF"
    [ -n "${TESLA_CLIENT_SECRET:-}" ] || die "TESLA_CLIENT_SECRET vazio em $CONF"
}

urlenc() { jq -rn --arg v "$1" '$v|@uri'; }

access_token() {
    [ -f "$TOKENS" ] || die "sem tokens; correr: tesla-setup url  e depois  tesla-setup token <code>"
    jq -r '.access_token' "$TOKENS"
}

guardar_tokens() {
    local resp="$1"
    echo "$resp" | jq -e '.access_token' >/dev/null 2>&1 || { echo "$resp" | jq . 2>/dev/null || echo "$resp"; die "resposta sem access_token"; }
    umask 077
    echo "$resp" | jq --arg t "$(date -Iseconds)" '. + {obtido_em: $t}' > "$TOKENS"
    info "tokens guardados em $TOKENS (expira em $(echo "$resp" | jq -r '.expires_in')s)"
}

# ---------------------------------------------------------------------------

cmd_chaves() {
    mkdir -p "$KEYS"
    if [ -f "$KEYS/private-key.pem" ]; then
        info "já existe $KEYS/private-key.pem (não regenerar depois de emparelhar)"
    else
        info "a gerar par de chaves EC prime256v1"
        ( umask 077; openssl ecparam -name prime256v1 -genkey -noout -out "$KEYS/private-key.pem" )
    fi
    openssl ec -in "$KEYS/private-key.pem" -pubout -out "$KEYS/public-key.pem" 2>/dev/null
    mkdir -p "$WEBROOT"
    cp "$KEYS/public-key.pem" "$WEBROOT/$PUBNAME"
    chmod 644 "$WEBROOT/$PUBNAME"
    info "chave pública publicada em $WEBROOT/$PUBNAME"
    echo "    privada: $KEYS/private-key.pem  -> copiar para o tesla-http-proxy e guardar cópia offline"
}

cmd_verificar() {
    local url="https://${TESLA_DOMAIN}/.well-known/appspecific/${PUBNAME}"
    info "GET $url"
    local remoto
    remoto=$(curl -fsS --max-time 15 "$url") || die "não acessível de fora (DNS, 443, certificado, Proxy Reverso)"
    if [ -f "$KEYS/public-key.pem" ] && [ "$remoto" = "$(cat "$KEYS/public-key.pem")" ]; then
        info "OK: a chave publicada é igual à local"
    else
        echo "$remoto"
        echo "AVISO: a chave publicada não coincide com $KEYS/public-key.pem"
    fi
}

cmd_partner() {
    precisa_credenciais
    info "a obter token de partner"
    local resp ptoken
    resp=$(curl -sS -X POST "$TESLA_TOKEN_URL" \
        -d grant_type=client_credentials \
        -d client_id="$TESLA_CLIENT_ID" -d client_secret="$TESLA_CLIENT_SECRET" \
        --data-urlencode "scope=openid vehicle_device_data vehicle_location vehicle_cmds vehicle_charging_cmds" \
        -d audience="$AUDIENCE")
    ptoken=$(echo "$resp" | jq -r '.access_token // empty')
    [ -n "$ptoken" ] || { echo "$resp"; die "falhou o token de partner"; }

    info "a registar o domínio $TESLA_DOMAIN em $AUDIENCE"
    curl -sS -X POST "$AUDIENCE/api/1/partner_accounts" \
        -H "Authorization: Bearer $ptoken" -H "Content-Type: application/json" \
        -d "{\"domain\":\"${TESLA_DOMAIN}\"}" | jq .

    info "chave pública que a Tesla tem registada para o domínio"
    curl -sS "$AUDIENCE/api/1/partner_accounts/public_key?domain=$(urlenc "$TESLA_DOMAIN")" \
        -H "Authorization: Bearer $ptoken" | jq .
}

cmd_url() {
    [ -n "${TESLA_CLIENT_ID:-}" ] || die "TESLA_CLIENT_ID vazio em $CONF"
    local state; state=$(openssl rand -hex 8)
    echo "Abrir no browser, fazer login e copiar o parâmetro 'code' do URL de redirect:"
    echo
    echo "${TESLA_AUTH_URL}?response_type=code&client_id=$(urlenc "$TESLA_CLIENT_ID")&redirect_uri=$(urlenc "$TESLA_REDIRECT_URI")&scope=$(urlenc "$TESLA_SCOPES")&state=${state}&locale=pt-PT"
    echo
    echo "Depois: tesla-setup token <code>"
}

cmd_token() {
    precisa_credenciais
    local code="${1:-}"
    [ -n "$code" ] || die "uso: tesla-setup token <code>"
    # aceita o URL de redirect inteiro
    if [[ "$code" == *"code="* ]]; then code=$(echo "$code" | sed -E 's/.*[?&]code=([^&]+).*/\1/'); fi
    info "a trocar o code por tokens"
    guardar_tokens "$(curl -sS -X POST "$TESLA_TOKEN_URL" \
        -d grant_type=authorization_code \
        -d client_id="$TESLA_CLIENT_ID" -d client_secret="$TESLA_CLIENT_SECRET" \
        -d code="$code" -d audience="$AUDIENCE" \
        --data-urlencode "redirect_uri=$TESLA_REDIRECT_URI")"
}

cmd_refresh() {
    [ -n "${TESLA_CLIENT_ID:-}" ] || die "TESLA_CLIENT_ID vazio em $CONF"
    [ -f "$TOKENS" ] || die "sem tokens para renovar"
    local rt; rt=$(jq -r '.refresh_token // empty' "$TOKENS")
    [ -n "$rt" ] || die "tokens.json sem refresh_token (falta o scope offline_access?)"
    info "a renovar o access token"
    guardar_tokens "$(curl -sS -X POST "$TESLA_TOKEN_URL" \
        -d grant_type=refresh_token -d client_id="$TESLA_CLIENT_ID" -d refresh_token="$rt")"
}

cmd_emparelhar() {
    echo "No telemóvel (com a app Tesla instalada), abrir e aprovar:"
    echo
    echo "https://tesla.com/_ak/${TESLA_DOMAIN}"
}

cmd_veiculos() {
    curl -sS "$AUDIENCE/api/1/vehicles" -H "Authorization: Bearer $(access_token)" | jq .
}

cmd_estado() {
    echo "Domínio : $TESLA_DOMAIN"
    echo "Região  : $TESLA_REGION ($AUDIENCE)"
    echo "Client  : ${TESLA_CLIENT_ID:-(vazio)}"
    printf "Chaves  : "; [ -f "$KEYS/private-key.pem" ] && echo "ok ($KEYS)" || echo "não geradas"
    printf "Publicada: "; [ -f "$WEBROOT/$PUBNAME" ] && echo "ok" || echo "não"
    if [ -f "$TOKENS" ]; then
        echo "Tokens  : obtidos em $(jq -r '.obtido_em // "?"' "$TOKENS"), expires_in $(jq -r '.expires_in' "$TOKENS")s"
    else
        echo "Tokens  : não"
    fi
}

ajuda() {
    cat <<EOF
tesla-setup <comando>

Ordem sugerida:
  chaves      gera o par EC e publica a chave pública
  verificar   testa a chave pelo domínio público (HTTPS)
  partner     regista o domínio como partner na Fleet API
  url         mostra o URL de autorização OAuth
  token CODE  troca o code (ou o URL de redirect inteiro) por tokens
  emparelhar  mostra o link para emparelhar a chave virtual no carro
  veiculos    lista os veículos (GET /api/1/vehicles)

Outros:
  refresh     renova o access token com o refresh token
  estado      resumo da configuração

Gateway (portal admin, clientes, Status API, logs):
  admin                         define utilizador/password do portal admin
  cliente add NOME [DESCRIÇÃO]  cria cliente e mostra a chave X-API-Key
  cliente list                  lista clientes
  cliente on|off|rm|chave NOME  ativa, desativa, apaga ou gera nova chave
  status-api on|off|token|info  Status API para outro portal (token Bearer)
  definicao [CHAVE [VALOR]]     cache_segundos, retencao_logs_dias
  logs [N] [NIVEL]              últimos N registos (INFO|WARNING|ERROR)
  logs-limpar                   apaga logs mais antigos que a retenção
  resumo                        estado do gateway (JSON)

Configuração: $CONF
EOF
}

# ---------------------------------------------------------------------------

cmd="${1:-ajuda}"; shift || true
GATEWAY_CLI="${GATEWAY_CLI:-/opt/gateway/cli.py}"
case "$cmd" in
    ajuda|-h|--help) ajuda; exit 0 ;;
    admin|status-api|definicao|logs|logs-limpar|resumo)
        exec python3 "$GATEWAY_CLI" "$cmd" "$@" ;;
    cliente)
        sub="${1:-list}"; shift || true
        exec python3 "$GATEWAY_CLI" "cliente-$sub" "$@" ;;
esac
carregar_conf
case "$cmd" in
    chaves)     cmd_chaves ;;
    verificar)  cmd_verificar ;;
    partner)    cmd_partner ;;
    url)        cmd_url ;;
    token)      cmd_token "$@" ;;
    refresh)    cmd_refresh ;;
    emparelhar) cmd_emparelhar ;;
    veiculos)   cmd_veiculos ;;
    estado)     cmd_estado ;;
    *)          ajuda; exit 1 ;;
esac
