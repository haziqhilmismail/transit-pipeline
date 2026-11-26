#!/usr/bin/env bash
# Run on tower. Copies the transit-server:local image (the one the tower hoarder runs)
# to the collector B bucket. The VM loads it within an hour, or at its next boot.
set -euo pipefail
: "${BUCKET:?set BUCKET to the collector B bucket name}"
gcloud=${GCLOUD:-gcloud}
image=transit-server:local

id=$(docker image inspect --format '{{.Id}}' "$image")
if "$gcloud" storage cat "gs://$BUCKET/ship/manifest.json" 2>/dev/null | grep -q "\"$id\""; then
  echo "already shipped $id"
  exit 0
fi
# The image goes first: a manifest never names an image that is not in the bucket yet.
docker save "$image" | gzip | "$gcloud" storage cp - "gs://$BUCKET/ship/transit-server.tar.gz"
printf '{"image_id": "%s", "date": "%s"}\n' "$id" "$(date -u +%FT%TZ)" |
  "$gcloud" storage cp - "gs://$BUCKET/ship/manifest.json"
echo "shipped $id"
