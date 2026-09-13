# Discovery agent & compiler spec

The only part of the system where an LLM runs. It discovers how to accomplish a goal
against a live surface, then compiles the successful run into a capability artifact that
the replay engine can execute without a model.

Two distinct components. Keep them separate — the brief requires the artifact be
"decoupled from the raw model transcript," and a compiler that is merely a trace
serializer does not satisfy that.

```
goal ──▶ agent loop ──▶ trace ──▶ probe pass ──▶ compiler ──▶ draft artifact
```

---

## 1. The LLM seam

```python
class LLMClient(Protocol):
    def decide(
        self,
        goal: str,
        observation_text: str,
        history: list[HistoryEntry],
    ) -> AgentAction: ...
```

Default implementation: Gemini via `google-genai`, using function calling. An Anthropic
implementation is a drop-in alternative. Provider selected by `LLM_PROVIDER` env var.

Provider-agnostic because the decision loop only needs constrained function calling —
nothing provider-specific. This is worth stating in the REPORT: the model is the least
load-bearing choice in the system, which is the point of compiling it out.

---

## 2. Action vocabulary

The model may emit **only** these. It never writes selectors, never writes code, never
emits coordinates.

| Tool | Arguments | Notes |
|---|---|---|
| `navigate` | `url` | Must be within the allowlist |
| `click` | `ref` | |
| `type` | `ref`, `value`, `parameter?` | See §3 |
| `select` | `ref`, `value`, `parameter?` | |
| `read` | `ref`, `output`, `type` | See §4 |
| `wait_for` | `ref?`, `reason` | |
| `done` | `summary` | Model believes the goal is met |
| `stuck` | `reason` | Model cannot proceed — triggers escalation |

`ref` values come from the current observation only. A ref the model invents, or one
from a previous observation, is rejected without acting — and logged. This is the
mechanism that makes it structurally impossible for the model to author a locator.

Note there is no `assert` tool. Checkpoints are inferred by the compiler (§6.3), not
authored by the model.

---

## 3. Parameter declaration

The compiler must know which typed values were *supplied to this run* versus which are
part of the flow. The model declares it, because the model knows why it typed something:

```
type(ref="n2", value="10001", parameter="member_id")   ->  {{member_id}}
type(ref="n5", value="SAV")                            ->  literal "SAV"
```

**Do not infer parameters by matching values against the goal text.** A goal like "open
a type 2 sub-account for member 10001" would parameterize any coincidental `2` elsewhere
in the flow — producing an artifact that silently writes a wrong value into a form field
on a member's account. Silent wrong values are the failure mode to design against here.

Explicit declaration is also auditable: a reviewer sees `{{member_id}}` against a
declared input and can check it. And it fails loudly — a missed declaration leaves a
hardcoded value, obvious in review and broken on the second invocation.

---

## 4. Output declaration

Symmetric with parameters. When the model reads a value it believes answers the goal:

```
read(ref="n15", output="savings_balance", type="money")
```

The compiler binds the output to that step and selects a transform from the fixed
registry based on the declared type (`money` → `parse_currency`).

---

## 5. The agent loop

```
open surface at entry point
loop:
    observation = surface.observe()
    if no_progress(observation): stop NO_PROGRESS
    action = llm.decide(goal, serialize(observation), history)
    if action is done:  verify, stop
    if action is stuck: raise InterventionRequest, stop
    decision = policy_gate.check(action, ctx)   # same gate as replay
    if denied: record, tell the model it was denied, continue
    result = surface.act(action)
    append to trace
```

### Stopping conditions

| Condition | Trigger |
|---|---|
| `SUCCESS` | Model emits `done` **and** the final observation is non-trivial |
| `MAX_STEPS` | Default 15. Hard cap on cost |
| `TIMEOUT` | Wall clock, default 180s |
| `NO_PROGRESS` | Same `state_hash` twice consecutively with an action between |
| `STUCK` | Model emits `stuck` → intervention request |
| `POLICY` | Repeated denials on the same action |

