#!/bin/bash
# Idempotent setup for the hourly bulk-image-GC Cloud Scheduler job.
#
# Prerequisites:
#   - `setup_fx_cron_scheduler.sh` has run at least once
#     (creates the service account + shared secret we reuse here).
#   - The `bulk-images/gc` endpoint is live on the backend.
#
# Creates a Cloud Scheduler job that POSTs hourly to
# /api/internal/bulk-images/gc on the cruise-v3-backend Cloud Run
# service, sweeping abandoned batches (> 24h) and stuck workers (> 15m).
#
# The GC endpoint is idempotent — safe to run more often than needed.
# We chose hourly cadence so a stuck job self-heals within ~1h,
# well before a user gets frustrated. Off-band failsafe: the GCS
# bucket has a 48h TTL on the `bulk-image-staging/` prefix.
#
# Safe to re-run — every operation is `describe || create`.

set -euo pipefail

PROJECT_ID="cruise-v3-prod"
REGION="asia-northeast1"
SERVICE_NAME="cruise-v3-backend"
SERVICE_URL="https://cruise-v3-backend-871185913501.${REGION}.run.app"
JOB_NAME="bulk-image-gc-hourly"
SECRET_NAME="v3-internal-cron-secret"
SA_NAME="cruise-v3-scheduler-invoker"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "▶ Setup: Bulk-image GC Cloud Scheduler (project=${PROJECT_ID}, region=${REGION})"

# ─── 1. Service account for Cloud Scheduler ─────────────────
# Reuses the SA created by setup_fx_cron_scheduler.sh. If that hasn't
# run yet, this describe check will fail — the fx script must go first.
if ! gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "✗ Service account ${SA_EMAIL} missing — run setup_fx_cron_scheduler.sh first"
    exit 1
fi

# ─── 2. Shared secret must already exist ─────────────────────
if ! gcloud secrets describe "${SECRET_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "✗ Secret ${SECRET_NAME} missing — run setup_fx_cron_scheduler.sh first"
    exit 1
fi

SECRET_VALUE_FOR_HEADER="$(gcloud secrets versions access latest \
    --secret="${SECRET_NAME}" --project="${PROJECT_ID}")"

# ─── 3. Cloud Scheduler job ─────────────────────────────────
echo "→ Cloud Scheduler job: ${JOB_NAME}"
if gcloud scheduler jobs describe "${JOB_NAME}" \
        --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "  exists, updating"
    gcloud scheduler jobs update http "${JOB_NAME}" \
        --location="${REGION}" --project="${PROJECT_ID}" \
        --schedule="0 * * * *" \
        --time-zone="UTC" \
        --uri="${SERVICE_URL}/api/internal/bulk-images/gc" \
        --http-method=POST \
        --oidc-service-account-email="${SA_EMAIL}" \
        --oidc-token-audience="${SERVICE_URL}" \
        --update-headers="X-Internal-Token=${SECRET_VALUE_FOR_HEADER}"
else
    gcloud scheduler jobs create http "${JOB_NAME}" \
        --location="${REGION}" --project="${PROJECT_ID}" \
        --schedule="0 * * * *" \
        --time-zone="UTC" \
        --uri="${SERVICE_URL}/api/internal/bulk-images/gc" \
        --http-method=POST \
        --oidc-service-account-email="${SA_EMAIL}" \
        --oidc-token-audience="${SERVICE_URL}" \
        --headers="X-Internal-Token=${SECRET_VALUE_FOR_HEADER}"
fi

echo ""
echo "✓ Done. To test immediately:"
echo "    gcloud scheduler jobs run ${JOB_NAME} --location=${REGION} --project=${PROJECT_ID}"
echo "  Then inspect Cloud Run logs; look for 'bulk-image cleanup' entries."
