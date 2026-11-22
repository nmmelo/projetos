# MELCloud integrador

Cliente em Python para a API do **MELCloud clássico** (Mitsubishi Electric): lista os equipamentos da conta, mostra o
estado de cada unidade e controla o ar condicionado (ligar/desligar, modo, temperatura, ventilador e lâminas).

Só usa a biblioteca padrão do Python 3, sem dependências.

> A API do MELCloud **não é oficial nem documentada** pela Mitsubishi: foi obtida por engenharia inversa da app e é
> usada por vários projetos (Home Assistant, Node-RED, ioBroker). Pode mudar sem aviso.
>
> Este script é para o **MELCloud clássico**. O **MELCloud Home** (adaptadores Wi-Fi MAC-5xx mais recentes) usa uma
> API diferente e não é suportado.

## Ficheiros

| Ficheiro | Descrição |
|---|---|
| `melcloud.py` | Cliente de linha de comandos (`list`, `status`, `set`) |
| `CONVERSA.md` | Resumo do chat que deu origem ao projeto |

## Requisitos

- Python 3.8 ou superior
- Conta MELCloud com os equipamentos já configurados na app
- Acesso à internet (`app.melcloud.com`)

## Configuração

As credenciais são lidas de variáveis de ambiente:

```bash
export MELCLOUD_USER="<email>"
export MELCLOUD_PASS="<password>"
```

Para não as escrever na shell, podem ficar num ficheiro protegido:

```bash
cat > ~/.melcloud.env <<'EOF'
export MELCLOUD_USER="<email>"
export MELCLOUD_PASS="<password>"
EOF
chmod 600 ~/.melcloud.env
source ~/.melcloud.env
```

Depois do primeiro login, a chave de sessão (`ContextKey`) fica guardada em `~/.melcloud_token` (permissões `600`).
Quando expira, o script volta a fazer login automaticamente.

## Utilização

```bash
chmod +x melcloud.py

./melcloud.py list                      # lista os equipamentos
./melcloud.py status <DeviceID>         # estado em tabela
./melcloud.py status <DeviceID> --json  # JSON completo devolvido pela API
./melcloud.py set <DeviceID> --power on --mode cool --temp 22 --fan 3
./melcloud.py set <DeviceID> --power off
```

### Comandos

| Comando | Descrição |
|---|---|
| `list` | Lista todos os equipamentos (edifícios, pisos e áreas): `DeviceID`, `BuildingID`, nome e tipo |
| `status <id>` | Estado de uma unidade ar-ar (ATA) ou ar-água (ATW); outros tipos em JSON |
| `set <id> ...` | Altera uma unidade ar-ar (ATA); só envia os campos indicados |

### Opções do `set`

| Opção | Valores |
|---|---|
| `--power` | `on`, `off` |
| `--mode` | `heat`, `dry`, `cool`, `fan`, `auto` |
| `--temp` | Temperatura em °C (ex. `21.5`) |
| `--fan` | `0` = auto, `1` a `5` |
| `--vane-v` | Lâmina vertical: `0` = auto, `1` a `5`, `7` = swing |
| `--vane-h` | Lâmina horizontal: `0` = auto, `1` a `5`, `8` = split, `12` = swing |

### Tipos de equipamento

| Código | Tipo |
|---|---|
| `0` | ATA — ar-ar (ar condicionado) |
| `1` | ATW — ar-água (bomba de calor, ex. Ecodan) |
| `3` | ERV — ventilação com recuperação de energia |

## Como funciona

| Passo | Endpoint |
|---|---|
| Login | `POST /Mitsubishi.Wifi.Client/Login/ClientLogin` → devolve o `ContextKey` |
| Listar | `GET /User/ListDevices` (cabeçalho `X-MitsContextKey`) |
| Estado | `GET /Device/Get?id=<DeviceID>&buildingID=<BuildingID>` |
| Alterar | `POST /Device/SetAta` com o estado completo, `EffectiveFlags` (campos alterados) e `HasPendingCommand=true` |

`EffectiveFlags` usados: `0x01` power, `0x02` modo, `0x04` temperatura, `0x08` ventilador, `0x10` lâmina vertical,
`0x100` lâmina horizontal.

## Cuidados

- **Limite de pedidos:** a Mitsubishi bloqueia temporariamente (várias horas) contas com pedidos a mais. Em `cron`,
  usar intervalos de **5 minutos ou mais**.
- **Erro 429:** o script termina com aviso; esperar antes de tentar de novo.
- **Comandos:** a unidade demora alguns segundos a aplicar o comando; o estado devolvido pelo `set` é o pedido, não
  necessariamente o já aplicado.

## Exemplo em cron

Ligar o ar condicionado em aquecimento a 21 °C nos dias úteis às 7h00:

```cron
0 7 * * 1-5  . $HOME/.melcloud.env && /caminho/melcloud.py set <DeviceID> --power on --mode heat --temp 21 >> $HOME/melcloud.log 2>&1
```

## Licença

[Apache License 2.0](../../LICENSE) — Copyright 2026 Nuno Melo. Ver [NOTICE](../../NOTICE).
