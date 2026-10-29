"""CLI: `proposal-creator seed` then `proposal-creator serve`."""

from __future__ import annotations

import argparse
import sys

from .db import DEFAULT_DB_PATH, seed_examples
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
    return 2  # unreachable: argparse enforces a command


if __name__ == "__main__":
    sys.exit(main())
