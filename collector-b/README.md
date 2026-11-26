# Collector B

Collector B is a second GTFS-Realtime collector on a Google Cloud e2-micro VM. It runs the same
hoarder image as collector A on tower, from a different public address, so it has its own rate limit.
B exists to fill the gaps in A's archive when tower is off. B copies each packed day to a private
Cloud Storage bucket. Tower reads the bucket and repairs A's closed days.

## What runs where

| Part | Where | What it does |
|---|---|---|
| `ship-image.sh` | tower | Copies the `transit-server:local` image and a manifest to `gs://$BUCKET/ship/`. |
| `startup.sh` | VM, at boot and every hour | Makes swap, installs Docker, loads the image when the manifest changes, (re)starts the hoarder, writes the timers. |
| `collector-b sync` | VM, every 5 min | Uploads new packed days to `gs://$BUCKET/b/realtime/<feed>/<date>.tar.xz`. It never overwrites. |
| `collector-b status` | VM, every 15 min | Uploads the hoarder `status.json` to `gs://$BUCKET/b/status.json`. |
| `collector-b heartbeat` | VM, every hour | Uploads `gs://$BUCKET/b/heartbeat.json`: `written`, `ext_ipv6`, `image_id`. |
| `collector-b prune` | VM, daily | Deletes local packed days older than 30 days, only if they are in the bucket. |

The hoarder uses the settings of Harith's `deploy/compose.hoarder.yaml` (read-only root, no
capabilities, user 1000:1000, 256 MB), with two changes. `REALTIME_INTERVAL` is 30 s, the refresh
period of the feeds. The container uses the host network, because the Docker bridge has no IPv6.

The VM has no external IPv4 address. It reaches the internet over IPv6 only, and it reaches Cloud
Storage through Private Google Access. Every host it needs has an IPv6 address: `api.data.gov.my`,
`deb.debian.org` and `packages.cloud.google.com`.

The startup script stops if the GCE metadata server does not answer, so it never starts a second
hoarder on tower.

## Create it

Do these steps one time, from a machine with `gcloud`. Set the names first:

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

3. Make the two service accounts. The VM can create and read objects. It can replace only
   `b/status.json` and `b/heartbeat.json`, and it cannot delete other objects.

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

4. Make the tower key. Keep it outside every git repository.

   ```sh
   mkdir -p ~/.config/transit-pipeline
   (umask 077; gcloud iam service-accounts keys create ~/.config/transit-pipeline/gcs-reader.json \
     --iam-account=tower-reader@$PROJECT.iam.gserviceaccount.com)
   ```

5. On tower, ship the image:

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

7. Read the startup log. The last line is `collector-b: ready, image ...`.

   ```sh
   gcloud compute instances get-serial-port-output collector-b --zone=$ZONE | grep startup-script
   ```

## Update it

- New hoarder code: rebuild `transit-server:local` on tower, then run `ship-image.sh`. The VM loads
  the new image and restarts the hoarder within one hour.
- New `startup.sh`: run the command below. The VM applies it within one hour.

  ```sh
  gcloud compute instances add-metadata collector-b --zone=$ZONE \
    --metadata-from-file=startup-script=collector-b/startup.sh
  ```

- To apply a change now, stop and start the VM. Docker gives the hoarder 40 s to stop.

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

The design stays inside the Google Cloud free tier. The expected cost is USD 0.

| Item | Use | Free tier limit |
|---|---|---|
| e2-micro VM in us-central1, us-west1 or us-east1 | 1 VM all month | 1 VM |
| Standard persistent disk | 20 GB | 30 GB |
| External IPv4 address | none | (billed, about USD 3.65 a month) |
| External IPv6 address | 1 | no charge |
| Cloud NAT | none | (billed) |
| Bucket storage, Standard, US region | about 2 GB (90 days of packed days) and a 70 MB image | 5 GB |
| Class A operations (uploads) | about 4,000 a month | 5,000 |
| Internet egress from the VM | the HTTP requests and TCP acknowledgements of the feed polls | 1 GB a month |

The uploads are the Class A operations: `status.json` 2,880 a month, `heartbeat.json` 720, and
about 15 packed days a day (450). Do not make the status or heartbeat timers faster.
The bucket has no soft delete, so a replaced `status.json` does not stay as billed storage.
Traffic from the VM to Cloud Storage in the same region has no charge.

A budget alert on the billing account sends an email when the cost goes above the limit.
