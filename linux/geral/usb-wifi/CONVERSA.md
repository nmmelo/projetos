# Conversa: internet na VM através de um equipamento USB

Chat com o Claude, de 5 a 7 de novembro de 2026. As perguntas estão como foram escritas; as respostas
estão resumidas, mas mantêm os comandos e as conclusões. A configuração final e o README estão nesta pasta.

---

## 1. Ligar o equipamento USB à VM

**Pergunta:**
> posso numa vm no virtualbox associar a um router wifi, mas que está ligado por usb, no windows consigo ter internet, a ideia era ligar esse router a vm

**Resposta:** três opções.

| Opção | Quando usar |
|---|---|
| NAT | Só internet na VM, pela ligação do Windows |
| Placa em ponte | A VM precisa de IP próprio na rede do router |
| USB passthrough | A VM controla o dispositivo diretamente (o Windows deixa de o ver) |

Em NAT/ponte a VM vê sempre uma placa ethernet virtual; só com passthrough vê o dispositivo real.

---

## 2. Erro de permissões ao instalar

**Pergunta:**
> dá me rro de permissões ao instalar, não consigo fazer run as

**Resposta:** o Extension Pack precisa de administrador. Sem ele: usar NAT/ponte (não precisam do pack)
ou, para passthrough, testar o controlador USB 3.0 e, se der aviso, o **USB 1.1**. Num PC da empresa,
pedir ao suporte.

---

## 3. USB passado, mas sem `apt-get update`

**Pergunta:**
> já coloquei o usb o dispositivo, contudo neste momento a vm tem um device nat, tem um host e o usb, contudo não consigo fazer por exemplo apt-get update

**Resposta:** comandos de diagnóstico: `lsusb`, `ip -br a`, `ip route`, `ping -c3 8.8.8.8`, `ping -c3 google.com`.

---

## 4. Diagnóstico: o DNS não resolve

**Pergunta:**
> parece que não consegue resolver o nome (saída do lsusb / ip / ping)

Resultado relevante:

| Item | Valor |
|---|---|
| Dispositivo | `1bbb:0195` Alcatel OneTouch L850V / Telekom Speedstick LTE |
| Pen | `enx2adbfddbe719` em `DOWN`, sem IP |
| NAT | `enp0s3` 10.0.2.15, `default via 10.0.2.2` |
| Host-only | `enp0s8` 192.168.93.3 |
| `ping 8.8.8.8` | OK |
| `ping google.com` | Falha |

**Resposta:** há conectividade, falha só o DNS. No Ubuntu o DNS é do `systemd-resolved`
(`resolvectl dns enp0s3 8.8.8.8 1.1.1.1`). Alternativa no anfitrião:
`VBoxManage modifyvm "VM" --natdnshostresolver1 on`. Opção de usar a pen LTE em vez do NAT.

---

## 5. `resolvectl` não resolveu

**Pergunta:**
> penso que não deu ... apt-get update: 0% [Connecting to archive.ubuntu.com]

**Resposta:** a rede do trabalho bloqueia DNS externo (e possivelmente obriga a proxy). Testes com
`dig @8.8.8.8` / `dig @10.0.2.3` / `curl -I`. Solução mais simples: sair pela pen LTE, que não passa
pela rede da empresa.

---

## 6. Pen ativa mas sem IP

**Pergunta:**
> ip a (enx2adbfddbe719 UP, LOWER_UP, sem IPv4)

**Resposta:** no Ubuntu recente não há `dhclient`; o IP é pedido pelo netplan. Criado
`/etc/netplan/60-lte.yaml` com DHCP e DNS. Se não houver IP: verificar dados/PIN na página da pen
(`http://192.168.1.1`) e `networkctl status`.

---

## 7. Desligar o NAT por segurança

**Pergunta:**
> se calhar por segurança posso desligar a interface de nat, para não ligar a minha rede

**Resposta:** sim; assim o tráfego sai só pela pen. Desligar no VirtualBox (Definições → Rede → Placa 1)
ou `VBoxManage modifyvm "VM" --nic1 none`. A host-only só liga ao Windows (manter se usar SSH).

---

## 8. Desligar apenas no Ubuntu

**Pergunta:**
> posso neste caso desligar apenas no ubuntu

**Resposta:** sim, com `ip link set enp0s3 down`. É mais rápido mas menos seguro: quem tiver root na VM
volta a ligar. Desligar no VirtualBox é a garantia total.

---

## 9. Não permanente (em casa usa o NAT)

**Pergunta:**
> não quero permanente porque em casa posso usar o nat de casa

**Resposta:** comandos temporários e atalhos `rede-trabalho` / `rede-casa`. Ao reiniciar a VM o NAT volta.

---

## 10. Ligar também a pen no modo trabalho

**Pergunta:**
> e vai também ligar o do dispositivo no trabalho, em casa nunca vai estar ligado usb

**Resposta:** versão final:

- `60-lte.yaml` com `match: name: "enx*"` (o MAC `2a:db:...` parece aleatório, o nome pode mudar) e
  `optional: true` (o arranque não espera pela pen em casa).
- `rede-trabalho` desliga o NAT e liga/reconfigura todas as `enx*`; `rede-casa` liga o NAT.
- Filtro USB no VirtualBox para a pen passar sozinha para a VM.

Ficheiros finais: [`60-lte.yaml`](60-lte.yaml), [`rede.sh`](rede.sh), [`README.md`](README.md).

---

## 11. Guardar no repositório

**Pergunta:**
> Quero colocar isto no repositório Claude/linux/geral/ onde vamos colocar o readme.md e tudo o que foi feito para permitir ter internet na vm através do equipamento usb. Podemos também gravar a conversa e a pasta será usb Wi-Fi

**Resposta:** criada a pasta `linux/geral/usb-wifi` com o README, os ficheiros de configuração e esta conversa.