`NO_PROGRESS` uses the `state_hash` that deliberately excludes field values — so typing
does not register as progress, but a navigation does.

### The policy gate applies here too

Discovery actions pass through the **same** `PolicyGate` as replay. Invariant #3 is not
"replay is gated" — it is one choke point for the whole system. A denied action is
reported back to the model as a tool result so it can try something else, rather than
silently failing.

### `done` is not taken on trust

If the model says `done`, verify the final observation is a plausible end state (has a
heading, is not the entry point, is not an error page). A model declaring success on the
login screen must not produce an artifact.

---

## 6. The compiler

Trace in, draft artifact out. This is where the judgment lives.

### 6.1 Prune

Drop from the recorded flow:
- actions whose `ActionResult.ok` was false
- actions rejected for an invalid `ref`
- actions denied by policy
- **backtracks**: if `state_hash` returns to a previously-seen value, the actions
  between the first occurrence and the return are a loop with no net effect — prune them

**Never prune an action whose `risk` is `risky`.** A risky action may have had a side
effect even if the visible state returned to a prior value. If a risky action falls
inside a detected loop, keep it and flag the artifact for review. Pruning something
irreversible because the screen looked the same afterwards is exactly the wrong instinct
in this domain.

### 6.2 Build locator bundles

For each surviving action, translate the `UINode` it acted on into a ranked
`LocatorBundle`. Include only strategies the node actually supports:

| Rank | Emit when |
|---|---|
| `a11y` | `name` is non-empty → `{role, name}` |
| `label` | a label association exists |
| `spatial` | `nearby_text` non-empty → `{anchor_text, direction}` derived from `bounds` |
| `dom` | `dom_hint` present |

Also write the human-readable `target.description` — this is what makes the artifact
reviewable and what a tenant override patches.

### 6.3 Infer checkpoints

Compare the observation before and after each action:

| Action | Inferred checkpoint |
|---|---|
| `navigate` | `url_matches` on the resulting URL pattern |
| `type` | `value_equals` on the target |
| `click` causing navigation | `element_present` on the new page's heading |
| `click` revealing content | `element_present` on the most significant new node |

Mark inferred checkpoints in the artifact so a reviewer knows they were derived rather
than declared. An inferred checkpoint that is too strict is a source of false failures,
and a reviewer needs to be able to see which ones to scrutinize.

### 6.4 Canonicalize routes

Concrete paths become patterns: `/members/10001` → `/members/:member_id` where the
segment corresponds to a declared parameter. This is a stretch goal the brief lists, and
it falls out naturally here since parameters are already declared.

### 6.5 Declare the capability envelope

- `capability.id` — derived from the goal, namespaced (`member.lookup_savings_balance`)
- `capability.approval_state` — **always `draft`**. Never auto-approve.
- `provenance` — model, goal, run_id, `trace_ref` pointing at the real trace file,
  `recorded_against` from `PageSignature.app_version`
- `success.checkpoint` — from the final observation
- `inputs` / `outputs` — from declared parameters and reads

---

## 7. The probe pass

Discovery only walks the happy path. It never sees "no member found," so it cannot
discover the error taxonomy. The brief does not say where declared outcomes come from —
this is a decision to make and justify.

**Approach: probe what is empirically discoverable, leave the rest to human review.**

After a successful run, re-execute the compiled steps once with a deliberately invalid
value for one declared parameter (generated to violate nothing but the data — e.g. a
well-formed ID that does not exist). Observe where the flow diverges, and capture the
distinguishing node at the divergence point — typically an `alert` region.

Emit a candidate outcome:

```json
{
  "code": "MEMBER_NOT_FOUND",
  "kind": "business",
  "detect": { "kind": "text_matches", "pattern": "No member found" },
  "check_after": ["step_003"],
  "returns": { "found": false },
  "terminal": true,
  "origin": "probed"
}
```

