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
- `serve` runs the read-only list page on loopback only (`--port` to change it).

First `uv sync` downloads the `hatchling` build backend from PyPI (one-time, free);
after that everything is local.

### Test it

```powershell
uv run python -m unittest discover -s tests -v
```

### Layout

- `src/proposal_creator/db.py` — schema, queries, seed data
- `src/proposal_creator/web.py` — loopback-only list page (stdlib `http.server`)
- `src/proposal_creator/__main__.py` — `seed` / `serve` CLI
- `tests/` — stdlib `unittest` suite (DB + page rendering + handler)
- `data/` — company profile; the SQLite file lives here too (gitignored)
