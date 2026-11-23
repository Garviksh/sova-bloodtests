#!/bin/bash
# One-time add-ons (safe to re-run; prints no secret values):
#  1. The secrets Settings writes to: bt-redcliffe-prod-key (Redcliffe production key) and bt-shopify-health-app (sova.health's own
#     Shopify app: Client ID + secret). The service may add versions and read them, nothing else.
#  2. Cloud Run job bt-reports + its trigger bt-checks (11:00, 13:00, 17:00 IST): Redcliffe status of every scheduled / rescheduled
#     booking, new reports, the due sample-collected / report WhatsApps, and a summary row for Analytics
set -euo pipefail
P=sova-production; R=asia-south1; SA=sova-automation@$P.iam.gserviceaccount.com; SVC=sova-bloodtests
gcloud config set project $P >/dev/null 2>&1
URL=https://$SVC-$(gcloud projects describe $P --format='value(projectNumber)').$R.run.app
for s in bt-redcliffe-prod-key bt-shopify-health-app bt-shopify-in-app; do
  gcloud secrets describe $s >/dev/null 2>&1 || gcloud secrets create $s --replication-policy=user-managed --locations=$R >/dev/null
  for role in secretmanager.secretAccessor secretmanager.secretVersionAdder; do
    gcloud secrets add-iam-policy-binding $s --member=serviceAccount:$SA --role=roles/$role >/dev/null
  done
  echo "secret $s ready (Settings > Go live saves into it)"
done
gcloud run jobs deploy bt-reports --region $R --image docker.io/curlimages/curl:8.10.1 --service-account $SA \
  --set-secrets TOKEN=bt-token:latest --set-env-vars URL=$URL --command sh \
  --args=-c,'curl -fsS -X POST --max-time 840 "$URL/reports?token=$TOKEN"' --task-timeout 900 --max-retries 1 --quiet >/dev/null
gcloud run jobs add-iam-policy-binding bt-reports --region $R --member=serviceAccount:$SA --role=roles/run.invoker >/dev/null
echo "Cloud Run job bt-reports ready"
RUN="https://run.googleapis.com/v2/projects/$P/locations/$R/jobs/bt-reports:run"
gcloud scheduler jobs delete bt-reports-morning --location $R --quiet >/dev/null 2>&1 && echo "old trigger bt-reports-morning (10:00) removed"
if gcloud scheduler jobs describe bt-checks --location $R >/dev/null 2>&1; then
  gcloud scheduler jobs update http bt-checks --location $R --schedule "0 11,13,17 * * *" --time-zone Asia/Kolkata --uri "$RUN" \
    --http-method POST --oauth-service-account-email $SA >/dev/null
else
  gcloud scheduler jobs create http bt-checks --location $R --schedule "0 11,13,17 * * *" --time-zone Asia/Kolkata --uri "$RUN" \
    --http-method POST --oauth-service-account-email $SA >/dev/null
fi
echo "trigger bt-checks: every day 11:00, 13:00 and 17:00 IST"
gcloud run jobs execute bt-reports --region $R --wait >/dev/null 2>&1 && echo "test run of bt-reports: OK" || echo "test run of bt-reports FAILED: see Cloud Run > Jobs > bt-reports > logs"
bq show sova.bt_results >/dev/null 2>&1 || bq mk --table --time_partitioning_field at sova.bt_results \
id:STRING,at:TIMESTAMP,program_id:STRING,test_no:INTEGER,booking_id:STRING,report_url:STRING,status:STRING,source:STRING,by_user:STRING,customer_name:STRING,phone:STRING,biomarkers:STRING,report_text:STRING,report_pdf:BYTES
