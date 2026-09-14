# Escalation & handoff spec

Closes brief requirement 3.6. Nine things are required; one (raising an intervention
request with context) already exists. This spec covers the other eight.

The scope note permits mocking the operator UI. It does **not** permit mocking the
handoff mechanism or the control-transfer model — those must be real.

---

## 1. The control-transfer model

The brief says there must be a way to know "who is (or should be) in control." The
parenthetical is doing work: *should be* can differ from *is*. An intervention can be
raised and not yet picked up. A human can take over and walk away. So ownership is an
explicit state with timeouts, not a boolean.

```python
class Controller(StrEnum):
    AUTOMATION = "automation"
    AWAITING_HUMAN = "awaiting_human"   # raised, nobody has claimed it
    HUMAN = "human"                     # claimed and operating
    RETURNING = "returning"             # released, engine re-verifying
    ABANDONED = "abandoned"             # claim timed out
```

```
AUTOMATION ──escalate──▶ AWAITING_HUMAN ──claim──▶ HUMAN
                              │                      │
                          claim_timeout          release
                              ▼                      ▼
                          ABANDONED             RETURNING ──▶ AUTOMATION
                                                    │
                                              verification fails
                                                    ▼
                                              AWAITING_HUMAN (re-escalate)
```

**Only the controller may act.** `Surface.act()` checks the current controller and
raises if automation attempts an action while the session is owned by a human. This is
the enforcement that makes "who is in control" meaningful rather than advisory — the
same reasoning as the policy gate being a single choke point.

Two timeouts, both configurable:

- **claim timeout** — `AWAITING_HUMAN` with nobody claiming → `ABANDONED`, run fails
  with the intervention still on record
- **hold timeout** — `HUMAN` with no activity → session closed, run fails, state
  recorded. A held browser session against a bank system is not something to leave open
  indefinitely

State lives in `SessionControl`, persisted to `evidence/{run_id}/control.json` so it
survives across the process boundary between engine and operator surface.

---

## 2. Triggers

The brief lists three. All must route to the same mechanism.

| Trigger | Source | Exists? |
|---|---|---|
| Agent stuck during discovery | model emits `stuck`, or `NO_PROGRESS` | request raised, handoff missing |
| Replay hits unrecoverable condition | exhausted `max_attempts`, `then: escalate` | request raised, handoff missing |
| Risky step needs a decision | `requires_approval` from the policy gate | request raised, handoff missing |

Discovery escalation is a real requirement, not replay-only. When the agent is stuck, a
human takes over the same browser, does the manual steps, and hands back — and the
actions they took are recorded into the trace so the compiler can incorporate them.

That last point matters: a human unblocking a discovery run is contributing to the
recorded flow. Their actions belong in the artifact, marked as human-contributed.

---

## 3. Ceding the session

`Surface.release()` and `reacquire()` are currently stubbed. The browser is already
launched with a remote debugging port, which was the expensive part.

```python
@dataclass
class SessionHandle:
    cdp_endpoint: str        # ws://127.0.0.1:PORT/devtools/browser/...
    page_url: str
    run_id: str
    released_at: datetime
```

**`release()`** — stop issuing commands, flush evidence, stop the Playwright trace
segment, write `control.json` with `AWAITING_HUMAN`, and return the handle. **The
browser context stays alive.** Nothing is closed.

**`reacquire(handle)`** — reconnect over the same CDP endpoint, re-observe, resume trace
recording, set controller to `RETURNING`.

Same browser, same cookies, same session, same page. That is what "the same live
session — not a fresh one" requires, and connecting over CDP is what makes it literally
true rather than approximately true.

---

## 4. Recording what the human did

Required by the brief, and the least obvious part to build. The human is driving a
browser the engine is not controlling, so before/after diffing is not enough — it would
miss the path taken.

Inject a capture script via CDP at release time that listens for `click`, `input`,
`change`, and `submit`, and records for each:

```json
{
  "seq": 3,
  "at": "2026-09-13T14:22:31Z",
  "kind": "click",
  "role": "button",
  "name": "Acknowledge",
  "frame_path": ["main"],
  "url": "http://127.0.0.1:5001/members/10001"
}
```

Note what is captured: **role and accessible name, not selectors and not typed values.**
Two reasons. It matches the vocabulary the rest of the system speaks, so human actions
are comparable to recorded steps. And typed values are potentially regulated data —
capture that a field was filled, mark it `value_redacted: true`, never the content.

Written to `evidence/{run_id}/human_actions.jsonl`. Also appended to the discovery trace
when the escalation happened during discovery, tagged `actor: "human"`.

Removed on `reacquire()`. The capture script is for the human's turn only.

---

## 5. Handback: resume or complete

The brief says "resume or complete" — two outcomes, deliberately. On `reacquire()`:

