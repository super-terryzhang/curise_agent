# Runbook

Living document — each Phase adds its operational procedures here.

## Environments

| Name | URL | Purpose |
|---|---|---|
| local | http://localhost:8000 | Developer machine |
| staging | `cruise-backend-v3-staging.run.app` (Phase 0 to create) | Integration testing |
| production | `cruise-backend-v3.run.app` (Phase 7 to cut over) | Live traffic |

## Common operations

### Deploy to staging

> Phase 0 — add once Cloud Run service exists

```bash
gcloud run deploy cruise-backend-v3-staging \
  --source . \
  --region asia-northeast1 \
  --env-vars-file env_vars.staging.yaml
```

### Tail production logs

```bash
gcloud run logs tail --service cruise-backend-v3 --region asia-northeast1
```

### Rotate a leaked SECRET_KEY

1. Generate new key: `python -c "import secrets; print(secrets.token_hex(48))"`
2. Update Cloud Run secret: `gcloud secrets versions add cruise-v3-secret-key --data-file=-`
3. Deploy: triggers a restart, invalidates all access tokens
4. Users will need to re-login; refresh tokens in DB still valid (they're opaque, not signed)

## Phase-specific runbooks

- Phase 0: _this section grows as we go_
- Phase 1: _TBD_
- Phase 3: **Order PO-fields expansion** (below)

### Phase 3 — Order PO fields production migration

Goal: move the 7 PO-specific fields from `Document.extracted_data.metadata`
JSON into real columns on the `v2_orders` table, without downtime.

This migration is **only needed for production** — development / staging
SQLite creates the columns directly via `Base.metadata.create_all`.

**Contract with the ops team**: v3 application code is ready to read from
either location. The migration sequence is decoupled from app deploys.

#### Step 1 — Expand (schema only, zero risk)

```bash
# Inside the v3_backend venv on a machine with DB access
alembic upgrade 0002_orders_expand
```

Adds 7 nullable columns to `v2_orders`. No data is touched. v2 production
is unaffected because the legacy JSON columns still hold all values.

#### Step 2 — v2 double-write PR (separate codebase)

Cherry-pick into v2-backend:

1. Modify `v2-backend/services/documents/document_order_projection.py`:
   - Inside `create_or_update_order_from_document`, after setting
     `order.order_metadata = payload["order_metadata"]`, copy each
     metadata value into its new column if present:
     ```python
     md = payload["order_metadata"] or {}
     order.po_number = md.get("po_number")
     order.ship_name = md.get("ship_name")
     # ... etc for the 7 fields
     ```
2. Deploy v2 with this change.
3. Monitor for **3 full business days** — check that every new Order row
   has populated new columns matching its JSON. Use:
   ```sql
   SELECT COUNT(*) FROM v2_orders
   WHERE order_metadata->>'po_number' IS DISTINCT FROM po_number
     AND created_at > NOW() - INTERVAL '1 day';
   ```
   Must return 0. Any non-zero → fix and restart the clock.

#### Step 3 — Backfill historical rows

Use `scripts/backfill_order_fields.py` (written for Phase 3, shipped now).

```bash
# Dry run first, produce a sample diff
python scripts/backfill_order_fields.py --dry-run --limit 500 | tee dry-run.log

# If sample diff looks right, run for real (resumable via checkpoint file)
python scripts/backfill_order_fields.py --batch-size 500

# If it crashes, resume
python scripts/backfill_order_fields.py --resume
```

The script is idempotent — re-running after completion is a no-op.

#### Step 4 — Verify no drift

```sql
-- All rows that have a JSON po_number must also have a column po_number
SELECT COUNT(*) FROM v2_orders
WHERE order_metadata->>'po_number' IS NOT NULL
  AND po_number IS NULL;
-- Expected: 0

-- Sample diff
SELECT id, po_number, order_metadata->>'po_number' AS jsonb_po
FROM v2_orders
WHERE order_metadata->>'po_number' != po_number
LIMIT 20;
-- Expected: empty
```

#### Step 5 — (Phase 7 only) Contract migration

**Not in Phase 3 scope.** After v3 has been reading from the new columns
exclusively for at least 2 weeks in production (Phase 7), drop the
legacy JSON fields:

```python
# future migration 0xxx_orders_contract.py
op.execute(
    "UPDATE v2_orders SET extraction_data = extraction_data - 'metadata' - 'products'"
)
# Or drop specific JSON keys using PostgreSQL operators.
```

#### Rollback

- After Step 1 only: `alembic downgrade -1` — drops the 7 columns. No data lost.
- After Step 2: revert v2 PR; v3 still works because it falls back to reading
  from the JSON blob when new columns are empty.
- After Step 3: v2 still works (it reads from JSON); revert v3 to v2 traffic
  and the JSON is still the source of truth.

## Incidents

Log incidents in `docs/incidents/YYYY-MM-DD-<slug>.md`:

```markdown
# YYYY-MM-DD — <title>

- Detected: <time>
- Resolved: <time>
- Impact: <user-facing description>
- Root cause: <what broke>
- Fix: <what restored service>
- Prevention: <what we changed>
```
