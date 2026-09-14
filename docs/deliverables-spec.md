# Deliverables spec — evidence, CLI, README

Closes deliverables 1 and 3 from brief §6. `REPORT.md` (deliverable 2) is written
separately by hand.

The brief is explicit: *"Please use these exact paths and headings — we read a lot of
submissions side by side."* Treat the paths as literal requirements.

---

## 1. The demo path must work from a clean clone

This is the highest-risk item in the whole submission. A reviewer clones the repo, follows
the README, and hits an error — that outcome undoes a lot of good work.

Verify by actually doing it:

```bash
git clone <repo> /tmp/verify && cd /tmp/verify
```

Then follow the README literally, typing nothing that is not written in it. Fix whatever
breaks. Known candidates:

- `PYTHONPATH` — scripts must run without a prefix. Either `pip install -e .` is documented
  or `pyproject.toml` handles it. No `PYTHONPATH=.` in documented commands.
- `playwright install chromium` must be listed; the pip package alone is not enough.
- `.env` — `.env.example` must exist and list both provider keys.
- Port collisions from a previous run.
- `pillow` is test-only; note it or move it.

Delete `/tmp/verify` afterwards.

---

## 2. CLI surface

One entry point, documented commands:

```
python -m src.cli discover --goal "..." --target <url> [--max-steps N]
python -m src.cli replay --capability <id> --input member_id=10001 [--tenant <id>]
python -m src.cli operator
python -m src.cli capabilities          # list saved artifacts with inputs/outputs
python -m src.cli show <capability-id>  # pretty-print an artifact for review
```

`capabilities` and `show` are small and serve two purposes. `show` satisfies the schema's
"reviewable by a human" requirement without hand-editing JSON. `capabilities` is the
minimal version of the agent-facing catalog stretch goal — a listing of callable
capabilities with typed signatures.

Every command must print a clear structured result and exit non-zero on failure.

---

## 3. `/evidence/` contents

The brief asks for: a saved example artifact, logs from a discovery run and a replay run,
and ideally one replay that hits an error or exceptional state.

Curate this. A directory of thirty acceptance-run leftovers is worse than six clearly named
ones, because a reviewer cannot tell which matter.

```
/evidence/
  README.md                       ← what each directory shows, in order
  artifact/
    member.lookup_savings_balance.json
  discovery/                      ← the real LLM run
    trace.jsonl
    discovery.jsonl
    artifact.draft.json
    step_*.png
  probe/
    probe.jsonl
  replay-success/                 ← happy path
    replay.jsonl
    result.json
  replay-not-found/               ← business outcome, NOT a failure
    replay.jsonl
    result.json
  replay-permission-denied/       ← second business outcome
  replay-hard-failure/            ← injected app error, default deny
    replay.jsonl
    result.json
    step_*.png
  replay-recovery/                ← session expiry recovered mid-flow
    replay.jsonl
  escalation/                     ← handoff, human takeover, resume
    control.json
    intervention.json
    human_actions.jsonl
    replay.jsonl
    *.png
  determinism/
    three-identical-runs.txt      ← same sequence, same strategy_index
```

### `/evidence/README.md` is the most-read file here

Reviewers will not reconstruct the story from JSONL. Walk them through it in order:

> 1. `discovery/` — a real Gemini-driven run against the target app. `trace.jsonl` shows
>    observe → decide → act with the model's actions.
> 2. `artifact/` — what the compiler produced from that run. Note `{{member_id}}` as a
>    declared input, ranked locator strategies, and `approval_state: draft`.
> 3. `replay-success/` — that same artifact replayed with no model. Compare the event count.
> 4. `replay-not-found/` — `member_id=99999`. Returns `MEMBER_NOT_FOUND` as a business
>    outcome. **Note the absence of any error or failure event.**
> 5. `replay-hard-failure/` — an injected server error matching no declared detector.
>    Default deny: step ID, expected, observed, screenshot.
> 6. `escalation/` — a run that could not recover, ceding the live session to a human and
>    resuming afterwards.

Point at the contrast between items 4 and 5 explicitly. Same engine, same code path, one
clean outcome and one hard failure, and the difference comes entirely from artifact-declared
detectors. That is the distinction the brief's glossary calls the most common design mistake.

### Sanity checks on evidence

- [ ] No sensitive value appears in any committed `.jsonl` or `result.json`
- [ ] No API key anywhere under `/evidence/`
- [ ] Screenshots have sensitive regions masked
- [ ] Total size is reasonable to clone — drop large `trace.zip` files, or keep one
- [ ] `replay-not-found/replay.jsonl` contains zero error/failure events
- [ ] The artifact in `/evidence/artifact/` is byte-identical to the shipped one

---

## 4. `/README.md`

The brief names two required contents: how to set up and run it including keys and config,
and a demo path with the exact commands. Structure:

**What this is** — three or four sentences. An LLM discovers a flow in a UI with no API; the
run compiles into a typed capability artifact; the artifact replays deterministically with no
model in the loop.

**Setup** — Python version, venv, install, `playwright install chromium`, `.env` from
`.env.example`, which provider keys and where to get them. Note that replay needs no API key
at all, only discovery does. That is a good detail: the production path has no model
dependency.

**Running the target app** — both tenants, both ports, the demo login.

**The demo path** — literal, copy-pasteable, in order:

```bash
# 1. discovery: a real LLM run against the app
python -m src.cli discover \
  --goal "Look up member 10001 and read their savings balance" \
  --target http://127.0.0.1:5001/members/search

# 2. replay the artifact it produced — no LLM
python -m src.cli replay --capability member.lookup_savings_balance --input member_id=10001

# 3. replay hitting a business outcome
python -m src.cli replay --capability member.lookup_savings_balance --input member_id=99999
```

Show expected output for each. A reviewer who sees what should happen can tell whether it did.

**Failure injection** — how to trigger each `/_test/config` flag, so a reviewer can reproduce
the error cases themselves rather than trusting the logs.

**Escalation demo** — start the operator app, trigger an escalation, claim it, act, release.

**Tests** — `pytest` (234), plus the four acceptance scripts and what each covers.

**Repo layout** — a short tree with one line per package.

**What is mocked** — operator auth, identity, queueing, co-browsing; no desktop surface.
State it here as well as in the REPORT; a reviewer reading only the README should not
mistake a deliberate cut for an oversight.

---

## 5. Housekeeping

- [ ] `.env` untracked and absent from history
- [ ] `docs/interview-notes.md` untracked
- [ ] No `__pycache__`, `.venv`, `.pytest_cache` committed
- [ ] Dead scaffolding removed — the hand-written v0 artifact is gone
- [ ] `requirements.txt` current
- [ ] Repo is public
- [ ] `/README.md`, `/REPORT.md`, `/evidence/` exist at exactly those paths

---

## 6. Acceptance checks

- [ ] A clean clone into a fresh directory, following only the README, reaches a successful
      discovery run
- [ ] Then a successful replay of the produced artifact
- [ ] Then a `99999` replay returning a business outcome
- [ ] `python -m src.cli capabilities` lists the capability with typed inputs and outputs
- [ ] `python -m src.cli show <id>` prints a readable artifact
- [ ] Every command in the README has been executed verbatim and works
- [ ] `/evidence/README.md` walks the thread in order and names the 4-vs-5 contrast
- [ ] No secret or sensitive value anywhere in the repo or its history
- [ ] `pytest` passes from a clean clone
