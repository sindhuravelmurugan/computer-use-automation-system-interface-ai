# computer-use-automation-system-interface-ai

A computer-use automation system for legacy back-office applications that have no API.

## What this is

An LLM discovers how to accomplish a goal by driving a real web UI — no API, just
the accessibility tree and a browser. The successful run is compiled into a typed,
versioned **capability artifact**: declared inputs and outputs, ranked locator
strategies per step, checkpoints, business outcomes, and recovery rules. That
artifact then replays **deterministically, with no LLM in the decision loop** —
same input, same steps, same outcome, every time. AI agents (or anything else)
invoke capabilities by name in production; only the one-time discovery run ever
talks to a model.

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env
```

Edit `.env` and set `GEMINI_API_KEY` (or `ANTHROPIC_API_KEY` and
`LLM_PROVIDER=anthropic`) — get one at
[aistudio.google.com/apikey](https://aistudio.google.com/apikey) or
[console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys).

**Only `discover` needs a key.** `replay`, `capabilities`, `show`, and `operator`
never call an LLM — that's the point. The production path (replay) has no model
dependency at all; you can run the entire demo below except the `discover` step
with `.env` empty.

## Running the target app

Two tenants, one codebase, run simultaneously on separate ports. In two terminals:

```bash
TENANT=meridian  PORT=5001 flask run
```

```bash
TENANT=riverbend PORT=5002 flask run
```

Log in with any username and password `demo`. The shipped artifact and the demo
below both target `meridian` on port 5001; `riverbend` on 5002 exists to show the
same artifact re-targeted across tenants (differently labeled fields, same flow —
see `docs/target-app-spec.md`).

## The demo path

Run these in order from the repo root, with the meridian tenant (port 5001) up.

```bash
# 1. discovery: a real LLM run against the app
python -m src.cli discover \
  --goal "Look up member 10001 and read their savings balance" \
  --target http://127.0.0.1:5001/members/search
```

Expected output (run id and evidence path will differ each time):

```
run_id=discover-<timestamp> stop_reason=SUCCESS
probe: probed successfully at step_003
draft artifact written to evidence/discover-<timestamp>/artifact.draft.json
review notes:
  - step_001: Synthesized by the compiler, not discovered: ...
```

This writes a fresh **draft** artifact under `evidence/discover-<timestamp>/` —
your own live run. It does not touch `artifacts/`. The commands below replay the
artifact that's already checked in at `artifacts/member.lookup_savings_balance.json`:
the reviewed, promoted result of an earlier discovery run exactly like the one you
just ran (that run's own trail is in `evidence/discovery/` — see
`evidence/README.md` for what a human reviewer added on top of the raw draft).

```bash
# 2. replay the artifact — no LLM
python -m src.cli replay --capability member.lookup_savings_balance --input member_id=10001
```

Expected output:

```
run_id=replay-<timestamp> status=success
outputs: {
  "savings_balance": "4832.10"
}
evidence: evidence/replay-<timestamp>/
```

```bash
# 3. replay hitting a business outcome
python -m src.cli replay --capability member.lookup_savings_balance --input member_id=99999
```

Expected output:

```
run_id=replay-<timestamp> status=business_outcome
outcome: MEMBER_NOT_FOUND
returns: {
  "found": false
}
evidence: evidence/replay-<timestamp>/
```

`status` is one of `success`, `business_outcome`, or `failure` — the CLI only
exits non-zero on `failure`. A not-found member is expected, typed behavior, not
an error; see `evidence/replay-not-found/` and the contrast with
`evidence/replay-hard-failure/` in `evidence/README.md`.

Two more commands round out the CLI surface:

```bash
python -m src.cli capabilities
```

```
member.lookup_savings_balance  v1.0.0  [approved]
  Discovered from the goal: Look up member 10001 and read their savings balance
  inputs:  member_id:string
  outputs: savings_balance:money
