# ESOP Enterprise Engine & Testbed — project instructions

## Product purpose

A backend engine and testbed for managing, practicing, and simulating stock option/RSU grants: tax-track handling (Israel 102 capital-gains/work-income tracks, US ISO/NSO), trustee management, and employee status. Three static-HTML client portals (admin, employee, trustee — served directly by the API, no JS framework) exercise it. Do not assume a UI framework, ORM, or tax rule beyond what's already implemented — confirm against `backend/app/models.py` and `database/init_scheme.sql` before adding new domain logic.

## Stack

- Backend: FastAPI + SQLAlchemy, SQLite (`esop_database.db`, WAL mode).
- Clients: plain HTML/JS under `clients/{admin,employee,trustee}_portal/`, mounted by FastAPI at `/clients`.
- API surface: `backend/app/api/routes.py`, prefixed `/api/v1`. Swagger UI at `/docs`.
- Sample request payloads for manual/API testing live in `json_samples/`.

## Before implementation

1. Read `README.md` and skim `json_samples/*.json` for the shape of existing requests before assuming an endpoint's contract.
2. Explore the current models (`backend/app/models.py`), schema (`database/init_scheme.sql`), and routes before proposing changes — this is a financial domain with real invariants (e.g. `option_pools`' allocated+unallocated=total check constraint); don't loosen one without understanding why it's there.
3. Write an explicit goal, non-goals, acceptance criteria, and verification plan before touching code, same as any other feature work.
4. Fan out focused, read-only perspectives to the available expert subagents (`.claude/agents/`: product, architecture, design, QA) before finalizing a plan; add the security expert whenever a change touches tax calculation, money amounts, PII, or auth. Fan their input into one coherent spec — the participant approves every product decision.
5. Present the plan before editing application code.

## Engineering boundaries

- Keep the project runnable with `python -m uvicorn backend.app.main:app --reload` (see Useful commands).
- This is a tax/financial-compliance domain: never invent or guess a tax rule (Israeli 102 tracks, ISO/NSO treatment, etc.) — verify against what's already modeled or ask the participant, don't extrapolate.
- Follow the existing comment convention: Hebrew comments that explain *why* an invariant exists (see the `option_pools` check-constraint comment in `models.py`) — write new ones the same way, not what-the-code-already-shows comments.
- Per the project's own standing rule (`.cursorrules`): explain code changes in Hebrew before writing them; prefer simple, maintainable solutions over clever ones; keep code modular.
- Don't rewrite unrelated areas or touch `esop_database.db` production data casually — it currently holds real seeded/working data, not disposable fixtures.
- Never weaken or delete a test to satisfy a verification loop (once tests exist — see Definition of done).

## Definition of done

Run `python -m pytest` from the project root (plain `pytest` is not on PATH on this machine) — it redirects itself to a temporary DB (see `tests/conftest.py`; never reorder the imports there). Manual verification runs against a **sandbox**, never `esop_database.db`:

```bash
export PYTHONIOENCODING=utf-8 && export ESOP_DATABASE_URL="sqlite:///./qa_sandbox.db"
python -m backend.seed_data && python -m uvicorn backend.app.main:app --port 8001
```

A feature is complete only when:

- `pytest` passes, and the new behaviour has a test that would fail without the change.
- It solves the stated problem, checked against real request/response data (not just "the endpoint returns 200").
- Tax/financial calculations are verified by hand against the specific track's real rule, with the worked example recorded somewhere reviewable.
- The change respects existing DB constraints (nothing silently violates a check constraint or foreign key).
- **The version's QA file is updated** — `docs/qa/<version>.md` (start from `docs/qa/_TEMPLATE.md`), listing its test cases with expected results and its risk areas, and linked from the table in `QA_TESTBOOK.md`. This is the participant's testing document; leaving it stale is the one failure that compounds silently across versions. Read only the active version's file — the archived ones are for historical bug triage, not routine work.
- `tests/test_project_invariants.py` passes — repo-level invariants (VERSION not behind the QA testbook, index and files in sync, the testbook index stays split). Every bug caught once becomes a permanent check there.
- The current git diff has received an independent review (`.claude/agents/change-reviewer.md`).
- The participant can explain the decision, tradeoffs, and remaining risk.

## Context discipline

- **Start here: `HANDOFF.md`.** It carries the current version, the next step, open decisions, and open debt. Read it first in a fresh conversation instead of reconstructing state from history.
- A session does not close without updating `HANDOFF.md` — same standing rule as the version's QA file. Cost tracks conversation length, not task difficulty, so state must live in files and conversations must be allowed to end. Open a new conversation between versions.
- `QA_TESTBOOK.md` is an index only; the test cases live in `docs/qa/<version>.md`. Never read the archived version files or re-merge them into one file.
- Prefer a subagent for read-only exploration ("where is X used", "does Y exist") so its reading stays out of this conversation's context.
- Don't dump whole files or full command output into the transcript when a targeted range or a count answers the question.

## Useful commands

```bash
python -m uvicorn backend.app.main:app --reload   # dev server, http://127.0.0.1:8000, docs at /docs
pip install -r requirements.txt                    # deps are pinned
python -m pytest                                   # full suite (plain `pytest` is not on PATH here)
```

Use `/clear` between unrelated exercises, `/context` to inspect context use, `/usage` to monitor the Claude plan, and `/rewind` when an implementation direction is wrong.
