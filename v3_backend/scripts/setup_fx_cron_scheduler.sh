#!/bin/bash
# Idempotent setup for the daily FX-refresh Cloud Scheduler job.
#
# Run once after the v13 backend deploy lands. Creates:
#   1. A Cloud Scheduler job that POSTs daily at 00:00 UTC to
#      /api/internal/fx-refresh on the cruise-v3-backend Cloud Run
#      service, with an OIDC token + the X-Internal-Token shared
#      secret header.
#   2. A Secret Manager entry for the shared secret if one doesn't
#      already exist (generates a random 32-byte value).
#   3. Updates the Cloud Run service env to read the secret as
#      INTERNAL_CRON_SECRET.
#
# Safe to re-run — every operation is `describe || create`.
#
# Required perms: roles/cloudscheduler.admin, roles/secretmanager.admin,
# roles/run.developer on the cruise-v3-prod project.

set -euo pipefail

PROJECT_ID="cruise-v3-prod"
REGION="asia-northeast1"
SERVICE_NAME="cruise-v3-backend"
SERVICE_URL="https://cruise-v3-backend-871185913501.${REGION}.run.app"
JOB_NAME="fx-refresh-daily"
SECRET_NAME="v3-internal-cron-secret"
SA_NAME="cruise-v3-scheduler-invoker"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "▶ Setup: FX-refresh Cloud Scheduler (project=${PROJECT_ID}, region=${REGION})"

# ─── 1. Service account for Cloud Scheduler ─────────────────
echo "→ Service account: ${SA_EMAIL}"
if ! gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud iam service-accounts create "${SA_NAME}" \
        --display-name="Cloud Scheduler invoker for cruise-v3-backend" \
        --project="${PROJECT_ID}"
fi

# Grant the SA permission to invoke the Cloud Run service
echo "→ Granting roles/run.invoker on ${SERVICE_NAME}"
gcloud run services add-iam-policy-binding "${SERVICE_NAME}" \
    --region="${REGION}" --project="${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/run.invoker" >/dev/null 2>&1 || true

# ─── 2. Shared-secret token in Secret Manager ───────────────
echo "→ Secret: ${SECRET_NAME}"
if ! gcloud secrets describe "${SECRET_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    SECRET_VALUE="$(openssl rand -base64 32 | tr -d '\n')"
    echo -n "${SECRET_VALUE}" | gcloud secrets create "${SECRET_NAME}" \
        --replication-policy="automatic" \
        --data-file=- --project="${PROJECT_ID}"
    echo "  created with fresh 32-byte random value"
else
    echo "  already exists, skipping create"
fi

# Grant Cloud Run service account permission to read the secret
RUN_SA="$(gcloud run services describe "${SERVICE_NAME}" \
    --region="${REGION}" --project="${PROJECT_ID}" \
    --format='value(spec.template.spec.serviceAccountName)')"
if [[ -z "${RUN_SA}" ]]; then
    RUN_SA="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
    echo "  Cloud Run uses default compute SA: ${RUN_SA}"
fi
gcloud secrets add-iam-policy-binding "${SECRET_NAME}" \
    --member="serviceAccount:${RUN_SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="${PROJECT_ID}" >/dev/null 2>&1 || true

# ─── 3. Wire secret as env var on the Cloud Run service ──────
echo "→ Wiring INTERNAL_CRON_SECRET into ${SERVICE_NAME}"
gcloud run services update "${SERVICE_NAME}" \
    --region="${REGION}" --project="${PROJECT_ID}" \
    --update-secrets="INTERNAL_CRON_SECRET=${SECRET_NAME}:latest" >/dev/null

# Fetch the secret value to include in the scheduler job header
SECRET_VALUE_FOR_HEADER="$(gcloud secrets versions access latest \
    --secret="${SECRET_NAME}" --project="${PROJECT_ID}")"

# ─── 4. Cloud Scheduler job ─────────────────────────────────
echo "→ Cloud Scheduler job: ${JOB_NAME}"
if gcloud scheduler jobs describe "${JOB_NAME}" \
        --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "  exists, updating"
    gcloud scheduler jobs update http "${JOB_NAME}" \
        --location="${REGION}" --project="${PROJECT_ID}" \
        --schedule="0 0 * * *" \
        --time-zone="UTC" \
        --uri="${SERVICE_URL}/api/internal/fx-refresh" \
        --http-method=POST \
        --oidc-service-account-email="${SA_EMAIL}" \
        --oidc-token-audience="${SERVICE_URL}" \
        --update-headers="X-Internal-Token=${SECRET_VALUE_FOR_HEADER}"
else
    # `create http` uses --headers (plural, comma-separated); only the
    # `update http` subcommand has --update-headers. Different verb,
    # different flag — gcloud's CLI surface is not symmetric here.
    gcloud scheduler jobs create http "${JOB_NAME}" \
        --location="${REGION}" --project="${PROJECT_ID}" \
        --schedule="0 0 * * *" \
        --time-zone="UTC" \
        --uri="${SERVICE_URL}/api/internal/fx-refresh" \
        --http-method=POST \
        --oidc-service-account-email="${SA_EMAIL}" \
        --oidc-token-audience="${SERVICE_URL}" \
        --headers="X-Internal-Token=${SECRET_VALUE_FOR_HEADER}"
fi

echo ""
echo "✓ Done. To test immediately:"
echo "    gcloud scheduler jobs run ${JOB_NAME} --location=${REGION} --project=${PROJECT_ID}"
echo "  Then inspect the Cloud Run logs or v2_exchange_rates table for fresh rows."
