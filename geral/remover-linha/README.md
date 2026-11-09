# Remover-linha

Script Python que **remove de um ficheiro todas as linhas que contenham um endereço IP**. Pergunta o ficheiro
e o IP, mostra as linhas encontradas, pede confirmação e faz backup antes de alterar.

Pensado para limpar IPs de ficheiros de configuração, por exemplo o `defaults/main.yml` de uma role Ansible
(ex.: `roles/firewall-configuration/defaults/main.yml`).

## Ficheiros

| Ficheiro | Descrição |
|---|---|
| [`remover_ip.py`](remover_ip.py) | O script (Python 3, só biblioteca padrão) |
| [`test_remover_ip.py`](test_remover_ip.py) | Testes automáticos (`pytest`) |
| [`exemplo/main.yml`](exemplo/main.yml) | Ficheiro de exemplo para experimentar |
| [`CONVERSA.md`](CONVERSA.md) | Resumo do chat que deu origem ao script |

## Requisitos

| Requisito | Notas |
|---|---|
| Python 3.8+ | Só biblioteca padrão, não precisa de `pip` |
| `pytest` | Só para correr os testes (opcional) |

## Utilização

```bash
chmod +x remover_ip.py
./remover_ip.py                       # pergunta o ficheiro e o IP
```

| Opção | Descrição |
|---|---|
| `-f`, `--ficheiro FICHEIRO` | Ficheiro a alterar (se omitido, é perguntado) |
| `-i`, `--ip IP` | IP a remover (se omitido, é perguntado) |
| `-y`, `--yes` | Não pedir confirmação |
| `--dry-run` | Só mostra as linhas que seriam removidas, não altera nada |
| `--sem-backup` | Não fazer backup |
| `--manter-backups N` | Quantos backups manter (predefinição: `1`, só o último) |
| `-h`, `--help` | Ajuda |

### Exemplo interativo

```
$ ./remover_ip.py
Ficheiro: roles/firewall-configuration/defaults/main.yml
IP a remover: "10.31.240.4"

Linhas com 10.31.240.4 em /home/nmmelo/ansible/roles/firewall-configuration/defaults/main.yml:
     2: servidor_antigo: "10.31.240.4"
     4: lista_ips: ["1.1.1.1", "10.31.240.4"]

Remover 2 linha(s)? [s/N] s
Backup: /home/nmmelo/ansible/roles/firewall-configuration/defaults/main.yml.20261109_134252.bak
Removidas 2 linha(s) com 10.31.240.4.
```

Se o ficheiro não existir ou o IP for inválido, volta a perguntar.

### Sem perguntas (scripts, cron, Ansible)

```bash
./remover_ip.py -f roles/firewall-configuration/defaults/main.yml -i 10.31.240.4 -y
```

### Só ver o que seria removido

```bash
./remover_ip.py -f exemplo/main.yml -i 10.31.240.4 --dry-run
```

```
Linhas com 10.31.240.4 em .../exemplo/main.yml:
     5: servidor_antigo: "10.31.240.4"
     7: lista_ips: ["1.1.1.1", "10.31.240.4"]
     9: url_api: "http://10.31.240.4:8080/api"

(dry-run) Seriam removidas 3 linha(s). Nada foi alterado.
```

## Regras de procura do IP

| Linha no ficheiro | Com o IP `10.31.240.4` |
|---|---|
| `servidor: "10.31.240.4"` | ✅ apagada |
| `servidor: '10.31.240.4'` | ✅ apagada |
| `lista: ["1.1.1.1", "10.31.240.4"]` | ✅ apagada (a linha inteira) |
| `url: "http://10.31.240.4:8080/api"` | ✅ apagada |
| `servidor: "10.31.240.40"` | ❌ fica |
| `servidor: 110.31.240.4` | ❌ fica |

- O IP pode ser escrito com ou sem aspas (`10.31.240.4`, `"10.31.240.4"`, `'10.31.240.4'`).
- Só o IP **exato** é apagado; IPs que o contêm como parte (`10.31.240.40`, `110.31.240.4`) ficam.
- É apagada a **linha inteira**, mesmo que tenha outros valores.
- Pensado para IPv4. IPv6 também é aceite, procurado na forma abreviada (ex.: `2001:db8::1`).

## Backups

- Antes de alterar, cria `<ficheiro>.AAAAMMDD_HHMMSS.bak` na mesma pasta.
- Só **depois** de o backup novo existir é que apaga os anteriores, mantendo os `--manter-backups` mais
  recentes (por omissão, só o último).
- A escrita do ficheiro é atómica (ficheiro temporário + `os.replace`) e mantém as permissões originais.

Restaurar:

```bash
cp main.yml.20261109_134252.bak main.yml
```

## Códigos de saída

| Código | Situação |
|---|---|
| `0` | Linhas removidas, nenhuma linha encontrada ou `--dry-run` |
| `1` | Cancelado, ficheiro não encontrado ou IP inválido (com `-f` / `-i`) |

## Testes

```bash
pip install pytest
pytest -q geral/remover-linha
```

Cobrem: IP com aspas, IP exato, remoção com backup, `--dry-run`, manutenção só do último backup, modo
interativo, cancelamento, IP inexistente e argumentos inválidos.

## Cuidados

- Em ficheiros YAML, apagar a primeira linha de uma variável com várias linhas pode deixar o ficheiro inválido;
  confirmar com `--dry-run` antes.
- O ficheiro é lido e escrito em UTF-8.
