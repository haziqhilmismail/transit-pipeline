# Collector B

Collector B is a second GTFS-Realtime collector on a Google Cloud e2-micro VM. It runs the same
hoarder image as collector A on tower, but from a different public address, so it gets its own rate
limit. Its job is to fill the gaps in A's archive when tower is off: B copies each packed day to a
private Cloud Storage bucket, and tower reads the bucket to repair A's closed days.

## What runs where

| Part | Where | What it does |
|---|---|---|
| `ship-image.sh` | tower | Copies the `transit-server:local` image to `gs://$BUCKET/ship/transit-server-<id>.tar.gz`, then writes the id to `ship/image_id`. |
| `startup.sh` | VM, at boot and every hour | Writes the timers, makes swap, installs Docker, loads the image when `ship/image_id` changes, and starts or restarts the hoarder. |
| `collector-b sync` | VM, every 5 min | Uploads new packed days to `gs://$BUCKET/b/realtime/<feed>/<date>.tar.xz`. It never overwrites one. |
| `collector-b status` | VM, every 15 min | Uploads the hoarder `status.json` to `gs://$BUCKET/b/status.json`. |
| `collector-b heartbeat` | VM, every hour | Uploads `gs://$BUCKET/b/heartbeat.json`: `written`, `ext_ipv6`, `image_id`. |
| `collector-b prune` | VM, daily | Deletes local packed days older than 30 days, only if they are in the bucket. |
| `collector-b fw` | VM, at boot before Docker | Blocks the hoarder from the metadata server (see below). |

The hoarder uses the settings from Harith's `deploy/compose.hoarder.yaml` (read-only root, no
capabilities, user 1000:1000, 256 MB) with two changes. `REALTIME_INTERVAL` is 40 s rather than the
feeds' 30 s refresh period, to send less traffic (see [Cost](#cost)). And the container uses the
host network, since the default Docker bridge has no IPv6.

The VM has no external IPv4 address. It reaches the internet over IPv6 only, and Cloud Storage
through Private Google Access. That works because every host it needs has an IPv6 address:
`api.data.gov.my`, `deb.debian.org` and `packages.cloud.google.com`.

If the GCE metadata server doesn't answer, the startup script stops, so running it by mistake on
tower can't start a second hoarder there. On the VM, firewall rules block uid 1000 (the hoarder)
from every TCP port of the metadata server except DNS, so the hoarder can't get the VM's storage
token. The rules aren't saved, so `collector-b-fw.service` adds them at each boot. Docker requires
that unit, which means the hoarder never starts without the rules.

Collector B doesn't collect `jaguh-bayu`. That feed comes from `otp-prd.transitos.io`, which has
no IPv6 address. The hourly static GTFS checks also fail on B with connect timeouts, so B archives
realtime data only. That's all it needs, because A keeps the static archive.

## Create it

You only do this once, from any machine with `gcloud`. Set the names first:

```sh
PROJECT=my-transit-project   # a project with billing linked
REGION=us-central1           # us-central1, us-west1 or us-east1 (free tier)
ZONE=$REGION-a
BUCKET=$PROJECT-archive
SA=collector-b@$PROJECT.iam.gserviceaccount.com
gcloud config set project "$PROJECT"
gcloud services enable compute.googleapis.com storage.googleapis.com
```

1. Make a VPC and a dual-stack subnet with external IPv6 and Private Google Access.

   ```sh
   gcloud compute networks create transit-vpc --subnet-mode=custom
   gcloud compute networks subnets create transit-$REGION --network=transit-vpc --region=$REGION \
     --range=10.10.0.0/24 --stack-type=IPV4_IPV6 --ipv6-access-type=EXTERNAL \
     --enable-private-ip-google-access
   ```

2. Make the bucket. It is private, it has no soft delete, and it deletes `b/realtime/` objects after 90 days.

   ```sh
   gcloud storage buckets create gs://$BUCKET --location=$REGION --uniform-bucket-level-access \
     --public-access-prevention --soft-delete-duration=0
   gcloud storage buckets update gs://$BUCKET --lifecycle-file=collector-b/lifecycle.json
   ```

3. Make the two service accounts. The VM's account can create and read objects, but the only ones
   it can replace are `b/status.json` and `b/heartbeat.json`, and it can't delete anything else.

   ```sh
   gcloud iam service-accounts create collector-b
   gcloud iam service-accounts create tower-reader
   for role in roles/storage.objectCreator roles/storage.objectViewer; do
     gcloud storage buckets add-iam-policy-binding gs://$BUCKET --member=serviceAccount:$SA --role=$role
   done
   cat >/tmp/cond.yaml <<EOF
   title: overwrite-b-status
   expression: resource.name == "projects/_/buckets/$BUCKET/objects/b/status.json" || resource.name == "projects/_/buckets/$BUCKET/objects/b/heartbeat.json"
   EOF
   gcloud storage buckets add-iam-policy-binding gs://$BUCKET --member=serviceAccount:$SA \
     --role=roles/storage.objectUser --condition-from-file=/tmp/cond.yaml
   gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
     --member=serviceAccount:tower-reader@$PROJECT.iam.gserviceaccount.com --role=roles/storage.objectViewer
   ```

