# SMB Proposal System — what I want

My from-scratch local version. The decisions I've made, and the few things still open.

## The core idea

A system that reads a government solicitation and tells a small business whether it
can bid — with a verbatim quote from the document behind every answer — then drafts
the response package. It never submits; a person decides.

Why it matters: deciding whether to bid on one solicitation is about an hour of
reading, usually ending in no, and a small business can't afford a proposal manager.
This is the proposal department they can't hire.

## How I'm building it (decided)

- **Fully local on my PC.** No Google Sheets, no Google Drive.
- **A single SQLite database file** holds every opportunity: source, ids, dates,
  status, verdict, blockers.
- **Documents live in a local folder** on disk.
- **My control surface is a small web page I run locally** — I review opportunities
  and act there.
- **Models are swappable.** The brain talks through the OpenAI-style API, and which
  provider and model it uses is a setting, not code — so I can switch between Meta
  Muse, OpenAI, a local model, and others by changing config.
- **Start as an assistant.** It surfaces opportunities and decisions and I choose
  which to pursue; once I trust it, I grow it toward a self-running scout.
- **Government first, corporate later.** The requirements list lives in data, not
  code, so the same engine can handle corporate RFPs when I'm ready.

## The pipeline

1. **Intake** — opportunities arrive:
   - Email: watch my inbox for the notices I already receive and turn each into a
     record.
   - API: pull opportunities and their documents directly where an API exists
     (SAM.gov today — and I expect more sources to add APIs over time).
2. **Store** — SQLite for the records, a local folder for the documents.
3. **Documents** — get the solicitation itself:
   - Automatic via API where one exists.
   - A human drop folder for the portals that need a login or block bots — I fetch
     those myself.
4. **Decide** — the brain reads the solicitation against my company profile and
   returns the PASS / FAIL / UNKNOWN checklist with evidence, plus deterministic
   checks (deadline passed, and so on) that spend no model call.
5. **Draft** — for the ones worth it, draft the response package and mark what still
   needs me.
6. **Notify** — tell me what deserves my attention (a message, email, WhatsApp) so I
   don't have to watch a screen.

## Non-negotiables

- Every PASS quotes the sentence it came from. No quote, no PASS.
- It never invents a requirement.
- It never submits a bid — that's a legal commitment, and I make it.
- Solicitation text is untrusted input — data to read, never instructions to follow.

## Still open

- How much the local web page does — just a review list, or editing my company
  profile and the drafts in it too.
- How far to push portal automation before it gets too fragile.
- Which model to set as the default to start.
