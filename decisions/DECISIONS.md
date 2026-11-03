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
## 013 — Evaluations model: checklist in data, quote gate in storage

Decision: Requirements live in `data/requirements_gov.json` (7 codes:
REG/CAP/EXP/CERT/FIN/PERF/COMP) and load into a `requirements` table; judgments
go in `evaluations` (one row per opportunity+requirement, upserted). The
no-quote-no-PASS rule is enforced where rows are written: PASS without a quote
is rejected, and any quote is rejected unless it appears verbatim
(whitespace-normalized) in the solicitation text. Verification works for
plain-text documents (.txt/.md) today; other formats store the quote flagged
`unverified` rather than blocking manual entry. Shown on the detail page and
via `set-eval` / `evals` / `reqs`.
Why: Data-driven checklist honors "government first, corporate later" — a
corporate JSON file swaps the whole checklist with no code change. Enforcing
the gate at the storage layer (not in the UI or a future model prompt) means
neither a human typo nor a model hallucination can bank a fake PASS when the
text is checkable.
Alternatives: Free-text requirement names per evaluation (loses the stable
checklist); exact-match quotes with no whitespace normalization (too brittle
across PDF line breaks); blocking all evals on unreadable formats (would make
the feature useless until PDF parsing lands).
Revisit when: PDF/DOCX text extraction arrives (then most quotes become
verifiable — likely via a `pypdf` dependency), or the model starts writing
evaluations through this same function.
## 014 — Model client: stdlib urllib, config defaults to Muse Spark

Decision: The OpenAI-compatible client (`model.py`) is handwritten over
`urllib` — no `openai` package, keeping the project dependency-free. Config
(`config.py`) reads `PROPOSAL_MODEL_*` from the environment with `.env` as
fallback (env wins); defaults are provider `meta`,
base URL `https://api.meta.ai/v1`, model `muse-spark-1.3`, reasoning effort
`minimal`, max_tokens 4000, temperature 0. The key has no default and is
required; it travels only in the Authorization header and never appears in
logs, errors, or `describe()` output. `reasoning_effort` is omitted from the
request when configured blank, so other providers stay compatible. A
`provider=stub` mode runs the evaluate flow offline with no key and no call.
Why: A chat-completions POST is ~30 lines with urllib, and zero dependencies
keeps installs trivial and auditable. Generous max_tokens + minimal reasoning
is what this reasoning model needs to return content at all. The stub keeps
the full flow testable and demoable for free.
Alternatives: The `openai` package (nicer errors, but a dependency for one
POST); per-provider SDKs (against decision 004 swappability).
Revisit when: A provider needs features outside plain chat completions
(tool calls, logprobs), or retries/streaming outgrow the small client.

## 015 — Evaluate: one call, prompt guards, gate downgrades

Decision: `evaluate <id>` judges the checklist in a single model call
(profile + requirements + solicitation text ? JSON verdicts) and writes every
verdict through `set_evaluation`. The system prompt states the non-negotiables
(verbatim quote for PASS, never invent) and declares the solicitation
untrusted data whose embedded instructions must be ignored. Model output the
gate refuses (bad PASS quote) is downgraded to UNKNOWN with a note naming the
failure and the UNVERIFIED proposed quote (labeled do-not-cite); unknown
requirement codes are skipped and reported; invalid verdicts coerce to UNKNOWN
with the raw value kept in the note. `--only CODE` limits a run (cheap
trials); `--dry-run` prints the prompt and writes nothing; token usage prints
after each run. Refuses to run without a readable (.txt/.md) document.
Why: One call per opportunity minimizes cost and latency. Downgrade-with-note
keeps a model mistake visible and reviewable instead of silently stored or
silently lost. The prompt-injection guard is the code-level form of the
"untrusted input" non-negotiable.
Alternatives: One call per requirement (7x cost, no benefit); discard refused
verdicts silently (hides model quality problems); let the model write verdicts
directly to the DB (bypasses the gate — never).
Revisit when: Checklists grow past one prompt window (then batch), or a second
model pass (self-review of quotes) proves worth its cost.
## 016 — PDF quote verification via pypdf (text PDFs; scans stay unverified)

