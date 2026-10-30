"""CLI: `proposal-creator seed` then `proposal-creator serve`."""

from __future__ import annotations

import argparse
import sys
from contextlib import closing
from pathlib import Path

from .checks import run_checks
from .config import ConfigError, load as load_config
from .db import DEFAULT_DB_PATH, connect, get_opportunity, seed_examples
from .evals import (
    DEFAULT_REQUIREMENTS_PATH,
    list_checklist,
    list_requirements,
    load_requirements,
    read_doc_text,
    set_evaluation,
)
from .evaluate import build_messages, run_evaluate
from .intake import DEFAULT_DROP_DIR, scan_drop
from .model import ModelError
from .rollup import apply_rollup
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

    set_p = sub.add_parser("set-eval", help="Record one checklist evaluation.")
    set_p.add_argument("id", type=int, help="Opportunity id")
    set_p.add_argument("code", help="Requirement code (see: reqs)")
    set_p.add_argument("verdict", help="PASS, FAIL, UNKNOWN, or N/A")
    set_p.add_argument("--quote", default=None,
                       help="Verbatim sentence (required for PASS)")
    set_p.add_argument("--note", default=None, help="Optional note")
    set_p.add_argument("--by", default="human", help="Who evaluated (default: human)")

    evals_p = sub.add_parser("evals", help="Show the checklist for one record.")
    evals_p.add_argument("id", type=int, help="Opportunity id")

    rollup_p = sub.add_parser(
        "rollup", help="Recompute one record's overall verdict (no model call)."
    )
    rollup_p.add_argument("id", type=int, help="Opportunity id")

    sub.add_parser("reqs", help="List the requirement checklist.")

    eval_p = sub.add_parser("evaluate", help="Judge one record with the model.")
    eval_p.add_argument("id", type=int, help="Opportunity id")
    eval_p.add_argument(
        "--only", action="append", default=None,
        help="Requirement code to judge (repeatable; default: all)",
    )
    eval_p.add_argument(
        "--dry-run", action="store_true",
        help="Print the prompt without calling the model or writing anything",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "seed":
        added = seed_examples(args.db)
        try:
            with closing(connect(args.db)) as conn:
                reqs = load_requirements(conn, DEFAULT_REQUIREMENTS_PATH)
        except FileNotFoundError:
            print(f"warning: {DEFAULT_REQUIREMENTS_PATH} not found; skipped.")
            reqs = 0
        print(f"Seeded {added} new row(s) and {reqs} requirement(s) into {args.db}.")
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
    if args.command == "set-eval":
        try:
            with closing(connect(args.db)) as conn:
                row = set_evaluation(
                    conn,
                    args.id,
                    args.code,
                    args.verdict,
                    quote=args.quote,
                    note=args.note,
                    evaluated_by=args.by,
                )
        except ValueError as exc:
            print(f"error: {exc}")
            return 1
        state = "verified" if row["verified"] else "unverified"
        print(f"stored: {args.code} = {row['verdict']} ({state}).")
        return 0
    if args.command == "evals":
        with closing(connect(args.db)) as conn:
            row = get_opportunity(conn, args.id)
            checklist = list_checklist(conn, args.id) if row is not None else []
        if row is None:
            print(f"No opportunity with id {args.id}.")
            return 1
        print(f"{row['title']} (id {row['id']})")
        for item in checklist:
            verdict = item["verdict"] or "—"
            quote = f' "{item["quote"]}"' if item["quote"] else ""
            print(f"{item['code']:8} {verdict:7}{quote}")
        return 0
    if args.command == "reqs":
        with closing(connect(args.db)) as conn:
            reqs = list_requirements(conn)
        for req in reqs:
            print(f"{req['code']:8} {req['question']}")
        return 0
    if args.command == "evaluate":
        try:
            model_config = load_config()
        except ConfigError as exc:
            print(f"error: {exc}")
            return 1
        print(f"model: {model_config.describe()}")
        try:
            profile_text = Path(model_config.company_profile).read_text(
                encoding="utf-8"
            )
        except OSError as exc:
            print(f"error: cannot read company profile: {exc}.")
            return 1
        with closing(connect(args.db)) as conn:
            row = get_opportunity(conn, args.id)
            reqs = (
                [r for r in list_requirements(conn)
                 if args.only is None or r["code"] in args.only]
                if row is not None
                else []
            )
            doc_text = read_doc_text(row["document_path"]) if row else None
        if row is None:
            print(f"No opportunity with id {args.id}.")
            return 1
        if args.only is not None:
            known = {r["code"] for r in reqs}
            for code in args.only:
                if code not in known:
                    print(f"error: Unknown requirement code: {code}.")
                    return 1
        if not doc_text:
            print(
                f"error: Opportunity {args.id} has no readable document."
                " Attach a .txt/.md/.pdf solicitation before evaluating."
            )
            return 1
        if args.dry_run:
            for message in build_messages(profile_text, reqs, doc_text):
                print(f"--- {message['role']} ---")
                print(message["content"][:1500])
            return 0
        try:
            with closing(connect(args.db)) as conn:
                report = run_evaluate(
                    conn, args.id, model_config, profile_text, only=args.only
                )
        except (ValueError, ModelError) as exc:
            print(f"error: {exc}")
            return 1
        for item in report["stored"]:
            print(f"stored: {item['code']} = {item['verdict']}")
        for item in report["downgraded"]:
            print(f"downgraded: {item['code']} ({item['reason']})")
        for code in report["skipped"]:
            print(f"skipped unknown code: {code}")
        usage = report.get("usage") or {}
        if usage:
            print("tokens: " + ", ".join(f"{k}={v}" for k, v in usage.items()))
        if report.get("verdict"):
            line = f"overall: {report['verdict']}"
            if report.get("blocker"):
                line += f" — {report['blocker']}"
            print(line)
        return 0
    if args.command == "rollup":
        try:
            with closing(connect(args.db)) as conn:
                row = get_opportunity(conn, args.id)
                if row is None:
                    print(f"No opportunity with id {args.id}.")
                    return 1
                verdict, blocker = apply_rollup(conn, args.id)
        except ValueError as exc:
            print(f"error: {exc}")
            return 1
        if verdict is None:
            print(f"{row['title']} (id {row['id']}): no evaluations yet;")
            print("verdict left unset.")
            return 0
        print(f"{row['title']} (id {row['id']})")
        print(f"overall: {verdict}")
        if blocker:
            print(f"blocker: {blocker}")
        return 0
    return 2  # unreachable: argparse enforces a command


if __name__ == "__main__":
    sys.exit(main())
