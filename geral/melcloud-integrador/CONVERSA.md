# Conversa: melcloud-integrador

Chat de 22 de novembro de 2026.

---

## 1. Pergunta

> O MELCloud tem api para usar

**Resposta:** não existe API pública oficial, mas há APIs não oficiais (engenharia inversa) estáveis e muito usadas.
São duas plataformas diferentes:

| Plataforma | API | Bibliotecas / integrações |
|---|---|---|
| MELCloud (clássico) | Não documentada, obtida por engenharia inversa; estável | Python: `pymelcloud`, `melcloud`; Home Assistant: `melcloud` |
| MELCloud Home (adaptadores MAC-5xx) | Sem API pública; OAuth via AWS Cognito + REST | Home Assistant: `melcloud_home`; HACS: `andrew-blake/melcloudhome` |
| Ambas | — | Node.js: `@olivierzal/melcloud-api` |

Cuidados referidos:

- a Mitsubishi impõe limites à API e bloqueia contas com polling a mais (usar 3–6 min ou mais);
- por não ser oficial, pode mudar sem aviso;
- alternativa sem cloud: adaptador ESP32 na porta CN105 da unidade (MQTT/ESPHome).

## 2. Pedido

> Eu uso a app MELCloud

**Resposta:** criado o `melcloud.py`, cliente Python só com a biblioteca padrão:

| Comando | Função |
|---|---|
| `list` | Todos os equipamentos (edifícios, pisos, áreas) |
| `status` | Estado de unidades ATA ou ATW, em tabela ou JSON |
| `set` | Liga/desliga, modo, temperatura, ventilador e lâminas (ATA) |

Decisões:

- credenciais em `MELCLOUD_USER` / `MELCLOUD_PASS`;
- `ContextKey` guardado em `~/.melcloud_token` (600) com novo login automático em caso de 401;
- erro 429 tratado com aviso de bloqueio temporário.

## 3. Repositório

> Podes colocar no repositório projeto sem referência a Claude, em geral, com o nome de MELCloud integrador com apache 2.0

Projeto colocado em `geral/melcloud-integrador`, com cabeçalho Apache 2.0 no script, `README.md` e entradas nos
READMEs da raiz e de `geral/`.

## Próximos passos possíveis

- Suporte de `set` para bombas de calor ar-água (temperatura da zona e da água quente)
- Execução num contentor Docker no Synology