Add `origin` to the outcome schema: `"probed"` | `"declared"` | `"reviewed"`. This is an
auditability field — a reviewer must be able to tell a detector grounded in an observed
page from one a human asserted.

### Bounds on probing

- One probe run per discovery. Not a search.
- **Input-driven outcomes only.** A probe can discover not-found by supplying a
  nonexistent ID. It cannot discover a session timeout without injecting one, and
  injecting failures into a live bank system to learn its error pages is not acceptable.
- **Never probe a flow containing a `risky` step.** Do not exercise a write path with
  bad data to see what happens.
- Recoveries are never probed. They are `origin: "declared"`, added in review.

### Why not let the model speculate

Asking the model "what errors might this flow produce?" yields plausible, unverified
detectors. An artifact containing guesses about error pages, presented as discovered,
would be a brittle assumption embedded in regulated automation. The honest position is
that the happy path and one probed outcome are verified; everything else is a review
responsibility, gated by `approval_state`.

---

## 8. Evidence

`evidence/{run_id}/`:

| File | Contents |
|---|---|
| `trace.jsonl` | Full transcript: observations, model actions, results. Redacted |
| `discovery.jsonl` | Structured event log, same format as replay |
| `probe.jsonl` | The probe run's events |
| `artifact.draft.json` | The compiled artifact |
| `step_NN.png` | Screenshots at each step (discovery only — useful for review) |
| `trace.zip` | Playwright trace, policy-gated |

The trace is the one place the raw model transcript lives. The artifact references it by
path and never embeds it. Sensitive parameter values are redacted in the trace.

---

## 9. What to build now

**Build:** `LLMClient` protocol + Gemini implementation, the tool schema, the agent
loop, the trace format, the compiler (prune, bundles, checkpoints, canonicalize,
envelope), the probe pass, evidence writing, and a `discover` CLI command.

**Schema addition:** `origin` on outcomes.

**Do not build:** the real policy gate (step 6 — keep using the existing seam), the
operator takeover (step 7).

**Delete afterwards:** `artifacts/member.lookup_savings_balance.v0.json`. It was
scaffolding so replay could be debugged without a model. The shipped artifact must come
from a real run.

---

## 10. Acceptance checks

The discovery run must be genuine. This is the one thing the brief says is not optional.

- [ ] `discover --goal "Look up member 10001 and read their savings balance" --target
      http://127.0.0.1:5001/members/search` completes and emits a draft artifact
- [ ] The run reached the account summary and read 4,832.10
- [ ] `evidence/{run_id}/trace.jsonl` exists and shows the real observe→decide→act
      sequence with model reasoning
- [ ] The compiled artifact has `member_id` as a declared typed input, and
      `step.value` is `{{member_id}}` — not the literal `10001`
- [ ] `savings_balance` is a declared output with type `money` bound to the read step
- [ ] `approval_state` is `draft`
- [ ] `provenance.trace_ref` points at a file that exists;
      `recorded_against` is `v4.2.1`
- [ ] Every step's target has at least two ranked strategies and a non-empty
      `description`
- [ ] **The compiled artifact replays successfully** via the step 4 engine with
      `member_id=10001`, returning 4,832.10
- [ ] **The compiled artifact replays with `member_id=99999`** and returns
      `MEMBER_NOT_FOUND` as a business outcome — using the probed detector
- [ ] The probe pass produced that outcome with `origin: "probed"`
- [ ] A run with `--max-steps 2` stops with `MAX_STEPS` and emits no artifact
- [ ] An invalid `ref` from the model is rejected without acting, and logged
- [ ] A run whose goal targets a disallowed domain is denied by the policy gate
- [ ] No sensitive parameter value appears in `trace.jsonl`
- [ ] The hand-written v0 artifact has been deleted

The second-to-last group is the end-to-end thread the brief asks for: a goal, an
LLM-driven run that completes it, a saved capability artifact, and a deterministic replay
of that same artifact — including one that hits an exceptional state.
