#!/bin/bash
# Redeploy the latest code (settings, keys and env stay). Extra gcloud flags pass through, e.g. --remove-env-vars X.
set -e; cd "$(dirname "$0")"
python3 test_main.py
gcloud run deploy sova-bloodtests --source . --function app --base-image python312 --region asia-south1 --quiet "$@" 2>&1 | tail -2
