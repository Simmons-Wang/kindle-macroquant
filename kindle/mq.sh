#!/bin/sh
# MacroQuant Kindle client. Installed at /mnt/us/macroquant/mq.sh
#   mq.sh once  -> fetch + draw, keep the normal Kindle UI (test)
#   mq.sh loop  -> battery mode: stop UI + background services, fetch/draw, Wi-Fi off,
#                  RTC deep sleep until next publish (plain sleep while charging)
# Exit loop mode: hold the power button ~20-40 s to reboot.
#
# v2 (2026-11-14): stop the full service set used by proven dashboards (x, otaupd, phd, tmd, todo, mcsd),
# not just lab126_gui -- otherwise Xorg/pillow respawn and fight the display; run from a /tmp copy in its own
# session (survives /mnt/us being exported over USB); never force suspend while charging/USB is connected;
# low-battery guard; diagnostics in sys.log.
# v2.1: flag early (non-RTC) wakes, log both RTC alarms, fsync logs, copy persistent firmware logs to prevboot/.

BASE="https://raw.githubusercontent.com/Simmons-Wang/kindle-macroquant/main/out"
DIR=/mnt/us/macroquant
RUN=/tmp/mq_run.sh
CACHE=/tmp/mq_dash.png
LOG=$DIR/log.txt
SYSLOG=$DIR/sys.log
MODE=${1:-loop}

FBINK=/mnt/us/libkh/bin/fbink
[ -x "$FBINK" ] || FBINK=/mnt/us/koreader/fbink
[ -x "$FBINK" ] || FBINK=fbink
# keep a RAM copy of fbink so drawing still works if /mnt/us is unmounted
if [ "$MODE" = loop ] && [ -x "$FBINK" ] && [ "$FBINK" != fbink ]; then
  cp "$FBINK" /tmp/mq_fbink 2>/dev/null && chmod +x /tmp/mq_fbink && FBINK=/tmp/mq_fbink
fi

# Loop mode always runs from a RAM copy, so the script survives /mnt/us going away (USB drive mode).
if [ "$MODE" = loop ] && [ "$0" != "$RUN" ]; then
  cp "$0" "$RUN" && chmod +x "$RUN" && exec sh "$RUN" loop
fi

us_ok() { [ -d "$DIR" ] && touch "$DIR/.rw" 2>/dev/null; }
log() { us_ok && echo "$(date -u '+%Y-%m-%d %H:%M:%S')Z $*" >> "$LOG" && sync; }
rotate() {
  for f in "$LOG" "$SYSLOG"; do
    if [ -f "$f" ] && [ "$(wc -c < "$f")" -gt 200000 ]; then tail -n 600 "$f" > "$f.1" && mv "$f.1" "$f"; fi
  done
}
kv() { grep "^$1=" "$2" 2>/dev/null | head -n1 | cut -d= -f2; }

