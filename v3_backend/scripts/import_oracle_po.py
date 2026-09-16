"""Single PO command. Uses configured v3 DB/storage; no schema creation or scheduling.

python -m scripts.import_oracle_po --po PO162045CCI --user-id ID --cache-dir PATH --policy PATH
Oracle credentials: ORACLE_USERNAME / ORACLE_PASSWORD. Policy contains explicitly
scoped unit_approvals and template_overrides, never credentials.
"""

import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--po", required=True)
    parser.add_argument("--user-id", required=True, type=int)
    parser.add_argument("--folder-id", type=int)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--allow-model", action="store_true")
    args = parser.parse_args()
    from apps.jobs.oracle_po import import_po
    from infrastructure.config import settings
    from infrastructure.oracle.adapter import OracleClient

    policy = json.loads(args.policy.read_text()) if args.policy else {}
    client = OracleClient(
        {"username": os.getenv("ORACLE_USERNAME"), "password": os.getenv("ORACLE_PASSWORD")}
    )
    records = [r for r in client.list_orders() if r["OrderNumber"] == args.po]
    if len(records) != 1:
        raise ValueError("UNIQUE_ORACLE_PO_REQUIRED")
    result = import_po(
        records[0],
        client=client,
        user_id=args.user_id,
        folder_id=args.folder_id,
        cache_dir=args.cache_dir,
        unit_approvals=policy.get("unit_approvals", []),
        template_overrides={int(k): v for k, v in policy.get("template_overrides", {}).items()},
        allow_model=args.allow_model,
        api_key=settings.GOOGLE_API_KEY,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(
            json.dumps({"status": "failed", "code": getattr(error, "code", type(error).__name__)})
        )
        raise SystemExit(1) from None
