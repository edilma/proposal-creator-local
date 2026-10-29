# proposal-creator-local

A local assistant that reads a government solicitation and tells a small business
whether it can bid — with a verbatim quote behind every answer — then drafts the
response package. It never submits; a person decides. Runs entirely on your machine.

See [AGENTS.md](AGENTS.md) for working rules, `decisions/DECISIONS.md` for why
things are built the way they are, and `decisions/proposal-system-brainstorm.md`
for the full vision.

The (fictitious, demo-only) company profile lives at `data/company_profile.md`.
It is not used yet; the Decide/Draft slices will read it.

## Slice 1: the local spine

SQLite database + a small local web page listing opportunities. No email, no AI yet.

### Run it

From this folder:

```powershell
uv sync
uv run proposal-creator seed
uv run proposal-creator serve
```

Then open http://127.0.0.1:8000/ in your browser.

- `seed` inserts two clearly-marked demo rows (idempotent — safe to re-run).
  The database file is `data/opportunities.db` (override with `PROPOSAL_DB`).
- `serve` runs the read-only pages on loopback only (`--port` to change it):
  the list at `/` (with a Checks summary column) and one detail page per
  opportunity at `/opp/<id>` showing all fields plus deterministic checks.
- `check <id>` prints the same deterministic checks in the terminal:
  deadline (passed / closing soon / days left), not-open-yet notice, and
  whether the attached document exists. No model calls, ever.
- `intake` scans the `drop/` folder and creates one stub record per new file
  (title from the filename, status `new`, re-runs add nothing). Drop
  solicitation files there for portals without an API. Dropped files stay
  local — only `drop/.gitkeep` is committed.
- `reqs` lists the requirement checklist (from `data/requirements_gov.json`).
  `set-eval <id> <code> <PASS|FAIL|UNKNOWN> --quote "..."` records one
  judgment; `evals <id>` shows the checklist. The detail page shows it too.
- The quote gate: PASS requires a quote, and any quote is refused unless it
  appears verbatim in the solicitation — when the document is readable
  plain text (`.txt`/`.md`). Other formats store the quote as *unverified*.

First `uv sync` downloads the `hatchling` build backend from PyPI (one-time, free);
after that everything is local.

### Test it

```powershell
uv run python -m unittest discover -s tests -v
```

### Layout

- `src/proposal_creator/db.py` — schema, queries, seed data
- `src/proposal_creator/checks.py` — deterministic checks (deadline, opening, document)
- `src/proposal_creator/web.py` — loopback-only list + detail pages (stdlib `http.server`)
- `src/proposal_creator/__main__.py` — `seed` / `serve` / `check` / `intake` /
  `set-eval` / `evals` / `reqs` CLI
- `src/proposal_creator/intake.py` — drop-folder scanning
- `src/proposal_creator/evals.py` — requirements, evaluations, quote gate
- `data/requirements_gov.json` — the government checklist (data, not code)
- `tests/` — stdlib `unittest` suite (DB + page rendering + handler)
- `data/` — company profile; the SQLite file lives here too (gitignored)
