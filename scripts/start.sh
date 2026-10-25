#!/data/data/com.termux/files/usr/bin/sh
# Copie para ~/.termux/boot/start.sh (requer o app Termux:Boot).
# Roda a cada boot: mantém a CPU acordada, sobe o SSH e o Sentinela.
termux-wake-lock
sshd
tmux has-session -t sentinela 2>/dev/null || tmux new -d -s sentinela "python ~/sentinela.py"
