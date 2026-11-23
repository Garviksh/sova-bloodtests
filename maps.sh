#!/bin/bash
# One-time: Google Maps for the booking page's pin (Maps JavaScript + Geocoding APIs, used from the browser only) and one key
# locked to this service's URLs and those two APIs. The key goes straight into Secret Manager as bt-maps-key; nothing prints it.
set -euo pipefail
P=sova-production; R=asia-south1; SA=sova-automation@$P.iam.gserviceaccount.com; SVC=sova-bloodtests; NAME="Sova blood tests booking map"
gcloud config set project $P >/dev/null 2>&1
URL=https://$SVC-$(gcloud projects describe $P --format='value(projectNumber)').$R.run.app
URL2=$(gcloud run services describe $SVC --region $R --format='value(status.url)')
gcloud services enable apikeys.googleapis.com maps-backend.googleapis.com geocoding-backend.googleapis.com
key() { gcloud services api-keys list --filter="displayName='$NAME'" --format='value(name)' | head -1; }
K=$(key)
if [ -z "$K" ]; then
  if ! gcloud services api-keys create --display-name="$NAME" --allowed-referrers="$URL/*,$URL2/*" \
       --api-target=service=maps-backend.googleapis.com --api-target=service=geocoding-backend.googleapis.com >/dev/null 2>/tmp/mapkey.err; then
    sed 's/AIza[0-9A-Za-z_-]*/[key hidden]/g' /tmp/mapkey.err; exit 1
  fi
  K=$(key); echo "key created: works only on $URL (and $URL2), only for Maps JavaScript + Geocoding"
fi
if ! gcloud secrets describe bt-maps-key >/dev/null 2>&1; then
  gcloud secrets create bt-maps-key --replication-policy=user-managed --locations=$R >/dev/null
  gcloud services api-keys get-key-string "$K" --format='value(keyString)' | tr -d '\n' | gcloud secrets versions add bt-maps-key --data-file=- >/dev/null
  echo "stored in Secret Manager as bt-maps-key"
fi
gcloud secrets add-iam-policy-binding bt-maps-key --member=serviceAccount:$SA --role=roles/secretmanager.secretAccessor >/dev/null
echo "maps ready: deploy once with  bash deploy.sh --update-secrets MAPS_KEY=bt-maps-key:latest"
