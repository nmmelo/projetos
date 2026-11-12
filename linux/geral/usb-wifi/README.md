# Internet na VM (VirtualBox) através de um equipamento USB

Configuração de uma VM Ubuntu no VirtualBox (anfitrião Windows) para sair para a internet por uma
pen LTE USB passada diretamente para a VM, sem passar pela rede do trabalho. Em casa a VM continua
a usar o NAT do VirtualBox.

O equipamento é uma **Alcatel OneTouch L850V / Telekom Speedstick LTE** (`1bbb:0195`). No Ubuntu aparece
como uma placa ethernet (`enx...`) e não como wifi.

## Ficheiros

| Ficheiro | Descrição |
|---|---|
| [`60-lte.yaml`](60-lte.yaml) | Netplan da pen LTE: DHCP, DNS 8.8.8.8/1.1.1.1, match `enx*`, opcional no arranque |
| [`rede.sh`](rede.sh) | Funções `rede-trabalho` e `rede-casa` para alternar entre a pen e o NAT |
| [`CONVERSA.md`](CONVERSA.md) | Resumo do chat que deu origem a esta configuração |

## Placas de rede da VM

| Placa | Interface | Uso |
|---|---|---|
| NAT | `enp0s3` | Internet em casa (pela rede do Windows) |
| Host-only | `enp0s8` (192.168.56.x) | Ligação VM ↔ Windows (SSH, etc.) |
| USB passthrough | `enx*` | Internet no trabalho pela pen LTE |

## 1. VirtualBox (anfitrião)

1. VM desligada → **Definições → USB** → ativar o controlador USB (3.0/xHCI; se der erro sem o
   Extension Pack, usar o **USB 1.1**).
2. Botão **+** → escolher a pen Alcatel. Fica um **filtro**: sempre que a pen é ligada ao PC passa
   para a VM (o Windows deixa de a ver).
3. Manter a placa NAT ativa (é a de casa); no trabalho é desligada dentro do Ubuntu.

> Instalar o Extension Pack precisa de direitos de administrador no Windows. Sem eles, usar o USB 1.1.

## 2. Ubuntu (VM)

```bash
# confirmar que a pen é reconhecida
lsusb | grep -i alcatel
ip -br a            # deve aparecer enx...

# netplan da pen
sudo cp 60-lte.yaml /etc/netplan/60-lte.yaml
sudo chmod 600 /etc/netplan/60-lte.yaml
sudo netplan apply

# funções de alternância
cp rede.sh ~/.rede.sh
echo '[ -f ~/.rede.sh ] && . ~/.rede.sh' >> ~/.bashrc
source ~/.bashrc
```

> No Ubuntu recente o `dhclient` não vem instalado; o IP é pedido pelo netplan/systemd-networkd.

## 3. Utilização

| Onde | Comando | Resultado |
|---|---|---|
| Trabalho | `rede-trabalho` | NAT em baixo, internet só pela pen LTE |
| Casa | `rede-casa` | NAT ligado (a pen não está presente) |

Ao reiniciar a VM o NAT volta a ficar ligado: no trabalho correr `rede-trabalho` depois do arranque.

Verificação:

```bash
ip route            # default via 192.168.1.1 dev enx...
ping -c3 google.com
sudo apt-get update
```

## Problemas encontrados

| Sintoma | Causa | Solução |
|---|---|---|
| `ping 8.8.8.8` funciona, `ping google.com` não; `apt-get update` fica em "Connecting" | A rede do trabalho bloqueia DNS externo pelo NAT | Sair pela pen LTE (`rede-trabalho`) |
| `enx...` em `DOWN` / sem IP | Interface não configurada | Netplan `60-lte.yaml` + `ip link set ... up` |
| Erro de permissões ao instalar o Extension Pack | Sem administrador no Windows | Usar controlador USB 1.1 ou pedir ao suporte |
| A pen não dá IP | Dados móveis/PIN | Página da pen em `http://192.168.1.1` |

Diagnóstico de DNS:

```bash
resolvectl query google.com
dig @8.8.8.8 google.com +short     # DNS externo
dig @10.0.2.3 google.com +short    # DNS do NAT do VirtualBox
```

## Segurança

- No trabalho o NAT fica em baixo, por isso o tráfego da VM não passa pela rede da empresa.
- Desligar no Ubuntu é reversível por quem tiver root na VM. Para garantia total, desligar a placa
  NAT nas definições do VirtualBox (`VBoxManage modifyvm "VM" --nic1 none`).
- A placa host-only só liga a VM ao Windows. Pastas e área de transferência partilhadas continuam
  a ligar a VM ao anfitrião.