battery() {
  b=$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null)
  [ -n "$b" ] || b=$(gasgauge-info -c 2>/dev/null | tr -dc '0-9')
  echo "${b:-0}"
}
charging() {  # 1 if on USB/charger power
  c=$(lipc-get-prop com.lab126.powerd isCharging 2>/dev/null)
  [ "$c" = 1 ] && { echo 1; return; }
  for f in /sys/class/power_supply/*/online; do
    [ -f "$f" ] && [ "$(cat "$f" 2>/dev/null)" = 1 ] && { echo 1; return; }
  done
  echo 0
}

sysdiag() {  # sysdiag TAG  -> one compact block in sys.log
  us_ok || return
  {
    echo "== $(date -u '+%Y-%m-%d %H:%M:%S')Z $1 | up $(cut -d' ' -f1 /proc/uptime)s | bat $(battery)% chg $(charging)" \
         "| pwr $(lipc-get-prop com.lab126.powerd state 2>/dev/null)"
    grep -iE 'reboot|reason|watchdog|panic|oops|respawn|crash|oom|killed|segfault|powerd.*(suspend|wakeup)' \
      /var/log/messages 2>/dev/null | tail -n "${2:-12}"
    for r in /sys/class/rtc/rtc0 /sys/class/rtc/rtc1; do
      [ -r "$r/wakealarm" ] && echo "   $(basename $r) alarm=$(cat $r/wakealarm 2>/dev/null) now=$(cat $r/since_epoch 2>/dev/null)"
    done
  } >> "$SYSLOG" 2>/dev/null
  sync
}

prevboot() {  # copy whatever persistent logs the firmware keeps, to see why the last reboot happened
  us_ok || return
  mkdir -p "$DIR/prevboot"
  for d in /var/local/log /var/local/upstart /var/log; do
    [ -d "$d" ] && ls -la "$d" > "$DIR/prevboot/ls_$(echo $d | tr / _).txt" 2>/dev/null
  done
  f=$(ls -t /var/local/log/messages* /var/local/log/*.log 2>/dev/null | head -n1)
  [ -n "$f" ] && { case "$f" in *.gz) zcat "$f" ;; *) cat "$f" ;; esac; } 2>/dev/null | tail -n 400 > "$DIR/prevboot/last_persistent.txt"
  tail -n 400 /var/log/messages > "$DIR/prevboot/messages_since_boot.txt" 2>/dev/null
  sync
}

ldn_hhmm() {
  off=$(kv LDN_OFFSET_S "$DIR/meta.txt"); off=${off:-0}
  t=$(( $(date +%s) + off ))
  date -u -d "@$t" '+%H:%M' 2>/dev/null || date -u '+%H:%MZ'
}
status_line() {  # bottom-right overlay inside the reserved 14px footer
  msg="BAT $(battery)% | $(ldn_hhmm) $1"
  $FBINK -q -S 1 -x -$(( ${#msg} + 1 )) -y -1 "$msg" >/dev/null 2>&1
}

wifi_up() {
  lipc-set-prop com.lab126.cmd wirelessEnable 1 2>/dev/null
  i=0
  while [ $i -lt 45 ]; do
    [ "$(lipc-get-prop com.lab126.wifid cmState 2>/dev/null)" = "CONNECTED" ] && return 0
    sleep 2; i=$((i + 1))
  done
  return 1
}
wifi_down() { [ "$MODE" = loop ] && lipc-set-prop com.lab126.cmd wirelessEnable 0 2>/dev/null; }

get() {  # get URL FILE ; falls back to -k only if the CA bundle rejects the cert
  curl -fsSL --max-time 90 -o "$2" "$1"; rc=$?
  if [ $rc -eq 60 ] || [ $rc -eq 77 ]; then
    log "TLS verify failed (rc=$rc), retrying without verification (public image only)"
    curl -fsSLk --max-time 90 -o "$2" "$1"; rc=$?
  fi
  return $rc
}

fetch() {
  ts=$(date +%s)
  get "$BASE/meta.txt?t=$ts" /tmp/mq_meta.new || return 1
  get "$BASE/dashboard.png?t=$ts" /tmp/mq_dash.new || return 1
  [ -s /tmp/mq_dash.new ] || return 1
  return 0
}

draw_full() {
  [ -s "$CACHE" ] || { [ -s "$DIR/dash.png" ] && cp "$DIR/dash.png" "$CACHE"; }
  [ -s "$CACHE" ] && $FBINK -q -c -f -g file="$CACHE" >/dev/null 2>&1
}

pick_rtc() {
  for r in /sys/class/rtc/rtc1 /sys/class/rtc/rtc0; do
    [ -w "$r/wakealarm" ] && { echo "$r"; return; }
  done
}

deep_sleep() {  # deep_sleep SECONDS
  secs=$1
  [ "$secs" -lt 300 ] && secs=300
  [ "$secs" -gt 43200 ] && secs=43200
  wifi_down
  if [ "$(charging)" = 1 ]; then          # suspending on USB power is refused or unstable: just wait
    log "sleep ${secs}s (charging: plain sleep)"
    sysdiag "pre-sleep(chg)" 4
    sleep "$secs"
    return
  fi
  log "sleep ${secs}s"
  sysdiag "pre-suspend" 6
  rtc=$(pick_rtc)
  if [ -n "$rtc" ]; then
    { echo 0 > "$rtc/wakealarm"; echo "+$secs" > "$rtc/wakealarm"; } 2>/dev/null
    t0=$(date +%s); sync
    echo mem > /sys/power/state 2>>/tmp/mq_err
    el=$(( $(date +%s) - t0 ))
    if [ $el -lt 60 ]; then
      log "suspend refused (elapsed ${el}s: $(tail -n1 /tmp/mq_err 2>/dev/null)) - plain sleep"
      sleep $(( secs - el ))
    else
      if [ $el -lt $(( secs - 120 )) ]; then
        log "EARLY wake after ${el}s of ${secs}s (not our alarm)"
        sysdiag "early-wake" 15
      else
        log "woke after ${el}s"
        sysdiag "resumed" 6
      fi
      sync
    fi
  else
    log "no writable RTC wakealarm - plain sleep"
    sleep "$secs"
  fi
}

stop_ui() {
  lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null
  stop lab126_gui >/dev/null 2>&1 || stop framework >/dev/null 2>&1
  sleep 2
  # same set as kindle-clock / kindle-voyage-weather; do NOT stop powerd, cmd or wifid (Wi-Fi/battery need them)
  for j in x otaupd phd tmd todo mcsd webreader; do stop "$j" >/dev/null 2>&1; done
  { echo powersave > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor; } 2>/dev/null
  sleep 2
}

once() {  # returns: 0 new image drawn, 1 same image, 2 fetch failed
  if ! wifi_up; then log "wifi not connected"; return 2; fi
  if ! fetch; then log "fetch failed"; return 2; fi
  gen=$(kv GENERATED_EPOCH /tmp/mq_meta.new)
  last=$(cat /tmp/mq_last_gen 2>/dev/null || cat "$DIR/last_gen" 2>/dev/null)
  cp /tmp/mq_meta.new /tmp/mq_meta.txt
  us_ok && cp /tmp/mq_meta.new "$DIR/meta.txt"
  if [ -n "$gen" ] && [ "$gen" != "$last" ]; then
    mv /tmp/mq_dash.new "$CACHE"
    echo "$gen" > /tmp/mq_last_gen
    us_ok && { cp "$CACHE" "$DIR/dash.png"; echo "$gen" > "$DIR/last_gen"; }
    draw_full
    log "new image gen=$gen bat=$(battery)%"
    return 0
  fi
  rm -f /tmp/mq_dash.new
  [ "$MODE" = once ] && draw_full
  log "no change (gen=$gen) bat=$(battery)%"
  return 1
}

# ---------------------------------------------------------------- test mode
if [ "$MODE" = once ]; then
  mkdir -p "$DIR"
  rm -f "$DIR/last_gen" /tmp/mq_last_gen
  once; rc=$?
  if [ $rc -eq 2 ]; then echo "MacroQuant: fetch failed - see $LOG"; exit 1; fi
  status_line "test"
  sleep 60
  exit 0
fi

# ---------------------------------------------------------------- battery loop
mkdir -p "$DIR"
echo $$ > "$DIR/pid"
rotate
log "loop start (v2) up=$(cut -d' ' -f1 /proc/uptime)s bat=$(battery)% chg=$(charging)"
sysdiag "boot-log" 25          # whatever the last boot left in /var/log/messages
prevboot
stop_ui
draw_full

retries=0
while true; do
  rotate
  b=$(battery)
  if [ "$(charging)" = 0 ] && [ "$b" -gt 0 ] && [ "$b" -lt 8 ]; then
    log "battery ${b}% - pausing updates"
    status_line "LOW BATTERY - CHARGE ME"
    deep_sleep 21600
    continue
  fi
  once; rc=$?
  now=$(date +%s)
  nxt=$(kv NEXT_FETCH_EPOCH /tmp/mq_meta.txt); nxt=${nxt:-0}
  case $rc in
    0) retries=0; status_line "new" ;;
    1) status_line "ok" ;;
    2) status_line "OFFLINE" ;;
  esac
  if [ $rc -eq 0 ] && [ "$nxt" -gt $((now + 300)) ]; then
    wait=$(( nxt - now ))
  elif [ $retries -lt 3 ] && { [ $rc -eq 2 ] || [ "$nxt" -le $((now + 300)) ]; }; then
    retries=$((retries + 1)); wait=1200          # publish late / offline: retry in 20 min
  elif [ "$nxt" -gt $((now + 300)) ]; then
    retries=0; wait=$(( nxt - now ))
  else
    retries=0; wait=14400                       # give up for 4 h
  fi
  deep_sleep "$wait"
done
