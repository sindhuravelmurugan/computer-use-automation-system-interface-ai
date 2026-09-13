# Replay engine spec

The production execution path. Given a saved artifact and input parameters, execute the
recorded flow with **no LLM in the decision loop** and return a structured result.

This is the component the brief weights most heavily alongside the schema. Its job is
not to be clever — it is to be *predictable*, and to be honest about what it observed.

---

## 1. Contract

```python
class ReplayEngine:
    def run(
        self,
        artifact: CapabilityArtifact,
        inputs: dict[str, Any],
        context: ReplayContext,
    ) -> ReplayResult: ...
```

```python
@dataclass
class ReplayContext:
    run_id: str
    tenant_id: str | None = None       # selects an override file, if any
    attended: bool = False             # is an operator available right now
    allow_risky: bool = False          # gate on irreversible steps
    keep_trace: bool = False
```

`attended` and `allow_risky` are caller-supplied because the same capability behaves
differently when an AI agent invokes it unattended at 2am versus when an operator runs
it with a console open.

---

## 2. Pre-flight — before the browser opens

Everything cheap that can reject the run happens first. Opening a browser against a
bank system to discover a typo'd member ID is waste.

In order:

1. **Approval state.** `draft` artifacts refuse to run unattended. Regulated
   environments run on change control; unreviewed automation does not touch accounts.
2. **Input validation.** Types, `required`, and `constraints` from the artifact's
   declared inputs. Reject with a structured error naming the offending parameter.
3. **Override merge.** If `context.tenant_id` is set, load the tenant override and merge
   it over the base artifact. The merge is additive-only — it may re-target steps and
   adjust detectors, never add or remove steps. A structural override is a hard error,
   not a silent hybrid.
4. **Allowlist check.** The artifact's `surface.entry_point` must be permitted. Step 6
   owns the policy gate; see §7 for the seam to build now.
5. **Risk gate.** If the artifact contains any `risk: "risky"` step and
   `allow_risky=False`, refuse before starting rather than stopping halfway through an
   irreversible flow.

Each failure here returns a `ReplayResult` with `status="failure"` and a distinct error
code. None of them opens a browser.

---

## 3. Version fingerprint

After `open()` and the first `observe()`, compare `PageSignature.app_version` against
`artifact.provenance.recorded_against`.

Mismatch is **a warning recorded in the result and the log, not a hard stop.** The
artifact may well still work; a stable UI is the premise of the whole system. But the
mismatch must be visible, because it is the first thing a human should look at when a
previously-reliable capability starts failing.

This plus `strategy_index` (§5) is the complete drift story. No scheduled diffing, no
separate monitoring system.

---

## 4. The per-step loop

For each step, in order. **Detectors run on every step, not only on checkpoint failure.**
A disclosure modal can appear over a page whose heading still satisfies the checkpoint —
skip detection there and the modal breaks the *next* step, where the error will be
misleading.

```
resolve target  ->  act  ->  observe  ->  1. recoveries
                                          2. outcomes
                                          3. checkpoint
                                          4. default deny
```

### 4.1 Resolve

`surface.resolve(step.target)`. Record `strategy_index` in the log every time.

- `not_found` → no detector may explain it → hard failure
- `ambiguous` → hard failure. **Never take the first match.** Silently acting on one of
  several candidates is worse than failing in this domain.

### 4.2 Act

`surface.act(...)`, which returns a fresh observation. The engine never calls `observe()`
separately for this purpose — the post-action state comes back with the result.

### 4.3 Recovery detectors — first

Scoped by `check_after` (a step ID list, or `"any"`).

On match, if attempts remain for that `(code, step_id)` pair:
- apply the declared `recovery` action
- follow `then`: `retry_step`, `continue`, `restart_from` (with `restart_step_id`), or
  `escalate`
- increment the attempt ledger; append to `recoveries_applied` in the result

Recoveries run before outcomes deliberately. A modal overlaying a not-found page must be
dismissed before you decide what the page says. Judge the state you can actually see.

**On exhausting `max_attempts`:** see §6. The condition is promoted; it never loops.

### 4.4 Outcome detectors — second

Scoped by `check_after`. On match:
- if `terminal: true`, stop the run and return `status="business_outcome"` with the
  declared `returns`
- this is a **success path**. Nothing is logged as an error.

### 4.5 Checkpoint — third

Assert the step's declared post-condition against the current observation. Pass →
continue. Fail → hard failure carrying `step_id`, the checkpoint's `expected`, the
observed value, and a screenshot.

### 4.6 Default deny

Any state that matched no recovery, no outcome, and failed its checkpoint is a hard
failure. **The engine never improvises.** There is no fallback reasoning, no retry of an
unclassified condition, and explicitly no LLM consulted.

---

## 5. Determinism

What makes replay reproducible:

| Mechanism | Why |
|---|---|
| No LLM import anywhere under `src/replay/` | Enforced by test, not convention |
| Condition-based waits only | `WaitSpec` strategies `load`/`settle`/`condition`. **No `sleep(n)` anywhere** — fixed sleeps are the main source of flaky replay |
| Ranked locator strategies, first match wins | Deterministic given the same page |
| Ambiguity refuses rather than guesses | Removes the main source of silent divergence |
| `state_hash` before/after each action | Detects "the click did nothing" |
| Attempt ledger keyed `(code, step_id)` | Recovery is bounded; no unbounded loops |

Add a test asserting no module under `src/replay/` imports `playwright`, `anthropic`, or
`google.genai`.

---

## 6. Escalation seam

When a recovery exhausts `max_attempts`, or a recovery's `then` is `escalate`:

