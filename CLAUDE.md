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

There is no automated test suite yet (`requirements.txt` is currently empty and no `test_*.py` files exist) — verification today is manual: exercise the relevant endpoint via `/docs` or the matching `json_samples/*.json` payload, and check the three client portals if the change touches them. A feature is complete only when:

- It solves the stated problem, checked against real request/response data (not just "the endpoint returns 200").
- Tax/financial calculations are verified by hand against the specific track's real rule, with the worked example recorded somewhere reviewable.
- The change respects existing DB constraints (nothing silently violates a check constraint or foreign key).
- The current git diff has received an independent review (`.claude/agents/change-reviewer.md`).
- The participant can explain the decision, tradeoffs, and remaining risk.

If you add the first real tests, also add the run command here (e.g. `pytest`) and update this section to require it going forward — don't leave this section stale once that infrastructure exists.

## Useful commands

```bash
python -m uvicorn backend.app.main:app --reload   # dev server, http://127.0.0.1:8000, docs at /docs
pip install -r requirements.txt                    # currently empty -- fill in as real deps are pinned
```

Use `/clear` between unrelated exercises, `/context` to inspect context use, `/usage` to monitor the Claude plan, and `/rewind` when an implementation direction is wrong.
