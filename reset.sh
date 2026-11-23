#!/bin/bash
# Fresh start for a clean test round. Prints no secret values.
#   bash reset.sh show   what exists now (revisions, rows, live staging bookings, settings)
#   bash reset.sh wipe   pause the hourly job, back up + empty bt_programs and bt_log (backups expire in 7 days),
#                        delete every Cloud Run revision except the one serving. Settings and SKU days stay.
set -uo pipefail
P=sova-production; R=asia-south1; SVC=sova-bloodtests
gcloud config set project $P >/dev/null 2>&1
Q() { bq -q query --use_legacy_sql=false --format="${2:-pretty}" "$1"; }
serving() { gcloud run services describe $SVC --region $R --format='value(status.traffic[0].revisionName)'; }
case "${1:-show}" in
show)
  echo "SERVING $(serving)"
  echo "REVISIONS $(gcloud run revisions list --service $SVC --region $R --format='value(metadata.name)' | tr '\n' ' ')"
  for t in bt_programs bt_log bt_config bt_skus; do
    echo "$t rows=$(Q "SELECT COUNT(*) FROM sova.$t" csv | tail -1) in_stream_buffer=$(bq show --format=json sova.$t | jq -c '.streamingBuffer.estimatedRows // 0')"
  done
  echo "SCHEDULER $(gcloud scheduler jobs describe bloodtests-tick --location $R --format='value(state)')"
  echo "== programs"; Q 'SELECT program_id, source, customer_name, RIGHT(phone, 4) AS phone_end FROM sova.bt_programs ORDER BY added_at'
  echo "== bookings whose last move is not a cancel"
  Q 'SELECT program_id, test_no, booking_id, x.kind, x.collection_date FROM (SELECT program_id, test_no, booking_id,
     ARRAY_AGG(STRUCT(kind, collection_date) ORDER BY `at` DESC LIMIT 1)[OFFSET(0)] x FROM sova.bt_log
     WHERE booking_id IS NOT NULL AND kind IN ("booked", "rescheduled", "cancelled") GROUP BY 1, 2, 3) WHERE x.kind != "cancelled"'
  echo "== settings"; Q 'SELECT key, IF(key = "test_numbers", "(set)", v) AS value FROM (SELECT key, ARRAY_AGG(value ORDER BY `at` DESC LIMIT 1)[OFFSET(0)] v FROM sova.bt_config GROUP BY key)'
  ;;
wipe)
  gcloud scheduler jobs pause bloodtests-tick --location $R --quiet >/dev/null && echo "hourly job paused"
  S=$(date +%Y%m%d_%H%M)
  for t in bt_programs bt_log; do
    bq -q cp -f sova.$t sova.${t}_bak_$S >/dev/null && bq -q update --expiration 604800 sova.${t}_bak_$S >/dev/null && echo "backup sova.${t}_bak_$S (auto-deletes in 7 days)"
    if Q "TRUNCATE TABLE sova.$t" none >/dev/null 2>&1; then echo "$t emptied"
    else  # rows still in the streaming buffer can't be truncated: drop and recreate with the same schema
      bq show --schema --format=json sova.$t > /tmp/$t.schema.json
      part=""; [ $t = bt_log ] && part="--time_partitioning_field at"
      bq -q rm -f -t sova.$t && bq -q mk --table $part sova.$t /tmp/$t.schema.json >/dev/null && echo "$t recreated empty (give it ~5 min before new writes)"
    fi
  done
  Q "INSERT sova.bt_config (key, value, \`at\`, by_user) VALUES ('orders_synced_in', '', CURRENT_TIMESTAMP(), 'reset'), ('orders_synced_health', '', CURRENT_TIMESTAMP(), 'reset')" none >/dev/null \
    && echo "order fetch starts over: the next run reads the last 12 months once, then only changes"
  keep=$(serving)
  for r in $(gcloud run revisions list --service $SVC --region $R --format='value(metadata.name)'); do
    [ "$r" = "$keep" ] && continue
    gcloud run revisions delete "$r" --region $R --quiet >/dev/null 2>&1 && echo "deleted $r" || echo "could not delete $r"
  done
  echo "kept $keep (serving)"; bash "$0" show 2>/dev/null | head -8
  ;;
esac
