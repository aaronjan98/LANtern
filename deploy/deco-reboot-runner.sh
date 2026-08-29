#!/usr/bin/env bash
# deco-reboot-runner.sh -- host side of LANtern's idle-gated Deco reboot.
#
# A systemd timer runs this every ~10 min. It asks the brain (the
# lantern-deco-reboot container) whether a reboot request is 'ready' (i.e.
# approved AND the activity oracle says WiFi is idle). If one is, it records an
# external-reachability check, runs the power-cycle actuator, records the check
# again, and reports the result back to the brain. The container can't do this
# itself -- the Kasa plug credentials + venv live here on the host.
#
# Set DRY_RUN=1 (see the .service file) to exercise the whole path WITHOUT
# actually power-cycling the AP -- use this to validate the first deploy.
set -uo pipefail

BRAIN_CTR="${BRAIN_CTR:-lantern-deco-reboot-1}"
ACTUATOR="${ACTUATOR:-/home/aj/deco-reboot/deco-reboot-soft.sh}"
TUNNEL_URL="${TUNNEL_URL:-https://immich.aaronjanovitch.com}"  # external path proof
DRY_RUN="${DRY_RUN:-0}"
LOG="${LOG:-/home/aj/deco-reboot/deco-reboot-runner.log}"

log()   { echo "$(date '+%F %T') $*" | tee -a "$LOG" >&2; }
brain() { docker exec "$BRAIN_CTR" python3 -m lantern.deco_reboot "$@"; }
# Any HTTP response (even a 302 to the login page) proves the reverse proxy +
# tunnel served across the reboot; "unreachable" means curl got nothing.
tunnel_check() {
  curl -sS -o /dev/null -w '%{http_code} in %{time_total}s' --max-time 8 \
    "$TUNNEL_URL" 2>/dev/null || echo "unreachable"
}

# Serialize runs: a power-cycle takes ~2 min, the timer must never overlap it.
exec 9>/tmp/deco-reboot-runner.lock
flock -n 9 || { log "another runner holds the lock; skipping"; exit 0; }

CLAIM="$(brain --claim-ready 2>/dev/null)" \
  || { log "cannot reach brain container $BRAIN_CTR; skipping"; exit 0; }
[ -z "$CLAIM" ] && exit 0   # nothing ready -- the normal case, stay quiet

ID="$(printf '%s' "$CLAIM" | sed -n 's/.*"id":[[:space:]]*\([0-9]*\).*/\1/p')"
[ -z "$ID" ] && { log "claimed a job but could not parse id from: $CLAIM"; exit 1; }
log "claimed reboot request #$ID: $CLAIM"

BEFORE="$(tunnel_check)"; log "external check BEFORE: $BEFORE ($TUNNEL_URL)"

if [ "$DRY_RUN" = 1 ]; then
  log "DRY_RUN=1 -- skipping the actual power-cycle"
  RESULT=success; DETAIL="dry-run (actuator not invoked)"
else
  if OUT="$("$ACTUATOR" 2>&1)"; then RESULT=success; else RESULT=fail; fi
  DETAIL="$(printf '%s' "$OUT" | tail -n 3 | tr '\n' ' ')"
fi

AFTER="$(tunnel_check)"; log "external check AFTER:  $AFTER"
log "request #$ID actuator result=$RESULT ($DETAIL)"

brain --complete "$ID" --result "$RESULT" \
  --tunnel-before "$BEFORE" --tunnel-after "$AFTER" --detail "$DETAIL" \
  >/dev/null 2>&1 || log "WARN: could not report completion for #$ID to the brain"
