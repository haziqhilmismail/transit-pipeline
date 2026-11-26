#!/bin/bash
# Collector B: the GCE startup script (instance metadata `startup-script`, bucket name in
# metadata `bucket`). It runs at every boot and again every hour, so each step checks
# the current state first and changes only what differs.
set -euo pipefail
exec 9>/run/collector-b.lock
flock 9

md() { curl -fsS -H 'Metadata-Flavor: Google' "http://metadata.google.internal/computeMetadata/v1/$1"; }
if ! md instance/id >/dev/null; then
  echo 'collector-b: no GCE metadata server, so this is not the collector B VM; refusing to run' >&2
  exit 1
fi
BUCKET=$(md instance/attributes/bucket)
DATA=/srv/hoard
STATE=/var/lib/collector-b
mkdir -p "$STATE/uploaded"
install -d -o 1000 -g 1000 "$DATA"
printf 'BUCKET=%s\nDATA=%s\nSTATE=%s\n' "$BUCKET" "$DATA" "$STATE" >/etc/default/collector-b

# The timers go first: if a later step fails, the hourly apply timer runs this script again.
cat >/usr/local/bin/collector-b <<'EOF'
#!/bin/bash
# collector-b sync|status|heartbeat|prune. Each upload is one JSON API request, one Class A
# operation; `gcloud storage cp` also lists the bucket for a new object, a second Class A.
# The free tier gives 5,000 a month.
set -euo pipefail
. /etc/default/collector-b
mark() { echo "$STATE/uploaded/$(basename "$(dirname "$1")")/$(basename "$1")"; }
up() { # file, object name, [precondition]. A 412 (precondition failed) is a success.
  local tok code
  tok=$(curl -fsS -H 'Metadata-Flavor: Google' \
    http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
  code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST -T "$1" -H "Authorization: Bearer $tok" \
    "https://storage.googleapis.com/upload/storage/v1/b/$BUCKET/o?uploadType=media&name=${2//\//%2F}${3:-}")
  case $code in 200 | 412) ;; *) echo "upload of $2 failed: HTTP $code" >&2; return 1 ;; esac
}
case $1 in
sync) # packed days are immutable, so a day is uploaded once and never overwritten
  for f in "$DATA"/realtime/*/*.tar.xz; do
    [ -e "$f" ] || continue
    m=$(mark "$f")
    [ -e "$m" ] && continue
    if up "$f" "b/realtime/$(basename "$(dirname "$f")")/$(basename "$f")" '&ifGenerationMatch=0'; then
      mkdir -p "${m%/*}"
      touch "$m"
    fi
  done ;;
status)
  cp "$DATA/status.json" "$STATE/status.json"
  up "$STATE/status.json" b/status.json ;;
heartbeat)
  ip6=$(ip -6 -o addr show scope global | awk '{sub(/\/.*/, "", $4); print $4; exit}')
  printf '{"written": %s, "ext_ipv6": "%s", "image_id": "%s"}\n' \
    "$(date +%s)" "$ip6" "$(cat "$STATE/image_id")" >"$STATE/heartbeat.json"
  up "$STATE/heartbeat.json" b/heartbeat.json ;;
prune) # local copies only, and only days already in the bucket
  find "$DATA/realtime" -name '*.tar.xz' -mtime +30 | while read -r f; do
    if [ -e "$(mark "$f")" ]; then rm -f "$f"; fi
  done ;;
*) echo "usage: collector-b sync|status|heartbeat|prune" >&2; exit 2 ;;
esac
EOF
chmod 755 /usr/local/bin/collector-b

timers=()
timer() { # name, OnCalendar, command
  printf '[Unit]\nDescription=collector-b %s\n[Service]\nType=oneshot\nExecStart=%s\n' "$1" "$3" \
    >"/etc/systemd/system/collector-b-$1.service"
  printf '[Timer]\nOnCalendar=%s\nPersistent=true\n[Install]\nWantedBy=timers.target\n' "$2" \
    >"/etc/systemd/system/collector-b-$1.timer"
  timers+=("collector-b-$1.timer")
}
timer sync '*:0/5' '/usr/local/bin/collector-b sync'
timer status '*:0/15' '/usr/local/bin/collector-b status'
timer heartbeat hourly '/usr/local/bin/collector-b heartbeat'
timer prune daily '/usr/local/bin/collector-b prune'
timer apply hourly '/usr/bin/google_metadata_script_runner startup'
systemctl daemon-reload
systemctl enable --now "${timers[@]}"

# The VM has no external IPv4 and no NAT: only IPv6 reaches the internet.
echo 'Acquire::ForceIPv6 "true";' >/etc/apt/apt.conf.d/99force-ipv6

if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
  rm -f /swapfile
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
fi

if ! command -v docker >/dev/null; then
  apt-get -o DPkg::Lock::Timeout=600 update -q
  DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 install -yq docker.io
fi

# The hoarder runs as uid 1000 on the host network. It must not get the VM's storage token.
for pair in "iptables 169.254.169.254" "ip6tables fd20:ce::254"; do
  read -r cmd addr <<<"$pair"
  rule=(OUTPUT -m owner --uid-owner 1000 -d "$addr" -p tcp --dport 80 -j REJECT)
  "$cmd" -C "${rule[@]}" 2>/dev/null || "$cmd" -I "${rule[@]}"
done

load=no
want=$(gcloud storage cat "gs://$BUCKET/ship/manifest.json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["image_id"])')
if [ "$want" != "$(cat "$STATE/image_id" 2>/dev/null)" ] || ! docker image inspect transit-server:local >/dev/null 2>&1; then
  gcloud storage cat "gs://$BUCKET/ship/transit-server-${want#sha256:}.tar.gz" | gunzip | docker load
  echo "$want" >"$STATE/image_id"
  load=yes
fi

# Same settings as Harith's deploy/compose.hoarder.yaml, except REALTIME_INTERVAL=30.
# Host network: the default Docker bridge has no IPv6, and IPv6 is the only way out.
run=(--name gtfs-hoarder-ts --restart unless-stopped --user 1000:1000
  --security-opt no-new-privileges:true --cap-drop ALL --read-only --tmpfs /tmp
  --memory 256m --network host --stop-timeout 40
  -e DATA_DIR=/data -e REALTIME_INTERVAL=30 -e STATIC_INTERVAL=3600 -e MIN_FREE_GB=2
  -v "$DATA:/data"
  --health-cmd 'node apps/backend/server/hoarder.ts status' --health-interval 1m --health-start-period 2m
  --log-opt max-size=10m --log-opt max-file=3
  transit-server:local node apps/backend/server/hoarder.ts)
cfg=$({ docker image inspect -f '{{.Id}}' transit-server:local; printf '%s\n' "${run[@]}"; } | sha256sum | cut -c1-16)
changed=no
if [ "$(docker inspect -f '{{index .Config.Labels "collector-b.cfg"}} {{.State.Running}}' gtfs-hoarder-ts 2>/dev/null)" != "$cfg true" ]; then
  docker stop -t 40 gtfs-hoarder-ts >/dev/null 2>&1 || true
  docker rm gtfs-hoarder-ts >/dev/null 2>&1 || true
  docker run -d --label "collector-b.cfg=$cfg" "${run[@]}"
  changed=yes
fi
if [ "$load" = yes ]; then docker image prune -f >/dev/null; fi

if [ "$changed" = yes ]; then systemctl start --no-block collector-b-heartbeat.service; fi
echo "collector-b: ready, image $want, container cfg $cfg"