4. Make the key for tower, and keep it outside every git repository.

   ```sh
   mkdir -p ~/.config/transit-pipeline
   (umask 077; gcloud iam service-accounts keys create ~/.config/transit-pipeline/gcs-reader.json \
     --iam-account=tower-reader@$PROJECT.iam.gserviceaccount.com)
   ```

5. Ship the image from tower. Use your own gcloud account (the project owner) and not the
   `tower-reader` key, because the script writes and deletes objects in `ship/`.

   ```sh
   BUCKET=$BUCKET collector-b/ship-image.sh
   ```

6. Make the VM. `--no-address` means no external IPv4.

   ```sh
   gcloud compute instances create collector-b --zone=$ZONE --machine-type=e2-micro \
     --subnet=transit-$REGION --stack-type=IPV4_IPV6 --no-address --ipv6-network-tier=PREMIUM \
     --image-family=debian-12 --image-project=debian-cloud \
     --boot-disk-size=20GB --boot-disk-type=pd-standard \
     --service-account=$SA --scopes=storage-rw \
     --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
     --metadata=bucket=$BUCKET --metadata-from-file=startup-script=collector-b/startup.sh
   ```

7. Check the startup log. It should end with `collector-b: ready, image ...`.

   ```sh
   gcloud compute instances get-serial-port-output collector-b --zone=$ZONE | grep startup-script
   ```

## Update it

- For new hoarder code, rebuild `transit-server:local` on tower and run `ship-image.sh`. Within an
  hour the VM loads the new image and restarts the hoarder.
- For a new `startup.sh`, run the command below. The VM applies it within an hour.

  ```sh
  gcloud compute instances add-metadata collector-b --zone=$ZONE \
    --metadata-from-file=startup-script=collector-b/startup.sh
  ```

- To apply a change straight away, stop and start the VM. Docker gives the hoarder 40 s to shut down.

  ```sh
  gcloud compute instances stop collector-b --zone=$ZONE && gcloud compute instances start collector-b --zone=$ZONE
  ```

## Check it

```sh
gcloud storage cat gs://$BUCKET/b/heartbeat.json
gcloud storage cat gs://$BUCKET/b/status.json
gcloud storage ls -r gs://$BUCKET/b/realtime/
```

## Cost

Everything fits in the Google Cloud free tier except, possibly, internet egress, which is close to
the 1 GB limit.

| Item | Use | Free tier limit |
|---|---|---|
| e2-micro VM in us-central1, us-west1 or us-east1 | 1 VM all month | 1 VM |
| Standard persistent disk | 20 GB | 30 GB |
| External IPv4 address | none | (billed, about USD 3.65 a month) |
| External IPv6 address | 1 | no charge |
| Cloud NAT | none | (billed) |
| Bucket storage, Standard, US region | about 2 GB (90 days of packed days) and a 70 MB image | 5 GB |
| Class A operations (uploads and listings) | about 4,200 a month | 5,000 |
| Internet egress from the VM | at most about 1.2 GB a month (measured, see below) | 1 GB a month |

The uploads are the Class A operations. In a 31-day month, `status.json` makes 2,976,
`heartbeat.json` 744, and about 15 packed days a day make 465: about 4,200 in total. Each upload is
one JSON API request. Don't use `gcloud storage cp` for a new object on the VM, because it also
lists the bucket, which is a second Class A operation. Bucket listings count too, so the
`gcloud storage ls` in "Check it" and tower's repair job use the same 800 or so that are left.
Don't make the timers faster. The bucket has no soft delete, so a replaced `status.json` doesn't
stay as billed storage, and traffic from the VM to Cloud Storage in the same region is free.

Egress is harder to pin down. The Cloud Monitoring metric
`compute.googleapis.com/instance/network/sent_bytes_count` counts everything the VM sends, Google
traffic included, so it gives an upper bound on billed egress rather than the bill itself. On
2026-11-26, at night in Malaysia, it showed about 25 KB a minute with `REALTIME_INTERVAL` at 30 s.
At 40 s, from 19:04 to 19:33 UTC, the average was 26.5 KB a minute, which is 1.17 GB in a 30-day
month. Polling less often barely moved it: the polls fell by a quarter, but sent packets fell by
only about 8%. Most of what the VM sends isn't the feed polls, so a longer interval won't bring
egress under 1 GB by itself. Internet egress costs about USD 0.12 a GB above the free tier. The
billing report in the console shows the real internet egress for each day, by SKU.

A budget alert on the billing account sends an email when the cost goes above the limit.