1. **Observe.**
2. **Check the capability-level success condition.** Met → the human finished the flow
   manually. Extract declared outputs, return `status="success"` with
   `human_intervention: true`. Do not re-execute anything.
3. **Else check the current step's checkpoint.** Met → continue from the next step.
4. **Else → re-escalate** (back to `AWAITING_HUMAN`), or fail if the re-escalation
   budget is exhausted.

### What is deliberately not done

The engine does **not** search forward through later steps' checkpoints to infer how far
the human got.

The reason is the failure mode: guessing wrong about the human's position could
re-execute an irreversible step that was already performed manually. The target app's
review-token guard would catch that specific case; a real bank system might not.
Refusing to infer is the conservative choice, and it is the right default when the cost
of a wrong guess is a duplicated transaction.

Document forward-search as the design you would build next, using
`human_actions.jsonl` as evidence rather than inference — that satisfies the brief's
"plus a clear design for the rest."

---

## 6. Evidence continuity

The brief requires preserving context and evidence across the handoff. Concretely:

- The evidence directory is per-run, not per-controller. Human actions land in the same
  directory as the automation's log.
- The main `replay.jsonl` / `discovery.jsonl` gets explicit boundary events:
  `{"event":"control_released","to":"human"}` and `{"event":"control_reacquired"}`, so
  the timeline reads continuously.
- Screenshots at both boundaries: at release (what the human is walking into) and at
  reacquire (what they left behind).
- The Playwright trace is stopped at release and a second segment started at reacquire.
  The human's turn is not traced — they did not consent to it, and the capture log is
  the record of that period.
- `result.json` carries a `handoffs` array: who, when, how long, how many actions.

---

## 7. The operator surface (mockable)

A minimal Flask app, separate from the target app, on its own port. This is the part the
brief explicitly permits keeping bare.

```
GET  /operator                        list of open intervention requests
GET  /operator/<run_id>               detail: capability, goal, step, reason,
                                      screenshot, and the CDP URL to open
POST /operator/<run_id>/claim         AWAITING_HUMAN -> HUMAN
POST /operator/<run_id>/release       HUMAN -> RETURNING, signals resume
GET  /operator/<run_id>/actions       what has been captured so far
```

Claiming is what transitions state — opening the page does not. That distinction is
what makes `AWAITING_HUMAN` versus `HUMAN` observable rather than notional.

The human clicks the CDP URL, which opens the live browser. They do the work. They click
Release. The engine, polling `control.json`, sees `RETURNING` and reacquires.

**Deliberately mocked:** no authentication, no operator identity beyond a name field, no
real-time co-browsing, no queueing across multiple runs. Document each as a cut.

---

## 8. What to build

**Build:** `SessionControl` state machine with both timeouts, controller enforcement in
`Surface.act()`, real `release()`/`reacquire()` over CDP, the human action capture
script and log, the handback verification sequence, boundary events and screenshots,
the operator Flask app, and escalation from discovery as well as replay.

**Mock:** operator auth, identity, queueing, co-browsing.

**Do not build:** forward-search on handback — document it.

---

## 9. Acceptance checks

- [ ] A replay whose recovery exhausts `max_attempts` transitions to `AWAITING_HUMAN`
      and the browser stays alive
- [ ] `control.json` reflects each state transition and is readable by the operator app
- [ ] `Surface.act()` raises if automation acts while controller is `HUMAN`
- [ ] The operator page lists the open request with capability, step, reason, screenshot
- [ ] Claiming transitions `AWAITING_HUMAN` → `HUMAN`; merely loading the page does not
- [ ] The CDP URL opens **the same browser session** — verified by the session cookie
      and current URL matching what the automation left
- [ ] Human clicks and typing are captured to `human_actions.jsonl` with role and
      accessible name
- [ ] A typed value is recorded as `value_redacted: true` with no content
- [ ] Release → engine reacquires, re-verifies, and completes the run successfully
- [ ] **Human completes the flow manually** → engine detects the success condition on
      handback, returns success with outputs, and re-executes nothing
- [ ] Human leaves the app in an unrelated state → engine re-escalates rather than
      blindly continuing
- [ ] Claim timeout → `ABANDONED`, run fails, intervention remains on record
- [ ] Hold timeout → session closed, failure recorded
- [ ] A discovery run emitting `stuck` escalates, a human acts, and those actions appear
      in the trace tagged `actor: "human"`
- [ ] `replay.jsonl` contains `control_released` and `control_reacquired` boundary events
- [ ] `result.json` carries a populated `handoffs` array
- [ ] Screenshots exist at both boundaries

The two that matter most: the CDP URL opening the *same* session (not a fresh one), and
the human-completes-the-flow case returning success without re-execution. Those are the
brief's "same live session" and "resume or complete" made concrete.
