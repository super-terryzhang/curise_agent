"""Cloud Run Job entry; shared by hourly Scheduler and the manual UI trigger."""

import json


def main():
    from apps.http.startup import verify_schema
    from apps.jobs.oracle_scan import execute_scan
    from infrastructure.db.engine import engine

    verify_schema(engine)
    result = execute_scan()
    print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
