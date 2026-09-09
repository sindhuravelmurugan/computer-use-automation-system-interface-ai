# Capability Artifact Schema v1

The typed, versioned contract emitted by a discovery run and consumed by the replay
engine. This document is the source of truth for the Pydantic models.

---

## Design principles

1. **The artifact is a contract, not a script.** It declares what it needs, what it
   returns, and what conditions it knows how to interpret. A caller can decide whether
   to invoke it without reading the steps.
2. **Targeting is ranked, never singular.** Every element carries an ordered list of
   strategies from most stable to least. Which one matched is recorded at replay time
   and is the primary drift signal.
3. **The error taxonomy is data, not code.** Business outcomes and recoverable
   conditions are declared here. The engine matches detectors generically; anything
   unmatched is a hard failure by default.
4. **Everything is addressed by stable ID.** Steps, outcomes, and recoveries carry IDs
   that survive editing, so overrides and logs can reference them safely.
5. **Sensitivity is declared, not inferred.** Redaction is driven by flags on the
   schema so no module has to guess what is regulated data.

---

## Top-level structure

```
{
  "schema_version": "1.0",

  "capability": { ... },     // identity, versioning, approval
  "surface":    { ... },     // what kind of thing this runs against
  "provenance": { ... },     // how this artifact came to exist
  "inputs":     [ ... ],     // typed parameters the caller supplies
  "outputs":    [ ... ],     // typed values the caller gets back
  "steps":      [ ... ],     // the ordered flow
  "outcomes":   [ ... ],     // declared business results (not failures)
  "recoveries": [ ... ],     // declared recoverable conditions
  "success":    { ... }      // capability-level success condition
}
```

---

## 1. `capability` — identity and versioning

```json
{
  "id": "member.lookup_savings_balance",
  "version": "1.2.0",
  "name": "Look up member savings balance",
  "description": "Searches for a member by ID and reads their current savings balance from the account summary screen.",
  "approval_state": "approved",
  "base_capability_id": null,
  "tenant_id": null
}
```

| Field | Purpose |
|---|---|
| `id` | Namespaced, stable. The name an AI agent invokes. |
| `version` | Semver. Bumps on any change to steps, inputs, or outputs. |
| `description` | Written for a *calling agent* to decide relevance. Load-bearing, not decoration. |
| `approval_state` | `draft` \| `approved` \| `deprecated`. Unattended replay refuses `draft`. |
| `base_capability_id` | Set when this is a tenant specialization of a base artifact. |
| `tenant_id` | Null for base artifacts; set for specializations. |

The last two fields are the multi-tenant seam. Designed in now, resolved at load time
by merging a base artifact with a tenant overlay.

---

## 2. `surface` — the perception/action seam

```json
{
  "type": "web",
  "entry_point": "https://app.example-cu.test/members/search",
  "viewport": { "width": 1280, "height": 900 },
  "requires_session": true
}
```

`type` is the discriminator that lets `web`, `legacy_web`, and `desktop` artifacts
coexist without a schema change. The replay engine selects a `Surface` driver from
this field and never learns anything else about the underlying technology.

---

## 3. `provenance` — how this artifact exists

```json
{
  "recorded_at": "2026-09-09T14:22:10Z",
  "recorded_by": "discovery-agent",
  "model": "claude-sonnet-4-6",
  "goal": "Look up member 10001 and read their savings balance",
  "run_id": "run_a3f9c2",
  "trace_ref": "evidence/run_a3f9c2/trace.jsonl",
  "human_edited": false
}
```

Points at the raw transcript rather than embedding it — this is what keeps the artifact
decoupled from the model output. `human_edited` matters for audit: a reviewer needs to
know whether a person changed the flow after recording.

---

## 4. `inputs` — typed parameters

```json
[
  {
    "name": "member_id",
    "type": "string",
    "required": true,
    "sensitive": false,
    "description": "Institution member identifier",
    "example": "10001",
    "constraints": { "pattern": "^[0-9]{5}$" }
  }
]
```

Types: `string` `integer` `decimal` `money` `date` `boolean` `enum`.

