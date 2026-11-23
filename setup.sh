#!/bin/bash
# Program blood tests: one-time setup in Cloud Shell (project sova-production). Safe to re-run. Prints no secret values.
# Reuses the SovaX adherence keys (Briza, sova.health Shopify, dashboard logins); adds bt-token and the redcliffe-api-key you created.
set -euo pipefail
P=sova-production; R=asia-south1; SA=sova-automation@$P.iam.gserviceaccount.com; SVC=sova-bloodtests; cd "$(dirname "$0")"
gcloud config set project $P >/dev/null
python3 -c "import functions_framework, google.cloud.bigquery" 2>/dev/null || pip3 install --user -q functions-framework==3.* google-cloud-bigquery==3.*
echo "== Tests"; python3 test_main.py

echo "== BigQuery tables"
bq show sova.bt_programs >/dev/null 2>&1 || bq mk --table sova.bt_programs \
program_id:STRING,order_name:STRING,source:STRING,purchased_at:TIMESTAMP,sku:STRING,product:STRING,days:INTEGER,customer_name:STRING,phone:STRING,email:STRING,address:STRING,city:STRING,state:STRING,pincode:STRING,lat:FLOAT,lng:FLOAT,added_at:TIMESTAMP
bq show sova.bt_log >/dev/null 2>&1 || bq mk --table --time_partitioning_field at sova.bt_log \
id:STRING,at:TIMESTAMP,program_id:STRING,test_no:INTEGER,kind:STRING,status:STRING,note:STRING,booking_id:STRING,collection_date:STRING,slot:STRING,by_user:STRING,detail:STRING
bq show sova.bt_skus >/dev/null 2>&1 || bq mk --table sova.bt_skus sku:STRING,name:STRING,product_type:STRING,days:INTEGER,note:STRING,at:TIMESTAMP,by_user:STRING
bq show sova.bt_config >/dev/null 2>&1 || bq mk --table sova.bt_config key:STRING,value:STRING,at:TIMESTAMP,by_user:STRING

echo "== Secrets (the adh-* keys are already readable by $SA from the SovaX adherence setup)"
URL=https://$SVC-$(gcloud projects describe $P --format='value(projectNumber)').$R.run.app  # Cloud Run's fixed URL for this service
if ! gcloud secrets describe bt-token >/dev/null 2>&1; then
T=$(openssl rand -hex 24)  # tick + booking-link signing key; made once, never rotated by re-runs (links in sent WhatsApps must keep working)
gcloud secrets create bt-token --replication-policy=user-managed --locations=$R >/dev/null
printf %s "$T" | gcloud secrets versions add bt-token --data-file=- >/dev/null
gcloud scheduler jobs delete bloodtests-tick --location $R --quiet >/dev/null 2>&1 || true
gcloud scheduler jobs create http bloodtests-tick --location $R --schedule "10 * * * *" --time-zone Asia/Kolkata --uri "$URL/tick?token=$T" \
--http-method POST --attempt-deadline 600s >/dev/null && echo "hourly job created (real WhatsApps only 10:00-19:00 IST)"
fi
for s in bt-token redcliffe-api-key; do gcloud secrets add-iam-policy-binding $s --member=serviceAccount:$SA --role=roles/secretmanager.secretAccessor >/dev/null; done
ADH=$(gcloud run services describe sova-adherence --region $R --format=json)
SHOP=$(jq -r '.spec.template.spec.containers[0].env[] | select(.name=="SHOP_HEALTH") | .value // empty' <<<"$ADH"); SHOP=${SHOP:-sova-health-6053.myshopify.com} # sova.health
TESTN=$(jq -r '.spec.template.spec.containers[0].env[] | select(.name=="TEST_NUMBERS") | .value // empty' <<<"$ADH")
echo "sova.health store: ${SHOP:-NOT SET}; test numbers: ${TESTN:-none}"
[ -f logo.webp ] || cp ~/ref/adh/logo-light.webp logo.webp 2>/dev/null || true

echo "== Google Maps key for the booking page's pin"; bash maps.sh
echo "== Deploy $SVC (test mode, Redcliffe staging)"
gcloud run deploy $SVC --source . --function app --base-image python312 --region $R --service-account $SA \
--allow-unauthenticated --max-instances 2 --timeout 600 --quiet \
--set-env-vars "^|^SEND_MODE=test|TEST_NUMBERS=$TESTN|SHOP_HEALTH=$SHOP|REDCLIFFE_HOST=https://apiqa.redcliffelabs.com" \
--set-secrets "BRIZA_API_KEY=adh-briza-api-key:latest,REDCLIFFE_KEY=redcliffe-api-key:latest,DASH_PASSWORD=adh-dash-password:latest,DASH_USERS=adh-dash-users:latest,TOKEN=bt-token:latest,MAPS_KEY=bt-maps-key:latest" 2>&1 | tail -3
bash addons.sh  # go-live secrets + the morning report job (bt-reports)
gcloud scheduler jobs run bloodtests-tick --location $R >/dev/null && echo "first hourly run started"
echo "Dashboard: $URL/ (same logins as the SovaX desk)"
