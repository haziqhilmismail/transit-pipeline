#!/usr/bin/env bash
# Run on tower, with a gcloud account that may write and delete in the bucket (the project
# owner, not the tower-reader key). Copies the transit-server:local image (the one the tower
# hoarder runs) to the collector B bucket. The VM loads it within an hour, or at its next boot.
set -euo pipefail
: "${BUCKET:?set BUCKET to the collector B bucket name}"
image=transit-server:local

id=$(docker image inspect --format '{{.Id}}' "$image")
obj=gs://$BUCKET/ship/transit-server-${id#sha256:}.tar.gz
if [ "$(gcloud storage cat "gs://$BUCKET/ship/manifest.json" 2>/dev/null |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["image_id"])' 2>/dev/null)" = "$id" ] &&
  gcloud storage ls "$obj" >/dev/null 2>&1; then
  echo "already shipped $id"
  exit 0
fi
# One object per image id, and the manifest last: the VM never reads a half-shipped image.
docker save "$image" | gzip | gcloud storage cp - "$obj"
printf '{"image_id": "%s", "date": "%s"}\n' "$id" "$(date -u +%FT%TZ)" |
  gcloud storage cp - "gs://$BUCKET/ship/manifest.json"
gcloud storage ls "gs://$BUCKET/ship/transit-server*.tar.gz" | grep -vxF "$obj" | xargs -r gcloud storage rm
echo "shipped $id"
