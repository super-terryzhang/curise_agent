"""Run as python -m scripts.prune_security_events [--apply]; never prints event contents."""

import argparse

from sqlalchemy.orm import Session

from domains.identity.audit import purge_expired, purge_expired_sessions
from infrastructure.db.session import engine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Delete events older than configured retention"
    )
    args = parser.parse_args()
    with Session(engine) as db:
        count = purge_expired(db, apply=args.apply)
        sessions = purge_expired_sessions(db, apply=args.apply)
    print(f"{'Deleted' if args.apply else 'Eligible'} security events: {count}")
    print(f"{'Deleted' if args.apply else 'Eligible'} expired sessions: {sessions}")


if __name__ == "__main__":
    main()
