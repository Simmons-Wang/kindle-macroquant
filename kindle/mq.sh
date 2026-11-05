#!/bin/sh
# MacroQuant Kindle client. Installed at /mnt/us/macroquant/mq.sh
#   mq.sh once  -> fetch + draw, keep the normal Kindle UI (test)
#   mq.sh loop  -> battery mode: stop UI, fetch/draw, Wi-Fi off, RTC deep sleep until next publish
# Exit loop mode: hold the power button ~20-40 s to reboot.

BASE="https://raw.githubusercontent.com/Simmons-Wang/kindle-macroquant/main/out"
DIR=/mnt/us/macroquant
LOG=$DIR/log.txt
FBINK=/mnt/us/libkh/bin/fbink
[ -x "$FBINK" ] || FBINK=/mnt/us/koreader/fbink
[ -x "$FBINK" ] || FBINK=fbink
MODE=${1:-loop}
mkdir -p "$DIR"

log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S')Z $*" >> "$LOG"; }
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 200000 ]; then tail -n 400 "$LOG" > "$LOG.1" && mv "$LOG.1" "$LOG"; fi

kv() { grep "^$1=" "$2" 2>/dev/null | head -n1 | cut -d= -f2; }

battery() {
  b=$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null)
  [ -n "$b" ] || b=$(gasgauge-info -c 2>/dev/null | tr -dc '0-9')
  echo "${b:-?}"
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
  get "$BASE/meta.txt?t=$ts" "$DIR/meta.new" || return 1
  get "$BASE/dashboard.png?t=$ts" "$DIR/dash.new" || return 1
  [ -s "$DIR/dash.new" ] || return 1
  return 0
}

draw_full() {
  $FBINK -q -c -f -g file="$DIR/dash.png" >/dev/null 2>&1
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
  log "sleep ${secs}s"
  wifi_down
  rtc=$(pick_rtc)
  if [ -n "$rtc" ]; then
    echo 0 > "$rtc/wakealarm" 2>/dev/null
    echo "+$secs" > "$rtc/wakealarm" 2>/dev/null
    t0=$(date +%s); sync
    echo mem > /sys/power/state 2>>"$LOG"
    el=$(( $(date +%s) - t0 ))
    if [ $el -lt 60 ]; then
      log "suspend did not happen (elapsed ${el}s) - falling back to plain sleep"
      sleep $(( secs - el ))
    else
      log "woke after ${el}s"
    fi
  else
    log "no writable RTC wakealarm - plain sleep"
    sleep "$secs"
  fi
}

once() {  # returns: 0 new image drawn, 1 same image, 2 fetch failed
  if ! wifi_up; then log "wifi not connected"; return 2; fi
  if ! fetch; then log "fetch failed"; return 2; fi
  gen=$(kv GENERATED_EPOCH "$DIR/meta.new")
  last=$(cat "$DIR/last_gen" 2>/dev/null)
  mv "$DIR/meta.new" "$DIR/meta.txt"
  if [ -n "$gen" ] && [ "$gen" != "$last" ]; then
    mv "$DIR/dash.new" "$DIR/dash.png"
    echo "$gen" > "$DIR/last_gen"
    draw_full
    log "new image gen=$gen"
    return 0
  fi
  rm -f "$DIR/dash.new"
  [ "$MODE" = once ] && draw_full
  log "no change (gen=$gen)"
  return 1
}

# ---------------------------------------------------------------- test mode
if [ "$MODE" = once ]; then
  rm -f "$DIR/last_gen"
  once; rc=$?
  if [ $rc -eq 2 ]; then echo "MacroQuant: fetch failed - see $LOG"; exit 1; fi
  status_line "test"
  sleep 60
  exit 0
fi

# ---------------------------------------------------------------- battery loop
echo $$ > "$DIR/pid"
log "loop start"
lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null
sleep 2
stop lab126_gui >/dev/null 2>&1 || stop framework >/dev/null 2>&1
sleep 3
[ -s "$DIR/dash.png" ] && draw_full

retries=0
while true; do
  once; rc=$?
  now=$(date +%s)
  nxt=$(kv NEXT_FETCH_EPOCH "$DIR/meta.txt"); nxt=${nxt:-0}
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
