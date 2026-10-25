#!/data/data/com.termux/files/usr/bin/sh
# Registra o estado da bateria a cada 10 s em ~/batlog.txt.
# Uso: tmux new -d -s batlog ~/batlog.sh
B=/sys/class/power_supply/battery
while true; do
  echo "$(date '+%d/%m %H:%M:%S') $(cat $B/capacity)% $(cat $B/batt_inbat_voltage)mV $(cat $B/status) online=$(cat $B/online) temp=$(cat $B/temp) store=$(cat $B/store_mode)" >> ~/batlog.txt
  sleep 10
done
