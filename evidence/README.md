# Evidence

Curated, not exhaustive. This is a handful of runs that tell the whole story, in
order — a real discovery run, the artifact it compiled into, that artifact replayed
deterministically down three different paths, and one full escalation. Every JSONL
and PNG here came out of an actual run against the live target app; nothing is
hand-written.

Read these in order:

## 1. `discovery/` — the real, LLM-driven run

A genuine Gemini-driven `discover` CLI run against the live meridian tenant (goal:
*"Look up member 10001 and read their savings balance"*). No scripted actions —
the model chose every step.

- `trace.jsonl` — the agent loop's own record: observe → decide → act, one entry
  per turn, with the model's reasoning and the action it chose.
- `discovery.jsonl` — the run's evidence log (navigation, resolves, checkpoints).
- `step_00.png` / `step_01.png` / `step_02.png` — a screenshot after each of the
  model's actions.
- `artifact.draft.json` — what `compile_trace` produced from that run, before
  human review: `approval_state: "draft"`, version `0.1.0`.

## 2. `probe/` — the probe pass

Immediately after a successful discovery run, the compiler re-runs the captured
flow once more with a deliberately invalid `member_id` to see what the app does
on a *different* path than the one just recorded — how else would a not-found
outcome ever get declared. `probe.jsonl` shows step_003's checkpoint failing
against the substituted value and the resulting `MEMBER_NOT_FOUND` outcome being
derived from that divergence, not guessed at.

## 3. `artifact/` — what shipped

`member.lookup_savings_balance.json`, byte-identical to `artifacts/member.lookup_savings_balance.json`
in the repo root. Compare it against `discovery/artifact.draft.json` to see what a
human reviewer added on top of the draft:

- `approval_state` moved from `draft` to `approved`, version `0.1.0` → `1.0.0`.
- `{{member_id}}` declared as a templated, `sensitive` input with a `^[0-9]{5}$`
  constraint — the probe pass exercises it, but the pattern itself is a reviewer's
  addition.
- A second business outcome, `PERMISSION_DENIED`, added by hand — the probe pass
  only varies the declared parameter, not member status, so it can never discover
  that member 10004 is restricted on its own.
- Two `recoveries` (`CONFIRMATION_INTERSTITIAL`, `SESSION_EXPIRED`) — neither
  fired during the one discovery run that produced the draft, so neither could be
  learned from it. They're declared from knowledge of the app, the same way an
  engineer reviewing a first successful run would annotate known failure modes
  before shipping it.

Run `python -m src.cli show member.lookup_savings_balance` for the same artifact
rendered for human review instead of raw JSON.

## 4. `replay-success/` — that same artifact, replayed with no model

`member_id=10001`. Same four steps as the discovery run, zero LLM calls. Compare
`replay.jsonl`'s event count and shape against `discovery/discovery.jsonl` — the
replay is a deterministic re-execution of exactly what the artifact declares, not
a fresh exploration.

## 5. `replay-not-found/` and `replay-permission-denied/` — business outcomes

`member_id=99999` (unseeded) and `member_id=10004` (restricted), respectively.
Both terminate with `status: "business_outcome"` and a declared `outcome.code`
(`MEMBER_NOT_FOUND`, `PERMISSION_DENIED`). **Note the absence of any error or
failure event in either `replay.jsonl`.** These are not failures — the artifact's
own `outcomes` list declared exactly these detectors in advance, so hitting them
is expected, typed behavior, not an exception.

## 6. `replay-hard-failure/` — the contrast that matters most

An injected server error (`POST /_test/config {"error_on_next": true}`) that
matches *no* declared detector — not an outcome, not a recovery. `result.json`
shows `status: "failure"`, `error.code: "CHECKPOINT_FAILED"`, the step ID,
what was expected, what was actually observed, and `step_003.png` as visual
evidence. The engine does not guess, retry blindly, or degrade gracefully. It
stops and reports exactly what it saw. Default deny.

**Compare directories 5 and 6 directly.** Same artifact, same replay engine, same
code path — the only difference between a clean business outcome and a hard
failure is whether the artifact declared a detector for what happened. That
distinction, entirely data-driven and never inferred by the engine at runtime, is
the design mistake this project is built around not making.

## 7. `replay-recovery/` — a mid-flow recoverable condition

The target app's session TTL lapses mid-run (`POST /_test/config
{"expire_session": true}`), so the app redirects to `/login` partway through the
flow. `replay.jsonl` shows two `SESSION_EXPIRED` recoveries (once at `step_001`,
once again at `step_003` after the injected expiry) and the run still finishes
with `status: "success"` — the artifact's own declared recovery handled it, no
model involved.

## 8. `escalation/` — a run that hands off to a human and resumes

A step is forced to `requires_approval` by the policy gate. `control.json` and
`intervention.json` show the session transitioning `AWAITING_HUMAN` → `HUMAN`
(claimed by `the-operator` over the mock operator API), the CDP endpoint handed
back so the "human" browser tab attaches to the *live, already-open* session
rather than a fresh one, and `human_actions.jsonl` recording what was typed and
clicked while control was ceded. `replay.jsonl` shows two escalation rounds: the
first release doesn't finish the capability (`handback verdict: unrecognized`),
so control is re-escalated automatically rather than assumed complete; the second
does (`verdict: capability_success`), and the run reports `status: "success"`
with no re-execution of anything the human already did. `intervention_step_002.png`
is the screenshot handed to the operator; `reacquire_step_002_{1,2}.png` are what
the engine saw each time it got the session back.

## 9. `determinism/`

`three-identical-runs.txt` — the same capability, same input, replayed three
times back-to-back against a freshly reset app. Same event sequence, same
`strategy_index` chosen at every locator resolution, same outcome, every time.
No LLM anywhere in these three runs.

---

## Sanity checks

- [x] No sensitive value appears in any committed `.jsonl` or `result.json` —
      values marked `sensitive` in the artifact (`member_id`, `savings_balance`)
      are written to disk as `REDACTED`.
- [x] No API key anywhere under `/evidence/`.
- [x] Screenshots contain no real personal data — the app's seed data is fully
      fictional (docs/target-app-spec.md).
- [x] Total size is small enough to clone without thinking about it — no
      `trace.zip` files are kept here; Playwright traces exist only as a
      `--keep-trace` opt-in for local debugging, not as a repo artifact.
- [x] `replay-not-found/replay.jsonl` contains zero error/failure events.
- [x] `artifact/member.lookup_savings_balance.json` is byte-identical to
      `artifacts/member.lookup_savings_balance.json`.
