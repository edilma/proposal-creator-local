"""CLI: `proposal-creator seed` then `proposal-creator serve`."""

from __future__ import annotations

import argparse
import sys
from contextlib import closing

from .checks import run_checks
from .db import DEFAULT_DB_PATH, connect, get_opportunity, seed_examples
from .intake import DEFAULT_DROP_DIR, scan_drop
from .web import HOST, PORT, serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proposal-creator")
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"SQLite file (default: {DEFAULT_DB_PATH})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed", help="Insert the two demo opportunity rows (idempotent).")

    serve_p = sub.add_parser("serve", help="Serve the local opportunities page.")
    serve_p.add_argument("--host", default=HOST, help=f"Bind host (default: {HOST})")
    serve_p.add_argument("--port", type=int, default=PORT, help=f"Port (default: {PORT})")

    check_p = sub.add_parser("check", help="Print deterministic checks for one record.")
    check_p.add_argument("id", type=int, help="Opportunity id")

    intake_p = sub.add_parser("intake", help="Scan the drop folder for new files.")
    intake_p.add_argument(
        "--drop", default=str(DEFAULT_DROP_DIR), help="Folder to scan (default: drop)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "seed":
        added = seed_examples(args.db)
        print(f"Seeded {added} new row(s) into {args.db}.")
        return 0
    if args.command == "serve":
        serve(args.db, args.host, args.port)
        return 0
    if args.command == "check":
        with closing(connect(args.db)) as conn:
            row = get_opportunity(conn, args.id)
            results = run_checks(row) if row is not None else []
        if row is None:
            print(f"No opportunity with id {args.id}.")
            return 1
        print(f"{row['title']} (id {row['id']})")
        for result in results:
            print(f"[{result['result'].upper():4}] {result['label']}: {result['detail']}")
        return 0
    if args.command == "intake":
        added, skipped = scan_drop(args.drop, args.db)
        for stub in added:
            print(f"added: {stub['document_path']} (title: {stub['title']})")
        print(f"intake complete: added {len(added)}, skipped {len(skipped)}.")
        return 0
    return 2  # unreachable: argparse enforces a command


if __name__ == "__main__":
    sys.exit(main())