`sensitive: true` means the value is never written to logs, evidence, or the artifact —
it is substituted at replay time and redacted everywhere else. Credentials are never
inputs at all; they come from the runtime session, not the caller.

`constraints` are validated *before* the browser opens. Cheap rejection of bad input
beats discovering it three steps in.

---

## 5. `outputs` — typed return values

```json
[
  {
    "name": "savings_balance",
    "type": "money",
    "required": true,
    "sensitive": true,
    "description": "Current available savings balance",
    "source": {
      "step_id": "step_005",
      "target": { "$ref": "steps.step_005.target" },
      "transform": "parse_currency"
    }
  }
]
```

Extraction is bound to a specific step's target so the engine knows *when* the value is
readable. `transform` is a named function from a fixed registry (`parse_currency`,
`trim`, `parse_date`, `raw`) — never arbitrary code in the artifact.

Note `sensitive: true` here. A balance is regulated data: it is returned to the caller
in memory but redacted in every log and evidence file.

---

## 6. `steps` — the flow

```json
{
  "id": "step_002",
  "action": "type",
  "target": {
    "description": "Member ID search field on the lookup screen",
    "strategies": [
      { "kind": "a11y",    "role": "textbox", "name": "Member ID" },
      { "kind": "label",   "label_text": "Member ID", "control": "input" },
      { "kind": "spatial", "anchor_text": "Member ID", "direction": "right" },
      { "kind": "dom",     "css": "#memberIdInput" }
    ]
  },
  "value": "{{member_id}}",
  "risk": "safe",
  "checkpoint": {
    "kind": "value_equals",
    "target": { "$ref": "self.target" },
    "expected": "{{member_id}}"
  },
  "wait": { "strategy": "settle", "timeout_ms": 5000 },
  "on_timeout": "fail",
  "notes": "Field has no test ID; accessible name comes from the adjacent label cell."
}
```

### Action vocabulary

Deliberately small. The discovery agent can only emit these, which is what keeps the
recorded flow replayable.

| Action | Meaning | Default risk |
|---|---|---|
| `navigate` | Go to a URL within the allowlist | safe |
| `click` | Activate a control | depends |
| `type` | Enter text into a field | safe |
| `select` | Choose from a dropdown | safe |
| `read` | Extract a value, no state change | safe |
| `wait_for` | Block until a condition holds | safe |
| `assert` | Verify without acting | safe |

### Targeting strategies, ordered by stability

| Kind | Basis | Why it ranks here |
|---|---|---|
| `a11y` | Role + accessible name | Survives markup rewrites; exists on desktop too |
| `label` | Visible label text → associated control | How a human finds it; stable across restyling |
| `spatial` | Position relative to anchor text | The fallback for table-layout legacy apps with no semantics |
| `dom` | CSS / XPath | Fastest and most brittle. Last resort, never first. |

The engine tries strategies in order and records the index that matched. A step that
starts resolving at index 2 when it recorded at index 0 is drifting — that signal feeds
the stability score without needing a separate drift detector.

### `risk`

`safe` — reversible, no state change. `risky` — mutates records, moves money, or is
otherwise irreversible. The policy gate handles the two classes differently; see the
policy config, not this schema.

### `checkpoint`

An assertion about state *after* the action. Kinds: `element_present`,
`element_absent`, `text_matches`, `value_equals`, `url_matches`. Without this, a
silently-failed click cascades into four steps of garbage before anything notices.

---

## 7. `outcomes` — declared business results

These are legitimate answers, not failures.

```json
[
  {
    "code": "MEMBER_NOT_FOUND",
    "kind": "business",
    "description": "No member exists with the supplied ID.",
    "detect": {
      "any_of": [
        { "kind": "text_matches", "pattern": "No member found" },
        { "kind": "element_present", "target": { "strategies": [
            { "kind": "a11y", "role": "alert", "name": "Search returned no results" }
        ]}}
      ]
    },
    "check_after": ["step_003"],
    "returns": { "found": false },
    "terminal": true
  }
]
```