Decision: `read_doc_text` extracts PDF text with pypdf (`PdfReader`, all pages
joined) so quotes verify against real solicitation PDFs through the unchanged
gate. `pypdf>=6.21.4` is a declared dependency, imported at module top so a
broken install fails fast instead of silently disabling verification. Any PDF
that yields no extractable text (scans, images, encrypted, corrupt) returns
"unreadable", and quotes against it store as unverified — never auto-rejected.
Tests build minimal valid PDFs in code (computed xref, no binary fixtures).
Why: Text PDFs are the common solicitation format, and pypdf is pure Python
with no transitive dependencies — the smallest possible addition. Treating
empty extraction as unverifiable (not as "quote absent") keeps the gate honest:
it refuses only quotes proven absent from known text.
Alternatives: OCR for scans (heavy native deps like tesseract — deferred until
a scanned solicitation actually appears); DOCX support (no demand yet; same
read_doc_text seam when needed); lazy pypdf import with silent fallback
(rejected — a missing declared dep should be loud).
Revisit when: Scanned PDFs need OCR, DOCX solicitations appear, or extraction
quality (layout/columns) causes false rejections — then consider layout-mode
extraction or per-page matching.
## 017 — fonttools evaluated for PDF extraction; not adopted

Decision: Do not keep fonttools. Extracted text from the real RFP PDF is
byte-identical (189,263 chars) with and without it, so it cannot change any
gate outcome. The earlier FIN-01/REG-01 downgrades were genuine gate behavior:
the PDF extracts with spacing artifacts ("limits o f not less than",
"registrat ion") and the model quoted the normalized wording, which is
correctly non-verbatim. On re-run, REG-01 passed by quoting a different,
cleanly extracted sentence — model variation, not an extraction change.
FIN-01 still downgrades on the same artifact.
Why: A dependency must earn its place with a measured difference; here the
measured difference is zero. The real follow-up, if these artifacts ever
block a verdict that matters, is extraction-tolerant matching (e.g. ignoring
spurious intra-word spaces) — not a font library.
Alternatives: Keep fonttools "in case" (rejected — speculative deps rot);
loosen the gate to accept near-verbatim quotes (rejected — the non-negotiable
is verbatim for a reason).
Revisit when: A PDF extracts differently with fonttools installed (re-run the
byte-compare), or pypdf makes it mandatory.
## 018 — Quote gate: normalized exact-substring matching

