# Surface abstraction spec

The seam between *how we perceive and act on an application* and *what the recorded
flow says*. This is the single most load-bearing abstraction in the system: it is what
lets one artifact schema and one replay engine serve a modern web app, a legacy
frameset, and eventually a desktop app.

**The rule:** the agent loop and the replay engine import from `src/surface/` and never
from `playwright`. If either touches Playwright directly, the seam has leaked.

---

## 1. The protocol

```python
class Surface(Protocol):
    surface_type: str                      # "web" | "legacy_web" | "desktop"

    def open(self, entry_point: str, run_id: str) -> None: ...
    def close(self, keep_trace: bool) -> None: ...

    def observe(self) -> Observation: ...
    def resolve(self, bundle: LocatorBundle) -> Resolution: ...
    def act(self, action: Action) -> ActionResult: ...

    def capture_screenshot(self, mask: list[Rect] | None) -> bytes: ...

    # control transfer (used by escalation, step 7)
    def release(self) -> SessionHandle: ...
    def reacquire(self, handle: SessionHandle) -> None: ...
```

Six methods plus lifecycle. Anything more and the abstraction is leaking implementation
detail; anything less and escalation or evidence has nowhere to live.

`release`/`reacquire` are declared now even though escalation is step 7. They are the
reason the web implementation must launch the browser with a remote debugging port —
retrofitting that later would mean rewriting session setup.

---

## 2. `observe()` — perception

Returns a snapshot. **Pure: reads state, changes nothing.**

```python
@dataclass(frozen=True)
class Observation:
    nodes: list[UINode]
    page: PageSignature
    screenshot: bytes | None
    state_hash: str
    observed_at: datetime
```

### `UINode`

```python
@dataclass(frozen=True)
class UINode:
    ref: str                 # "n7" — opaque handle, valid for this observation only
    role: str                # "button", "textbox", "heading", "alert", "dialog"
    name: str                # accessible name
    value: str | None        # current contents, if any
    enabled: bool
    visible: bool
    frame_path: list[str]    # ["main"] or ["main", "search-frame"]
    bounds: Rect
    nearby_text: list[str]   # text in adjacent cells / siblings
    dom_hint: str | None     # id or short css, captured but ranked last
```

**`nearby_text` is not optional.** The target app renders the savings value as a table
cell adjacent to a cell reading "Savings", with no `for`/`aria` binding between them. If
adjacency is not captured at observe time, the compiler cannot build a `spatial` locator
and the extraction step has nothing to bind to. This field is what makes that strategy
possible.

**`ref` is per-observation.** It must not be recorded in an artifact. The compiler
translates a ref into a `LocatorBundle` using the node's properties. This is the
mechanism that stops the model from ever authoring a selector.

### `PageSignature`

```python
@dataclass(frozen=True)
class PageSignature:
    url: str
    title: str
    heading: str | None       # first h1/h2 text
    app_version: str | None   # parsed from the app chrome, e.g. "v4.2.1"
```

Each field serves something downstream:

| Field | Used by |
|---|---|
| `url` | `SESSION_EXPIRED` detector, navigation checkpoints |
| `heading` | step and success checkpoints |
| `app_version` | version-drift fingerprint (3.7) |

`app_version` costs one selector to capture and is a direct, concrete answer to "how do
you detect per-tenant/version drift?" An artifact recorded against v4.2.1 replaying
against a different version should be flagged, not silently attempted.

### `state_hash`

Stable hash over `(role, name, frame_path)` of the pruned node list. **Excludes
`value`** — otherwise typing a single character registers as progress.

Two uses:
- Discovery: identical hash on consecutive observations means no progress → stop rather
  than burn the step budget.
- Replay: tells you whether an action actually changed anything.

### Pruning

The target app's layout tables generate large numbers of meaningless nodes. Keep:

- interactables: button, link, textbox, checkbox, radio, combobox, menuitem
- `heading`
- `alert`, `alertdialog`, `dialog`, `status`
- text-bearing leaves with non-empty content

Drop: presentational containers, empty cells, decorative images, anything
`visible=False` unless it is a dialog.

Pruning is a pure function over the raw tree, in its own module, unit tested. It is the
component most likely to need tuning once the agent loop is running.

### Frame traversal

The search form lives in an iframe. `observe()` must walk all frames and flatten them
into a single node list, each node carrying its `frame_path`. Without this the model
sees a page with no search field and dead-ends on step one.

`act()` and `resolve()` use `frame_path` to route back into the correct frame.

### Serialization for the model

Kept in a separate function from observation building, so prompt format can be tuned
without touching perception.

```
[n1] heading "Member Search"
[n2] textbox "Member ID" (empty) {frame: search-frame}
[n3] button "Search" {frame: search-frame}
[n4] link "Sign Out"
[n5] text "Internal use only."
```

Compact, and the model can only cite refs that exist.

---

## 3. `resolve()` — targeting

Takes a `LocatorBundle` from an artifact, returns what matched.

```python
@dataclass
class Resolution:
    node: UINode | None
    strategy_index: int | None    # which strategy matched
    candidates_found: int         # >1 means ambiguous
    status: Literal["resolved", "not_found", "ambiguous"]
```

### Strategy order

Tried in the order declared in the bundle. Recommended ranking:

| Rank | Kind | Basis |
|---|---|---|
| 0 | `a11y` | role + accessible name |
| 1 | `label` | visible label text → associated control |
| 2 | `spatial` | anchor text + direction (right/below) |
| 3 | `dom` | id / css |

