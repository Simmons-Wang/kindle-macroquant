#!/bin/sh
# Name: MacroQuant - Start (battery mode)
# Author: MacroQuant
# DontUseFBInk
# Hides the Kindle UI and keeps the dashboard on screen; deep-sleeps between updates.
# To exit: hold the power button 20-40 s until the Kindle reboots.
[ -f /mnt/us/macroquant/pid ] && kill "$(cat /mnt/us/macroquant/pid)" 2>/dev/null
if command -v setsid >/dev/null 2>&1; then
  setsid sh /mnt/us/macroquant/mq.sh loop </dev/null >/dev/null 2>&1 &
else
  nohup sh /mnt/us/macroquant/mq.sh loop </dev/null >/dev/null 2>&1 &
fi
exit 0