Decision: Verbatim testing now compares normalized forms — lowercase,
alphanumeric characters only — and requires the normalized quote to be an
exact substring of the normalized document. Case, whitespace, punctuation,
and hyphens are forgiven; words and numbers must match exactly. The stored
and displayed evidence is still the model's original quote text. This is
normalization plus exact matching, explicitly NOT fuzzy matching: no
similarity thresholds, edit distance, or token ratios anywhere.
Why: Real PDF extraction inserts stray intra-word spaces ("o f", "registrat
ion") and line-break hyphens that made true quotes fail character comparison
(opportunity 9 FIN-01). Stripping formatting fixes false refusals while the
exact-substring requirement keeps fabricated quotes and wrong numbers
failing — proven by tests both ways, including a wrong-amount quote.
Alternatives: Fuzzy/similarity matching (rejected — a threshold is a
negotiable definition of "verbatim" and would admit near-miss fabrications);
fixing spacing in extraction (fragile per-PDF heuristics; normalization at
match time is simpler and total).
Revisit when: A false accept or false refusal is ever traced to this rule —
then re-examine with the specific quote and text, not by loosening the rule
in general.
## 019 — Overall verdict roll-up (deterministic, no model call)

Decision: `rollup.compute_rollup` derives one verdict from stored data:
passed deadline (via the existing close-date check) -> CANNOT BID with the
deadline detail as blocker; else any FAIL -> CANNOT BID naming the failed
codes; else any UNKNOWN -> NEEDS INFO naming the unknown codes; else all
PASS/N/A -> CAN BID with an empty blocker. No evaluations -> write nothing.
`apply_rollup` writes Verdict/Blockers (+ bumps updated_at). It runs
automatically at the end of every evaluate run (over ALL stored evaluations,
not just a --only subset) and on demand via `rollup <id>` so manual set-eval
edits can refresh the verdict for free. N/A joins the verdict set as
satisfied; the model prompt and set-eval accept it. Verdict display needed no
changes — both pages already render the Verdict column.
Why: The overall call must be a transparent function of the checklist, never
a second model judgment that could disagree with it. The standalone command
exists because recomputing after a manual correction must not cost a model
call. N/A was required by the rule itself ("all PASS or N/A").
Alternatives: Model-written overall verdict (rejected — unverifiable second
judgment); roll up only in evaluate with no command (rejected — manual
corrections would leave stale verdicts or force paid re-runs); NULL vs empty
blocker for CAN BID (chose "" per the spec's "blocker empty").
Revisit when: The verdict needs more states (e.g. BID WITH RISK), weighted
requirements, or deadline-proximity (not just passed) as an input.

## 020 - Draft step: one call, PROMPTS.md prompts, checklist + files

Decision: `draft <id>` generates a response-package draft plus a commitments
checklist in a single model call (solicitation + company profile + stored
evaluations in; draft_markdown + commitments JSON out) and saves them as
drafts/opp-<id>-draft.md and drafts/opp-<id>-commitments.md, shown on the
opportunity detail page. docs/PROMPTS.md is now the source of truth for both
system prompts, loaded verbatim by prompts.get_prompt() (the eligibility
prompt moved there byte-identical); each prompt is the fenced block under its
## heading. The draft prompt instructs: full creative latitude on the writing
(structure, tone, wording, sections, formatting); strict on facts (only what
the profile supports, never invent); [NEEDS ME] on pricing, specific staffing,
the detailed technical approach, and anything uncovered; consistency with the
stored evaluations (FAIL/UNKNOWN - e.g. opp 9 CERT-01 - become [NEEDS ME]);
sections from THIS solicitation, not a generic template; DRAFT only, never
submits. Each checklist entry is flagged "supported by the company profile" or
"[NEEDS ME] - confirm we can deliver"; unknown statuses fail closed to
needs_me. The call reuses the model client with a larger budget
(PROPOSAL_MODEL_DRAFT_MAX_TOKENS, default 12000). Requires a readable document
and at least one stored evaluation; stub writes marked placeholders for free;
--dry-run prints the prompt and writes nothing. drafts/ is gitignored like
drop/ (only .gitkeep committed).
Why: One call bounds cost; the checklist is the over-promising check the draft
needs since its prose is free-form; prompts in one reviewed file keep the
honesty rules visible and diffable instead of buried in code; fail-closed
statuses keep a malformed model reply from ever looking supported.
Alternatives: Separate calls for draft and checklist (2x cost, and they could
disagree); a rigid draft template (rejected - the solicitation dictates the
shape); storing drafts in the DB (rejected - Markdown files are the artifact
the human edits and keeps).
Revisit when: Drafts need versioning per opportunity (then date-stamp the
filenames or add a drafts table), or one call's output outgrows the budget
(then split draft and checklist calls).

## 021 - Answer library: reusable answers with freshness flags

Decision: A new `answers` table (label UNIQUE case-insensitively, answer_text,
last_confirmed, optional valid_until, status active/retired) holds
human-written reusable answers, separate from the company profile.
`answers.py` owns the store: save_answer upserts by label (one answer per
topic; re-saving re-confirms to today and keeps existing valid-until/status),
update/retire edit by id, and ensure_schema migrates older DB files on first
touch. Freshness: expired when today is past valid-until (the day itself
still counts, mirroring the deadline rule); stale when last-confirmed is
older than PROPOSAL_ANSWER_STALE_DAYS (default 90 days); expired wins when
both apply; unknown age flags stale, never silent. The /answers page lists
every answer with add, edit in place (inputs associated to per-row forms via
form=, since a form may not wrap table cells), and retire, flagging
EXPIRED/STALE visibly; each opportunity page also saves label+text with no
model call. Drafts feed the ACTIVE library to the model with freshness
flags; the Draft prompt (docs/PROMPTS.md, still the source of truth) says
reuse a saved answer wherever it fits a required section of THIS
solicitation, mark expired/stale reuse "confirm still current", and [NEEDS
ME] where neither profile nor library covers a section. The commitments
checklist is three-way: "supported by the company profile" / "from your
saved answers" (plus "confirm still current" for library_stale) /
"[NEEDS ME] -- confirm we can deliver"; unknown statuses still fail closed
to needs_me. POST handling stays in stdlib http.server (no framework).
Why: Approved wording should compound across bids instead of being
re-typed; freshness flags keep reuse honest without silently trusting old
text. One-per-label upsert keeps the library deduplicated by construction.
Alternatives: Model-suggested answers auto-saved from drafts (rejected --
only the human's own saved text is trusted for reuse); fuzzy topic matching
at draft time (rejected -- the whole active library fits the prompt, so the
model sees all of it); Flask for the new forms (rejected -- four small POST
routes don't justify a dependency; revisit if the page grows auth or
richer interactions).
Revisit when: The library outgrows one prompt block (then retrieve per
section), answers need version history, or staleness needs per-topic
thresholds.

## 022 - Email intake core: Gmail read-only, notices, folders, documents

Decision: Notices arrive via the Gmail REST API (stdlib urllib, installed-app
OAuth, gmail.readonly scope; refresh token in gitignored
data/gmail_token.json; client id/secret in .env). `notices.py` parses the two
senders only -- MFMP single-part HTML by <b> label, OpenGov multipart (summary
lives in the HTML part only) -- shaped strictly by samples/*.eml; anything
else, and any non-new-notice subject (addendums parked per owner), is skipped
and logged. Matching: stored portal/agency number first, then the digit core
of the agency number printed in an unnumbered opportunity's documents
("DOR RFP #26/27-04", "(RFP) 26/27-04", "RFP_26-27-04" all reduce to 262704);
zero hits create a stub, one hit backfills empty fields only (never
overwrites), several hits report ambiguous once and touch nothing. Dedup is
the message id (content-hash backstop) in intake_seen. Documents live in a
documents table plus drop/opp-<id>/ folders (owner's suggestion); the legacy
document_path is adopted on first scan; a document is confirmed when the
agency digit core appears in its text. Decide/Draft/quote-gate now read the
combined text of all documents via evals.read_opp_text -- the only touch to
the done areas, behavior-identical for single-doc records (gate test proves a
quote in the second doc verifies). OpenGov tracking links resolve to the
portal URL + project id at live intake (kept as-is when unresolvable); the
view-vs-subscription link distinction is UNVERIFIED (sample links removed)
and must be confirmed against a real email. Portal document download is NOT
built yet: probes show OpenGov 403 to scripts and MFMP returning a JS shell,
while both open in the owner's browser -- endpoint discovery via the owner's
devtools comes first, manual drop stays the fallback.
Why: Read-only OAuth enforces the no-send/no-move guardrails at the
credential level; verbatim-stored numbers with normalized matching absorb the
spacing/slash/dash variants seen in the wild; folders match the owner's
mental model and keep multi-doc solicitations together.
Alternatives: Auto-forwarding to an intake mailbox (rejected -- same API setup
plus new message ids and fragile sender filtering); IMAP + app password
(rejected -- Workspace-dependent, less auditable than OAuth); fuzzy title
matching (rejected -- numbers or nothing, ambiguity goes to the human).
Revisit when: Addendums return (need real samples + the pursuing rule:
winnable stays live, fixable NEEDS INFO stays live); portal endpoints are
identified (then build autofetch with graceful fallback); SAM.gov API work
starts (separate piece, same notices/matching core).

## 023 - OpenGov fetch: saved page in, documents out

Decision: `fetch-portal <id> --from-file page.html` backfills an OpenGov
opportunity (portal id, agency financialId, title, release/due dates -- empty
fields only) and downloads every project attachment into drop/opp-<id>/ as
source='portal' documents, then runs agency-number confirmation. The page is
saved by the owner in their browser because OpenGov 403s scripts and
sometimes shows human verification; impersonating a browser past that would
breach the no-bypass rule. Both save styles work: plain saves pass through,
Chrome view-source saves (syntax-highlighted wrapper) are detected and
unwrapped line by line with entities decoded. Parsing reads the embedded
window.__data project subtree via balanced-brace extraction (the full blob is
a JS literal, not strict JSON -- it contains a function value -- but the
project record itself parses cleanly): id, financialId, title,
releaseProjectDate, proposalDeadline, attachments[] (filename, presigned S3
url, appendixId). Filenames are sanitized to basenames; existing files are
kept, never re-downloaded; downloads are atomic (.part + rename); failures
report per file with a re-save hint (signed URLs expire) and exit 1 while
keeping the backfill. The raw save (samples/opengov-page-322586.html) is
gitignored -- it holds live signed URLs and contact PII; tests run against a
scrubbed same-shape fixture (URLs/emails/phones/personal names redacted,
office/city kept) verified to parse identically.
Why: The data is public and already rendered for the owner; the only
script-hostile part is transport, so the human does the 10-second save and
the tool does everything after. Presigned S3 GETs need no session, so no
login automation is required either way.
Alternatives: Direct page fetch with browser impersonation (rejected --
bypass-adjacent and fragile against verification challenges); owner pasting
URLs by hand (rejected -- the page already lists them, parsing is exact);
Selenium/Playwright driving their browser (rejected -- heavy dependency for
a save dialog, revisit only if volume demands it).
Revisit when: OpenGov changes the embedded-data shape (parser errors name
the file -- re-map from a fresh save); MFMP findings arrive (same
fetch-portal pattern, separate parser); volume makes even the save step
worth automating (then revisit the browser-automation tradeoff openly).