`check_after` scopes when the detector runs, so a stray phrase elsewhere in the app
cannot trigger a false positive. `returns` is what the caller receives instead of the
declared outputs. `terminal: true` stops the run cleanly — this is a success path.

---

## 8. `recoveries` — declared recoverable conditions

```json
[
  {
    "code": "CONFIRMATION_INTERSTITIAL",
    "kind": "recoverable",
    "description": "Known 'acknowledge disclosure' modal that appears intermittently.",
    "detect": { "kind": "element_present", "target": { "strategies": [
      { "kind": "a11y", "role": "dialog", "name": "Disclosure" }
    ]}},
    "check_after": ["any"],
    "recovery": { "action": "click", "target": { "strategies": [
      { "kind": "a11y", "role": "button", "name": "Acknowledge" }
    ]}},
    "max_attempts": 2,
    "then": "retry_step"
  },
  {
    "code": "SESSION_EXPIRED",
    "kind": "recoverable",
    "description": "Session timed out and the app bounced to login.",
    "detect": { "kind": "url_matches", "pattern": ".*/login.*" },
    "check_after": ["any"],
    "recovery": { "action": "re_authenticate" },
    "max_attempts": 1,
    "then": "restart_from",
    "restart_step_id": "step_001"
  }
]
```

`then` is one of `retry_step`, `continue`, `restart_from`, `escalate`. Exhausting
`max_attempts` promotes the condition to a hard failure — recovery is bounded, never a
loop.

**Default deny:** any state matching no declared outcome and no declared recovery is a
hard failure. The engine never guesses.

---

## 9. `success` — capability-level completion

```json
{
  "checkpoint": {
    "kind": "element_present",
    "target": { "strategies": [
      { "kind": "a11y", "role": "heading", "name": "Account summary" }
    ]}
  },
  "require_all_outputs": true
}
```

Reaching the last step is not success. Success is asserting the expected end state and
extracting every required output.

---

## The replay result contract

What the engine returns. The three-way split the brief asks for, made explicit:

```json
{
  "status": "success" | "business_outcome" | "failure",
  "capability_id": "member.lookup_savings_balance",
  "capability_version": "1.2.0",
  "run_id": "run_b7d1e4",

  "outputs":  { "savings_balance": "REDACTED" },
  "outcome":  { "code": "MEMBER_NOT_FOUND", "returns": { "found": false } },

  "error": {
    "code": "CHECKPOINT_FAILED",
    "step_id": "step_004",
    "expected": "heading 'Account summary' present",
    "observed": "heading 'Permission denied' present",
    "strategy_used": null,
    "evidence": "evidence/run_b7d1e4/step_004.png"
  },

  "recoveries_applied": [
    { "code": "CONFIRMATION_INTERSTITIAL", "step_id": "step_003", "attempts": 1 }
  ],

  "duration_ms": 4210,
  "steps_completed": 4
}
```

Exactly one of `outputs`, `outcome`, or `error` is populated. `recoveries_applied` is
always present — a run that succeeded only after two recoveries is materially different
from a clean one, and the caller should be able to see that.

---

## Tenant override format (companion file)

Not part of the artifact. Merged over a base artifact at load time.

```json
{
  "base_capability_id": "member.lookup_savings_balance",
  "base_version": "1.2.0",
  "tenant_id": "cu_riverbend",
  "surface": { "entry_point": "https://riverbend.example.test/member-search" },
  "step_overrides": {
    "step_002": {
      "target": { "strategies": [
        { "kind": "a11y", "role": "textbox", "name": "Account Number" }
      ]}
    }
  },
  "outcome_overrides": {
    "MEMBER_NOT_FOUND": {
      "detect": { "kind": "text_matches", "pattern": "No matching account" }
    }
  }
}
```

Overrides patch by ID and are additive — they cannot add or remove steps, only
re-target existing ones and adjust detectors. A tenant that needs different *steps*
needs its own artifact, and the merge refuses rather than silently producing a hybrid.
That constraint is what keeps "reuse across tenants" from quietly becoming "a fork per
tenant."
