# ESOP Enterprise Engine & Testbed — project instructions

## Product purpose

A backend engine and testbed for managing, practicing, and simulating stock option/RSU grants: tax-track handling (Israel 102 capital-gains/work-income tracks, US ISO/NSO), trustee management, and employee status. Three static-HTML client portals (admin, employee, trustee — served directly by the API, no JS framework) exercise it. Do not assume a UI framework, ORM, or tax rule beyond what's already implemented — confirm against `backend/app/models.py` and `database/init_scheme.sql` before adding new domain logic.

## Stack

- Backend: FastAPI + SQLAlchemy, SQLite (`esop_database.db`, WAL mode), Alembic migrations.
- Clients: plain HTML/JS under `clients/{admin,employee,trustee}_portal/`, mounted by FastAPI at `/clients`.
- API surface: one router per domain under `backend/app/api/` (15 modules — auth, employees, grants, exercise_requests, documents, export, reports, …), all mounted in `main.py` under `/api/v1`. Swagger UI at `/docs`.
- Sample request payloads for manual/API testing live in `json_samples/`.
- **Architecture map**: `ARCHITECTURE.md` — the ledger/event-sourcing core, the `company_scope` multi-tenant registry, the tax-rule-pack engine, and the document/PDF engine each require reading several files to understand; that file is the shortcut. Read it before touching any of those four areas, and update it when their *shape* changes (not on every feature — that's what `HANDOFF.md`/`docs/qa/` are for).

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
- **Run `python tools/context_check.py` at the end of every implementation step** (after the step's tests are green and `HANDOFF.md` is updated), and act on its verdict — **never ask the participant whether to continue here or open a new conversation.** That question is what this measurement replaces: 🟢 → start the next step in this conversation without asking; 🟡 → the step is closed and verified, so this is the moment: update `HANDOFF.md`, commit, and tell the participant to open a new conversation; 🔴 → stop, update `HANDOFF.md`, commit, and say the conversation must close now. The thresholds live in the script (`TURNS_WATCH/CLEAN`, `MB_WATCH/CLEAN`) — tune them there, not by judgment in the moment.
- A session does not close without updating `HANDOFF.md` — same standing rule as the version's QA file. Cost tracks conversation length, not task difficulty, so state must live in files and conversations must be allowed to end. Open a new conversation between versions.
- **`HANDOFF.md` holds the present only.** When a version closes, its block moves to `docs/handoff/<version>.md` — it does not accumulate at the root. The file grew to 97,449 chars by 14/08/2026 before the split, which made the one file read first in every conversation the largest context cost in the repo. `test_handoff_stays_small` enforces the budget; **never read the archived files** unless a historical question demands it — same rule as `docs/qa/`.
- `QA_TESTBOOK.md` is an index only; the test cases live in `docs/qa/<version>.md`. Never read the archived version files or re-merge them into one file.
- Prefer a subagent for read-only exploration ("where is X used", "does Y exist") so its reading stays out of this conversation's context.
- Don't dump whole files or full command output into the transcript when a targeted range or a count answers the question.
- **Doc map** — each top-level doc answers one question and stays out of the others' way (see also the table at the top of `GOAL.md`): why the project exists → `GOAL.md`; what's broken in the market → `MARKET_ANALYSIS.md`; what's built next and why the version order changed → `FEATURE_SPEC.md`; how the code is actually shaped → `ARCHITECTURE.md`; who builds what and how a version ships → `AGENT_WORKFLOW.md`; what's in flight right now → `HANDOFF.md` (history: `docs/handoff/<version>.md`, archive); what to test and the risk map → `QA_TESTBOOK.md` + `docs/qa/<version>.md`; what's allowed while working → this file.

## Useful commands

```bash
python -m uvicorn backend.app.main:app --reload            # dev server, http://127.0.0.1:8000, docs at /docs
pip install -r requirements.txt                             # deps are pinned
python -m pytest                                            # full suite (plain `pytest` is not on PATH here)
python -m pytest tests/test_tax_engine.py                   # one file
python -m pytest tests/test_tax_engine.py::test_never_modeled_combination_raises_with_that_reason   # one test
python tools/context_check.py                               # end of every step — 🟢 continue / 🟡🔴 close the conversation
```

There is no configured linter/formatter (no ruff/flake8/black/mypy in `requirements.txt`) — don't invent a lint step or assume one runs in CI.

Use `/clear` between unrelated exercises, `/context` to inspect context use, `/usage` to monitor the Claude plan, and `/rewind` when an implementation direction is wrong.
