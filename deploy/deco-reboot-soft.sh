#!/usr/bin/env bash
# deco-reboot-soft.sh -- reboot the main Deco (Michelle's Room, master, .216)
# through its own encrypted local API. No plug, no power-cut: the unit reboots
# itself and comes back autonomously, so there's no bootstrapping problem. We
# confirm recovery with a wired ping (sweetpea is wired, so it stays up).
#
# Drop-in replacement for the plug-based deco-reboot.sh: same log, same exit
# convention (0 = success), so the runner calls it unchanged.
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$DIR/venv/bin/python3"
SCRIPT="$DIR/deco_soft_reboot.py"
DECO="${DECO_IP:-10.0.50.216}"
MAC="${DECO_MAC:-58-04-4F-35-05-54}"   # master, Michelle's Room
MAX_WAIT="${MAX_WAIT:-300}"
LOG="$DIR/deco-reboot.log"

log() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }
up()  { ping -c1 -W2 "$DECO" >/dev/null 2>&1; }

log "=== deco SOFT-reboot start (master $MAC / $DECO) ==="

if ! OUT="$("$PY" "$SCRIPT" --reboot "$MAC" 2>&1)"; then
  log "ERROR: soft-reboot command failed: $(printf '%s' "$OUT" | tail -1)"
  exit 1
fi
log "reboot command accepted: $(printf '%s' "$OUT" | tail -1)"

# The unit should drop off the wire, then recover. Confirm both.
log "watching $DECO drop then recover (up to ${MAX_WAIT}s)..."
start=$(date +%s); went_down=0; back=0
while [ $(( $(date +%s) - start )) -lt "$MAX_WAIT" ]; do
  if up; then
    [ "$went_down" = 1 ] && { back=1; break; }
  else
    went_down=1
  fi
  sleep 3
done

secs=$(( $(date +%s) - start ))
if [ "$back" = 1 ]; then
  log "Deco $DECO went down and recovered after ${secs}s -- soft reboot confirmed. Done."
elif [ "$went_down" = 0 ]; then
  # Never observed a drop within the window. Either it rebooted faster than our
  # 3s sampling, or the command didn't take. Treat 'reachable now' as success.
  if up; then
    log "NOTE: $DECO never seen down (fast reboot or slow to drop) but is reachable now -- treating as success."
  else
    log "CRITICAL: $DECO unreachable and no drop observed -- reboot likely did not take."
    exit 2
  fi
else
  log "CRITICAL: $DECO dropped but did NOT recover within ${MAX_WAIT}s -- CHECK THE UNIT."
  exit 2
fi