**Always raise an `InterventionRequest`.** Unconditionally. In a regulated system, a run
that hit a condition it could not resolve must leave something a human reviews —
particularly if it stopped partway through a write flow and left state behind.

**Conditionally hold the session.** Separate decision:

| Condition | Session |
|---|---|
| `attended=True` | hold open for takeover |
| Flow left uncommitted mutable state | hold open |
| Unattended read-only flow | close; the caller gets a failure immediately |

The caller always receives a structured result promptly. Notification and blocking are
decoupled — an AI agent invoking a balance lookup at 2am cannot wait for an operator,
but the operator must still find out.

For step 4, build: the `InterventionRequest` type, raising it, writing it to
`evidence/{run_id}/intervention.json` with capability, step ID, reason, redacted state
and screenshot, and returning a failure. The actual takeover mechanism is step 7.

```python
@dataclass
class InterventionRequest:
    run_id: str
    capability_id: str
    capability_version: str
    step_id: str
    reason: str                  # EXHAUSTED_RECOVERY | ESCALATE_DIRECTIVE | ...
    page: PageSignature
    screenshot_path: str
    session_held: bool
    raised_at: datetime
```

---

## 7. Policy gate seam (step 6 owns it)

Define the interface now, implement a permissive default:

```python
class PolicyGate(Protocol):
    def check(self, action: Action, ctx: ReplayContext) -> PolicyDecision: ...
```

Ship an `AllowAllGate` for now with a comment pointing at step 6, plus the allowlist
check in pre-flight (§2.4) since that one is cheap and real. The engine must call
`gate.check()` before every action from the start — retrofitting a choke point later
means finding every call site.

---

## 8. Outputs and success

- Extract declared outputs at their bound `source.step_id`, applying the named
  `transform` from the fixed registry (`parse_currency`, `trim`, `parse_date`, `raw`).
  Never arbitrary code.
- Verify the capability-level `success.checkpoint`. Reaching the last step is not
  success — asserting the expected end state is.
- If `require_all_outputs` and any required output failed to extract → hard failure, not
  a partial success.

---

## 9. Evidence

**JSONL, always**, at `evidence/{run_id}/replay.jsonl`. One line per event:

```json
{"seq":4,"event":"resolve","step_id":"step_002","strategy_index":0,"status":"resolved"}
{"seq":5,"event":"action","step_id":"step_002","kind":"type","duration_ms":180}
{"seq":6,"event":"checkpoint","step_id":"step_002","result":"pass"}
{"seq":7,"event":"recovery","code":"CONFIRMATION_INTERSTITIAL","step_id":"step_003","attempt":1}
```

`strategy_index` on every resolve is non-negotiable — it is the drift signal.

**Screenshots** on: hard failure, checkpoint mismatch, escalation, and final state on
success. Masked using the `bounds` of nodes bound to `sensitive` outputs.

**Redaction.** Values of `sensitive` inputs and outputs never reach the log. They are
substituted at execution time and rendered as `"REDACTED"` everywhere else — including
in the returned `ReplayResult` when it is serialized to disk. The in-memory result
returned to the caller carries the real value.

---

## 10. What to build now

**Build:** the engine, detector evaluation, the attempt ledger, output extraction and
transforms, success verification, result construction, evidence writing, the
`InterventionRequest` type and raising path, the `PolicyGate` protocol with a permissive
default.

**Hand-write a test artifact** at `artifacts/member.lookup_savings_balance.v0.json`
covering the search → detail → read flow, with declared `MEMBER_NOT_FOUND`,
`SESSION_EXPIRED`, and `CONFIRMATION_INTERSTITIAL`. This is scaffolding — step 5's
discovery run produces the real one and this gets deleted. It exists so replay can be
debugged without the LLM in play.

**Do not build:** the discovery agent, the compiler, the real policy gate, the operator
UI.

---

## 11. Acceptance checks

Against the target app on 5001, using the hand-written artifact. Each drives a
`/_test/config` flag where relevant.

- [ ] `member_id=10001` → `status="success"`, `savings_balance` parsed as a decimal
      (comma handled), success checkpoint verified
- [ ] `member_id=99999` → `status="business_outcome"`, code `MEMBER_NOT_FOUND`. **Not a
      failure.** Nothing in the log marked as an error
- [ ] `member_id=10004` (restricted) → `status="failure"` with a distinct code,
      screenshot written
- [ ] `expire_session` flag → `SESSION_EXPIRED` recovery fires, run completes,
      `recoveries_applied` non-empty
- [ ] `modal_on_next=2` → interstitial dismissed, run completes, attempts recorded
- [ ] `latency_ms=4000` → wait strategy absorbs it without a fixed sleep
- [ ] `error_on_next` → hard failure with step ID, expected, observed, screenshot
- [ ] A recovery whose `max_attempts` is exhausted → `InterventionRequest` written to
      `evidence/{run_id}/intervention.json`, failure returned promptly
- [ ] `attended=False` on a read-only flow → session closed after escalation;
      `attended=True` → held
- [ ] Bad input (`member_id="abc"`) → rejected in pre-flight, **browser never opened**
- [ ] `draft` approval state + unattended → refused before pre-flight completes
- [ ] Three consecutive runs of `10001` produce byte-identical outputs and the same
      `strategy_index` sequence
- [ ] No module under `src/replay/` imports `playwright`, `anthropic`, or `google.genai`
- [ ] No `sleep(` anywhere under `src/replay/`
- [ ] A `sensitive` output's value appears nowhere in `replay.jsonl` or the saved result

The `99999` case is the one to get right. If it appears anywhere in the logs as an
error, the business-outcome model has leaked into the failure path — which the brief
names as the most common design mistake in this problem.
