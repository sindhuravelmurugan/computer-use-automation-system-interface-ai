# Policy gate spec

Replaces `AllowAllGate` with real enforcement. The interface and every call site already
exist — this step fills in the rules.

Three responsibilities:

1. **Allowlist** — what the system is permitted to touch
2. **Risk classification** — treating irreversible actions differently from reversible ones
3. **Redaction** — one place that decides what never reaches disk

---

## 1. Contract

Already defined, unchanged:

```python
class PolicyGate(Protocol):
    def check(self, action: Action, ctx: ReplayContext) -> PolicyDecision: ...
```

```python
@dataclass(frozen=True)
class PolicyDecision:
    verdict: Literal["allow", "deny", "requires_approval"]
    rule: str | None        # which rule fired — needed for debuggable denials
    reason: str | None
```

`rule` matters. "Denied" is useless to debug; "denied by `allowlist.domains`" is
actionable.

---

## 2. Configuration

`config/policy.json`, loaded at startup. JSON for consistency with artifacts — one
serialization format in the repo.

```json
{
  "allowlist": {
    "domains": ["127.0.0.1:5001", "127.0.0.1:5002"],
    "routes": [
      "/login",
      "/members/search",
      "/members/search-frame",
      "/members/:member_id",
      "/members/:member_id/subaccount/new",
      "/members/:member_id/subaccount/review",
      "/members/:member_id/subaccount/confirm"
    ],
    "action_kinds": ["navigate", "click", "type", "select", "read", "wait_for", "assert"]
  },
  "risk": {
    "risky_route_patterns": ["/subaccount/confirm"],
    "risky_control_names": ["confirm", "submit", "delete", "transfer", "post", "commit"],
    "unattended_risky": "deny"
  },
  "redaction": {
    "always_redact_keys": ["password", "token", "ssn", "tax_id", "card_number"],
    "patterns": {
      "ssn": "\\b\\d{3}-\\d{2}-\\d{4}\\b",
      "card": "\\b(?:\\d[ -]*?){13,16}\\b"
    }
  }
}
```

### Deny by default

An action whose domain, route, or kind is not explicitly listed is **denied**. Not warned
about — denied. Anything else means a missed config entry becomes an allowed action.

Note `/_test/*` is absent from the allowlist, so the agent can never reach the failure
injection endpoints even though they exist on the same host. Only the test harness calls
them, directly.

### Route matching

Routes are patterns with `:param` segments, matched against the canonicalized path. This
reuses the canonicalization the compiler already does — `/members/10001` matches
`/members/:member_id`. Without pattern matching, an allowlist of concrete paths would be
unmaintainable across members.

---

## 3. Risk classification

Two sources, and the distinction matters.

**On replay: the artifact is authoritative.** Steps carry a declared `risk` field, set at
compile time and reviewable. The gate reads it. This is the trustworthy path — a human
approved that classification.

**During discovery: there is no declared risk yet**, so the gate classifies
conservatively from the action itself:

- target route matches a `risky_route_patterns` entry, or
- the control's accessible name contains a `risky_control_names` term, or
- the action is a `click` on a control whose name the classifier cannot read

Verdict for a risky action:

| Context | Verdict |
|---|---|
| `allow_risky=True` (explicit caller opt-in) | allow |
| `attended=True` | `requires_approval` |
| Unattended | `deny` per `unattended_risky` |

### Why conservative rather than accurate

Name-matching is a heuristic and will produce false positives — a button named
"Submit search" is harmless. The cost asymmetry justifies it: a false positive stops a
discovery run and a human unblocks it; a false negative commits an irreversible action
on a member account. Over-blocking is recoverable, over-permitting is not.

State this as a known limit in the REPORT rather than claiming the classifier is sound.

### Denial is reported to the model, not silent

During discovery, a denied action returns a tool result telling the model it was denied
and why. The model can then try another route. Silently failing would make it retry the
same thing until `NO_PROGRESS` fires — a confusing and expensive way to enforce policy.

---

## 4. Redaction

One module, applied at the **logging and serialization boundary**, never at call sites.
Scattered redaction is redaction with holes — bug 5 from the discovery session is the
evidence.

Three inputs, in priority order:

1. **Schema declarations** — any input or output marked `sensitive: true`. Authoritative,
   because it is declared rather than guessed.
2. **Config key names** — `always_redact_keys` catches field names like `password` even
   when nothing declared them.
3. **Patterns** — SSN and card-number shapes, as a net for values that appear without
   being declared or named.

**Declarations first, patterns last.** Pattern-only redaction is unreliable — a balance
like `4,832.10` matches no sensitive pattern but is regulated data. That is exactly why
the schema carries `sensitive` flags.

### Scope

| Destination | Treatment |
|---|---|
| `replay.jsonl` / `discovery.jsonl` / `trace.jsonl` | redacted |
| `result.json` on disk | redacted |
| `ReplayResult` returned in memory to the caller | **real values** — the caller asked for the balance |
| Screenshots | masked using node `bounds` |
| Playwright trace | cannot be meaningfully redacted → policy-gated, off by default |

The in-memory/on-disk split is the point: the capability returns real data to its
caller and persists none of it.

---

## 5. Limits — for REPORT §6

Write these down honestly. A guardrail model presented as complete is less credible than
one with stated boundaries.

**The gate sees actions, not consequences.** It can allow a click on an allowlisted route
that the app treats as irreversible. It has no model of application semantics. The
artifact's declared `risk` is the mitigation, and that depends on correct classification
at compile time plus human review.

**Risk classification during discovery is heuristic.** Accessible-name matching is not
semantic understanding.

**Pattern redaction catches shapes, not meaning.** An undeclared regulated value in an
unusual format passes through.

**The allowlist is per-deployment configuration**, not derived from anything. A
misconfigured allowlist is a real risk, and nothing in the system detects one.

**Traces are gated, not sanitized.** A DOM snapshot of a member record cannot be redacted
in any meaningful sense, so the control is access rather than transformation.

---

## 6. Acceptance checks

- [ ] An action targeting a domain outside the allowlist is denied, with `rule` naming
      `allowlist.domains`
- [ ] An action targeting `/_test/config` is denied — the agent cannot reach failure
      injection
- [ ] A route with a concrete ID (`/members/10001`) is allowed via the
      `:member_id` pattern
- [ ] An action kind not in `action_kinds` is denied
- [ ] Replay of the read-only capability passes the gate unchanged and still returns
      4,832.10
- [ ] A `risky` step with `allow_risky=False`, unattended → denied in pre-flight, browser
      never opened
- [ ] Same step with `attended=True` → `requires_approval`, not silent allow
- [ ] Same step with `allow_risky=True` → allowed
- [ ] During discovery, a denied action is reported back to the model as a tool result
      and the loop continues
- [ ] A `sensitive` input value appears nowhere in any `.jsonl` or `result.json`
- [ ] A `sensitive` output value is absent from disk but present in the in-memory
      `ReplayResult`
- [ ] A synthetic SSN-shaped string in a log payload is redacted by pattern
- [ ] Recovery actions pass through the gate (regression test for the bypass fixed in
      step 4)
- [ ] Every denial in the logs carries a `rule`
