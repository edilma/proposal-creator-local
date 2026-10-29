# Decisions

My record of why this is built the way it is â€” so if I switch something, or a model is
deprecated, I know why I chose it and how to revisit. Numbered; new entries appended.

How to add one (the agent and I both follow this):

> ## NNN â€” short title
> Decision: what was decided
> Why: the reason
> Alternatives: what else was considered
> Revisit when: what would make me change this

---

## 001 â€” Run fully local

Decision: Everything runs on my PC â€” no Google Sheets, no Google Drive.
Why: Removes cloud-auth friction, and keeps it private and fast to iterate.
Alternatives: Google Sheets/Drive (used in the other version); a hosted database.
Revisit when: I need multi-user access or to run it unattended on a server.

## 002 â€” SQLite for storage

Decision: A single SQLite file holds all opportunities and their status.
Why: The simplest possible local store â€” one file, no server.
Alternatives: Postgres; flat files.
Revisit when: I outgrow a single file or need concurrent writers.

## 003 â€” Local web page as the control surface

Decision: I review and act through a small web page I run locally.
Why: My own interface, no spreadsheet â€” visual, and mine to shape.
Alternatives: A command-line tool; a spreadsheet.
Revisit when: I want other people to use it, or to reach it remotely.

## 004 â€” Swappable models via config

Decision: The model is reached through an OpenAI-compatible client; provider, base URL,
key, and model name come from config.
Why: I'm not locked to one vendor; the OpenAI format is the de-facto standard, so
switching is a config change, not a code change.
Alternatives: Hardcode one provider; use an adapter library (e.g., LiteLLM) for
universal coverage.
Revisit when: I need a provider that isn't OpenAI-compatible, or want the per-provider
extras (reasoning, JSON mode, tools) unified.

## 005 â€” Start as an assistant

Decision: It surfaces opportunities and decisions; I choose which to pursue. Grow toward
autonomous later.
Why: Prove it works and that it can create proposals before I hand it the wheel.
Alternatives: A fully autonomous scout from day one.
Revisit when: I trust its judgment enough to let it run more on its own.

## 006 â€” Government first, corporate later

Decision: Government solicitations first; corporate RFPs later.
Why: That's where I have data and a clear need, and the engine generalizes.
Alternatives: Both at once.
Revisit when: The government side is solid and I want new markets.

## 007 â€” Intake: email + API, human drop for the rest

Decision: Watch the inbox and pull from APIs (SAM.gov) automatically; a human drop
folder for portals without an API.
Why: Automate where it's clean and legitimate; portals with logins and bot checks
aren't worth fighting.
Alternatives: Automate portal scraping; stay fully manual.
Revisit when: More sources offer APIs, or I accept the fragility of scraping.
## 008 — Opportunities table columns

Decision: The `opportunities` table holds the requested fields (source,
external_id, title, agency, open_date, close_date, status, verdict, blockers,
document_path) plus `url` (link back to the solicitation), `notes` (free text),
and `created_at`/`updated_at` timestamps. Dates are TEXT in ISO-8601 form.
`UNIQUE(source, external_id)` keeps re-imports from duplicating rows.
Why: `url` is the cheapest way to get back to the source notice; `notes` holds
anything that doesn't deserve a column yet; timestamps make "what changed" and
debugging possible. SQLite has no date type, and ISO-8601 TEXT sorts and compares
correctly.
Alternatives: Exactly the requested columns only; INTEGER unix timestamps; a
separate documents table (overkill while it's one path per opportunity).
Revisit when: An opportunity needs multiple documents, or verdicts need their own
history/audit table.

## 009 — Stdlib-only first slice (no web framework)

Decision: Slice 1 uses only the Python standard library: `http.server` for the
list page (bound to 127.0.0.1), `sqlite3` for storage, `unittest` for tests. No
third-party dependencies at all.
Why: The slice needs nothing a framework would add, and zero dependencies means
zero install risk and nothing to audit. Every value rendered into HTML is escaped
(`html.escape`), per the untrusted-input rule.
Alternatives: Flask/FastAPI now; Jinja templates (auto-escaping is nice but adds
a dependency for one table).
Revisit when: The page needs forms, editing, or auth — then pick one framework
(Flask is the likely small choice) and record it here.

## 010 — uv project with src layout and hatchling

Decision: Python project managed by `uv`, `src/proposal_creator` layout,
`hatchling` build backend, and a `proposal-creator` console script (`seed`,
`serve`). The venv lives at `.venv/`; the DB defaults to
`data/opportunities.db` (overridable via `PROPOSAL_DB`); `*.db` is gitignored.
Why: Matches the repo's decided working method (`uv`, small local pieces);
src layout keeps tests importing the installed package, not stray files.
Alternatives: Flat layout; setuptools backend; committed demo database.
Revisit when: Packaging needs grow (extras, native deps) or the DB location
convention stops fitting.
## 011 — Deterministic checks: what they are and their boundary rules

Decision: `checks.py` holds pure, model-free checks over one opportunity record,
shown on the detail page (`/opp/<id>`), summarized in the list ("ok" or
"N failed, M warnings"), and printable via `proposal-creator check <id>`.
Levels are fail/warn/info/ok. Rules: a deadline counts as passed only when
close_date is before today (closing day itself still counts as open); 0–7 days
out is a warning; a missing or unparsable close date is a warning, never a
silent pass; a future open date is info; a missing document path or a path whose
file is absent is a warning.
Why: These answers cost no model call and are never ambiguous, so they run on
every view. Warnings (not silence) for missing data keep gaps visible instead
of looking clean.
Alternatives: Fail on missing close date (too harsh — intake records routinely
lack it at first); compute checks once at intake and store them (they go stale;
recomputing per view is cheap).
Revisit when: Checks need history ("when did this start failing") or new rule
kinds that need the document text (those belong to the Decide slice, and still
stay model-free where possible).
## 012 — Drop-folder intake: stubs by filename, matched by path

Decision: `proposal-creator intake` scans `drop/` and creates one stub record
per new file: source `drop`, title from the filename stem, status `new`, and a
note that dates need review. Re-scans match on the stored document path and add
nothing; dotfiles and subdirectories are skipped. Stored paths are relative to
the project root (portable across machines). Dropped files are gitignored —
only `drop/.gitkeep` is committed.
Why: Filename-as-title is predictable and the human renames/refines later (no
edit UI yet, so anything cleverer would be unfixable). Matching by path is the
only stable identity a dropped file has. Ignoring contents keeps binaries and
possibly distribution-limited solicitations out of the public repo.
Alternatives: Parse titles/dates out of the documents at intake (fragile, and
PDF parsing needs a new dependency — deferred); commit dropped files as
evidence (revisit if evidence retention in git is ever wanted).
Revisit when: Intake needs subfolders per opportunity, or automatic
date/agency extraction proves reliable.
