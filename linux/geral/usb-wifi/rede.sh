# rede.sh - alternar a saída para a internet da VM entre a pen LTE (trabalho) e o NAT (casa)
#
# Instalar (uma vez):
#   cp rede.sh ~/.rede.sh
#   echo '[ -f ~/.rede.sh ] && . ~/.rede.sh' >> ~/.bashrc
#   source ~/.bashrc
#
# Uso:
#   rede-trabalho   -> desliga o NAT (enp0s3) e liga a pen LTE (enx*)
#   rede-casa       -> volta a ligar o NAT
#
# Nota: ao reiniciar a VM o NAT volta a ficar ligado; no trabalho corre rede-trabalho depois do arranque.

NAT_IF="enp0s3"

rede-trabalho() {
  sudo ip link set "$NAT_IF" down
  for i in $(ls /sys/class/net | grep '^enx'); do
    sudo ip link set "$i" up && sudo networkctl reconfigure "$i"
  done
  sleep 3
  ip -br a
  ip route
}

rede-casa() {
  sudo ip link set "$NAT_IF" up && sudo networkctl reconfigure "$NAT_IF"
  sleep 3
  ip -br a
  ip route
}