```

```bash
python -m src.cli show member.lookup_savings_balance
```

Pretty-prints the full artifact — steps, checkpoints, declared outcomes and
recoveries, success condition — for human review without hand-editing JSON.

Each `replay` run writes its own `evidence/replay-<timestamp>/` directory. Those
are scratch output from your own runs, not tracked in git — delete them freely;
they don't affect `evidence/`, which is curated and committed separately (see
`evidence/README.md`).

## Failure injection

The target app exposes a test-control endpoint, excluded from the automation
policy allowlist so the agent itself can never reach it:

```bash
curl -X POST http://127.0.0.1:5001/_test/config -H 'Content-Type: application/json' -d '{"expire_session": true}'
curl -X POST http://127.0.0.1:5001/_test/config -H 'Content-Type: application/json' -d '{"modal_on_next": 2}'
curl -X POST http://127.0.0.1:5001/_test/config -H 'Content-Type: application/json' -d '{"latency_ms": 4000}'
curl -X POST http://127.0.0.1:5001/_test/config -H 'Content-Type: application/json' -d '{"error_on_next": true}'
curl -X POST http://127.0.0.1:5001/_test/reset
```

| Flag | Effect | Artifact handling |
|---|---|---|
| `expire_session: true` | Invalidates the session now; next request 302s to `/login?expired=1` | `SESSION_EXPIRED` recovery: re-authenticate, restart from `step_001` |
| `modal_on_next: N` | Next N page renders show a disclosure modal | `CONFIRMATION_INTERSTITIAL` recovery: click Acknowledge, retry the step |
| `latency_ms: N` | Server-side delay on the next response | Exercises wait/timeout handling |
| `error_on_next: true` | Next request returns a 500 error page | No declared detector — hard failure, default deny |

Also deterministic without any flag: search `99999` for `MEMBER_NOT_FOUND`, or
`10004` (restricted) for `PERMISSION_DENIED` — both declared business outcomes on
the shipped artifact. `/_test/reset` clears every flag and reseeds the data, so
each of these is reproducible from a clean state every time.

## Escalation demo

The shipped capability has no risky steps of its own (nothing here writes data),
so the escalation path is exercised the same way `evidence/escalation/` was
produced: by forcing the policy gate to require approval on a step, so replay
blocks, cedes the live browser session to the operator surface, and resumes once
a human releases it back. With the meridian tenant up on 5001:

```bash
python -m scripts.escalation_acceptance
```

This runs a real two-round handoff end to end — `AWAITING_HUMAN` → claim → a human
(a second, independent Playwright connection over the same CDP endpoint) acting on
the live session → release → resume — printing a `[PASS]`/`[FAIL]` line per
property checked (session stays alive while ceded, the operator API lists and
claims the request, `Surface.act()` refuses automation input while control is
ceded, a human's completed flow is recognized without re-execution). It writes
`evidence/escalation-two-round-handoff/` on disk, the same shape as the curated
`evidence/escalation/` copy.

To poke at the operator API directly against any open request (including one left
by the script above before it fully resolves — add a breakpoint or slow it down
if you want a window to do this manually):

```bash
python -m src.cli operator   # separate terminal, port 5055 by default
```

```bash
curl http://127.0.0.1:5055/operator                              # list open requests
curl http://127.0.0.1:5055/operator/<run_id>                     # detail: capability, step, reason, screenshot
curl -X POST http://127.0.0.1:5055/operator/<run_id>/claim -H 'Content-Type: application/json' -d '{"name": "me"}'
curl -X POST http://127.0.0.1:5055/operator/<run_id>/release
```

## Tests

```bash
pytest
```

234 tests: schema validation, detector matching, override merging, redaction,
policy classification, compiler/probe logic, escalation state machine — the units
that carry real design weight, run against fakes, no browser or LLM required.

Four acceptance scripts exercise the same behaviors against the *live* app and a
real `Surface`/`ReplayEngine` (and, once each, a real LLM call) — what a unit test
against a fake can't confirm:

```bash
# with both tenants running (5001, 5002):
python -m scripts.surface_acceptance      # a11y perception, frame flattening, ranked resolution
python -m scripts.discovery_acceptance    # one real LLM run, its compiled artifact, both replays of it
python -m scripts.policy_acceptance       # pre-flight denial, risky-step verdicts, live redaction
python -m scripts.escalation_acceptance   # claim/act/release over a live CDP session, two-round handoff
```

## Repo layout

```
/target_app/     Flask app being automated — the stand-in for a bank system
/src/
  schema/        Pydantic models: artifact, result contract, overrides
  surface/       Surface protocol + web implementation (Playwright/a11y)
  agent/         LLM discovery loop, action tools, prompts
  compiler/      trace -> capability artifact
  replay/        deterministic executor, detectors, result builder
  policy/        allowlist, risk classification, redaction
  escalation/    intervention requests, session control state machine
  cli.py         discover / replay / capabilities / show / operator
/artifacts/      saved capability artifacts
/evidence/       curated run logs, screenshots, one saved artifact copy
/scripts/        live acceptance checks (see Tests, above)
/tests/          pytest suite
```

## What is mocked

Explicitly out of scope, by design (see `CLAUDE.md`): operator authentication
(the mock operator API takes a free-text name, nothing more), agent/caller
identity, request queueing across multiple concurrent runs, real-time
co-browsing, and any desktop/native-app surface — only the web `Surface`
implementation exists. None of these were cut by oversight; the brief doesn't
reward building scaling infrastructure, so the seams are designed (the `Surface`
protocol, the policy gate's single choke point) without building the plumbing
behind them.