### `strategy_index` is the drift signal

Record which rank matched, every time, in the structured log. A step that recorded at
rank 0 and now resolves at rank 2 has drifted. This is drift detection for free — no
separate mechanism, no scheduled diffing.

### Ambiguity is not success

If a strategy matches more than one node, do **not** take the first. Return
`status="ambiguous"` and fall through to the next strategy. Silently picking the first
match is how replay quietly does the wrong thing, which in this domain is worse than
failing.

---

## 4. `act()` — action

```python
@dataclass
class Action:
    kind: Literal["navigate","click","type","select","read","wait_for","assert"]
    target: LocatorBundle | None     # None for navigate
    value: str | None
    url: str | None
    wait: WaitSpec
```

```python
@dataclass
class ActionResult:
    ok: bool
    resolution: Resolution | None
    error_code: str | None      # NOT_FOUND, AMBIGUOUS, TIMEOUT, NOT_INTERACTABLE
    duration_ms: int
    observation_after: Observation
```

Two points:

**`act()` always returns a fresh observation.** Callers need post-action state for
checkpoints and detectors, and making it part of the result means neither the agent nor
the replay engine has to remember to re-observe.

**`act()` does not interpret failure.** It reports `error_code` mechanically. Deciding
whether a `NOT_FOUND` is a business outcome, a recoverable condition, or a hard failure
is the replay engine's job, driven by the artifact's declared detectors. The surface
knows nothing about business meaning.

### Waiting

```python
@dataclass
class WaitSpec:
    strategy: Literal["load", "settle", "condition"]
    timeout_ms: int
    condition: LocatorBundle | None
```

Default `settle` — wait for network idle plus a short quiet period. Never
`sleep(n)`; fixed sleeps are the main source of flaky replay.

---

## 5. Evidence

Placed at run lifecycle, **not inside `observe()`**. If `observe()` captured or mutated,
it would stop being pure and the recovery detectors could no longer be tested against a
faithful snapshot.

**Trace** — session-scoped. Started in `open()`, stopped in `close()`. Written to
`evidence/{run_id}/trace.zip` only when `keep_trace=True`. Retain on failure, discard
on clean success; traces are megabytes each and would bloat the repo.

**Screenshots** — event-driven, not per-step:

| Trigger | Requirement |
|---|---|
| Hard failure | 3.5 richer signal on failure |
| Checkpoint mismatch | shows expected vs observed |
| Escalation raised | 3.6 intervention request carries current state |
| Final state on success | proof the capability landed where it claimed |

Typically 1–2 images per run.

### The redaction tension — state it, don't paper over it

3.4 says never persist raw sensitive data. 3.5 asks for screenshots and traces. A
screenshot of the account summary contains a member's balance in pixels; a trace
contains the full DOM.

Resolution:

- **Screenshots:** `capture_screenshot(mask=...)` accepts rects. The caller passes the
  `bounds` of nodes bound to `sensitive` outputs, and those regions are blanked before
  the image is written.
- **Traces:** a DOM snapshot cannot be meaningfully redacted. Traces are therefore
  policy-gated — off by default, enabled explicitly for development — rather than
  sanitized.

Document this as a **limit** of the guardrail model in REPORT §6. An acknowledged
unresolved tension reads better than a claim of having solved it.

---

## 6. What to build now

**Build:** `Surface` protocol, `WebSurface` (Playwright sync, accessibility tree, frame
flattening, pruning, four resolve strategies, evidence hooks), the model serializer, and
the dataclasses above.

**Declare but stub:** `release()` / `reacquire()` — raise `NotImplementedError` with a
comment pointing at step 7. But **do** launch the browser with a remote debugging port
now, since that is the part that would be expensive to retrofit.

**Do not build:** `DesktopSurface`, `CoordinateSurface`. Note in the REPORT that the
interface has room for them: a coordinate-driven surface (Citrix, remote desktop) would
implement the same six methods with visual-template targets rather than a11y nodes.

---

## 7. Acceptance checks

Against the running target app on port 5001, driven from a script — no LLM involved yet.

- [ ] `observe()` on the search page returns the member ID textbox and the search
      button, both with `frame_path` including the iframe
- [ ] Pruned node count is under ~40 on the summary page (layout tables excluded)
- [ ] `PageSignature.app_version` parses as `"v4.2.1"`
- [ ] `state_hash` is identical across two consecutive `observe()` calls with no action
      between, and differs after a navigation
- [ ] `state_hash` does **not** change when a character is typed into a field
- [ ] `resolve()` finds the search button by `a11y` at `strategy_index=0`
- [ ] `resolve()` finds the savings value via `spatial` (anchor "Savings", direction
      right) when `a11y` and `label` both miss
- [ ] `resolve()` returns `status="ambiguous"` rather than guessing when a bundle
      matches multiple nodes
- [ ] A full scripted sequence — navigate, type 10001, click Search — reaches the
      summary page and reads 4,832.10
- [ ] The same sequence against port 5002 (riverbend) fails to resolve on the changed
      label, and reports `not_found` rather than silently doing something else
- [ ] `capture_screenshot(mask=[...])` writes an image with the masked region blanked
- [ ] Nothing under `src/agent/` or `src/replay/` imports `playwright`

The riverbend check is the important one. It should **fail** at this stage — that
failure is what the tenant override mechanism will later resolve, and seeing it fail
cleanly now proves the resolver is honest rather than accidentally permissive.
