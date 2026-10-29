# Decisions

My record of why this is built the way it is — so if I switch something, or a model is
deprecated, I know why I chose it and how to revisit. Numbered; new entries appended.

How to add one (the agent and I both follow this):

> ## NNN — short title
> Decision: what was decided
> Why: the reason
> Alternatives: what else was considered
> Revisit when: what would make me change this

---

## 001 — Run fully local

Decision: Everything runs on my PC — no Google Sheets, no Google Drive.
Why: Removes cloud-auth friction, and keeps it private and fast to iterate.
Alternatives: Google Sheets/Drive (used in the other version); a hosted database.
Revisit when: I need multi-user access or to run it unattended on a server.

## 002 — SQLite for storage

Decision: A single SQLite file holds all opportunities and their status.
Why: The simplest possible local store — one file, no server.
Alternatives: Postgres; flat files.
Revisit when: I outgrow a single file or need concurrent writers.

## 003 — Local web page as the control surface

Decision: I review and act through a small web page I run locally.
Why: My own interface, no spreadsheet — visual, and mine to shape.
Alternatives: A command-line tool; a spreadsheet.
Revisit when: I want other people to use it, or to reach it remotely.

## 004 — Swappable models via config

Decision: The model is reached through an OpenAI-compatible client; provider, base URL,
key, and model name come from config.
Why: I'm not locked to one vendor; the OpenAI format is the de-facto standard, so
switching is a config change, not a code change.
Alternatives: Hardcode one provider; use an adapter library (e.g., LiteLLM) for
universal coverage.
Revisit when: I need a provider that isn't OpenAI-compatible, or want the per-provider
extras (reasoning, JSON mode, tools) unified.

## 005 — Start as an assistant

Decision: It surfaces opportunities and decisions; I choose which to pursue. Grow toward
autonomous later.
Why: Prove it works and that it can create proposals before I hand it the wheel.
Alternatives: A fully autonomous scout from day one.
Revisit when: I trust its judgment enough to let it run more on its own.

## 006 — Government first, corporate later

Decision: Government solicitations first; corporate RFPs later.
Why: That's where I have data and a clear need, and the engine generalizes.
Alternatives: Both at once.
Revisit when: The government side is solid and I want new markets.

## 007 — Intake: email + API, human drop for the rest

Decision: Watch the inbox and pull from APIs (SAM.gov) automatically; a human drop
folder for portals without an API.
Why: Automate where it's clean and legitimate; portals with logins and bot checks
aren't worth fighting.
Alternatives: Automate portal scraping; stay fully manual.
Revisit when: More sources offer APIs, or I accept the fragility of scraping.
