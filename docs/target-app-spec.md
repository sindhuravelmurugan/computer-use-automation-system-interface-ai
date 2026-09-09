# Target app spec — "Meridian" member servicing console

A deliberately legacy-styled web app standing in for a credit union back-office system.
It exists to be automated. Everything about it is designed so the automation system's
interesting problems can actually be demonstrated.

Fully fictional data. No real names, no real PII, no external services.

---

## Why it is built this way

| Property | Reason |
|---|---|
| Server-rendered, table layouts, no test IDs | Proves accessibility-first targeting earns its keep |
| Real `<label for=...>` on every input | Accessible names must exist — real bank apps do have labels. Removing them would make targeting *impossible*, not *hard* |
| Injectable failures via a control endpoint | Evidence runs must be reproducible, not dependent on luck |
| Two tenant variants from one codebase | Demonstrates cross-tenant artifact reuse without maintaining two apps |
| In-memory seed data | Resets on restart, so replays start from a known state |

---

## Run

```
TENANT=meridian  PORT=5001 flask run     # base tenant
TENANT=riverbend PORT=5002 flask run     # variant tenant
```

Single codebase. Tenant selected by env var, resolved through a config dict.

---

## Tenant configuration

| Key | meridian | riverbend |
|---|---|---|
| `brand_name` | Meridian Credit Union | Riverbend Federal CU |
| `member_id_label` | Member ID | Account Number |
| `search_button_label` | Search | Find |
| `summary_heading` | Account summary | Account overview |
| `not_found_text` | No member found | No matching account |
| `savings_row_label` | Savings | Savings balance |
| `accent_color` | navy | forest green |

These differences are the point. The base artifact is recorded against meridian; a
tenant override file re-targets the changed labels for riverbend. Same steps, same
flow, different names.

---

## Seed data

Eight members. Obviously fictional.

| ID | Name | Status | Savings | Notes |
|---|---|---|---|---|
| 10001 | Ada Thornbury | active | 4,832.10 | Primary happy-path member |
| 10002 | Bertie Mossgrove | active | 118.45 | Low balance |
| 10003 | Cyrus Pendlewick | active | 92,004.00 | High balance, comma formatting |
| 10004 | Delia Ashcombe | restricted | — | Triggers permission denied |
| 10005 | Emory Vance | active | 0.00 | Zero balance edge case |
| 10006 | Fenwick Bramble | closed | 0.00 | Closed account business outcome |
| 10007 | Greta Linnfield | active | 1,250.75 | Has existing sub-accounts |
| 10008 | Horace Quill | active | 7,410.20 | Spare |

Any ID not in this table produces the not-found result. Use `99999` in demos.

---

## Routes

### Auth (mock)

```
GET  /login              Login form. Any username, password "demo".
POST /login              Sets a session cookie.
GET  /logout
```

Sessions carry a TTL. Expiry redirects to `/login` with `?expired=1`.
No real credentials anywhere in the repo.

### Flow A — read flow (the primary capability)

```
GET  /members/search     Search screen. The form is inside an <iframe>.
POST /members/search     Redirects to detail, or renders the not-found result.
GET  /members/<id>       Account summary. Savings balance lives here.
```

The search form being in an iframe is deliberate — the surface layer must handle frame
traversal, which is exactly the legacy-web reality the brief describes.

### Flow B — write flow (the risky-action path)

```
GET  /members/<id>/subaccount/new     Multi-field form
POST /members/<id>/subaccount/review  Review screen — nothing committed yet
POST /members/<id>/subaccount/confirm Commits. Irreversible.
GET  /members/<id>/subaccount/<sid>   Confirmation screen
```

Three-stage on purpose: form → review → commit. The commit step is classified `risky`
by the policy gate and must not execute unattended without approval. Without this flow,
risk classification is theoretical.

### Test control (not part of the simulated product)

```
POST /_test/config       Set failure flags for the current session
POST /_test/reset        Clear all flags, reseed data
GET  /_test/config       Current flag state
```

Namespaced under `/_test/` and excluded from the automation allowlist, so the agent can
never reach it. Only the test harness calls it.

---

## Failure modes

Each maps to something the artifact schema declares. All are deterministic once set.

### 1. Not found — business outcome

Search an unseeded ID. Renders, in a `role="alert"` region:

> No member found for that ID.

The page must return HTTP 200. A not-found *result* is not an HTTP error, and treating
it as one would collapse the business/failure distinction the whole design rests on.

### 2. Permission denied — hard failure

Member 10004 is `restricted`. Requesting the detail page renders a denial page with
heading "Permission denied" and no balance. HTTP 403.

### 3. Session expired — recoverable

```
POST /_test/config  {"expire_session": true}
```
Invalidates the session immediately. The next request 302s to `/login?expired=1`.
The replay engine should detect the login URL, recover, and resume.

### 4. Disclosure modal — recoverable

```
POST /_test/config  {"modal_on_next": 2}
```
The next 2 page renders include a modal `<div role="dialog" aria-label="Disclosure">`
with an "Acknowledge" button, overlaying content. Dismissing it reveals the page.
Represents the intermittent interstitials real systems throw.

### 5. Slow load — recoverable

```
POST /_test/config  {"latency_ms": 4000}
```
Server-side delay on the next response. Exercises wait strategy and timeout handling.

### 6. Application error — hard failure

```
POST /_test/config  {"error_on_next": true}
```
Next request returns a 500 error page. The unrecoverable case.

---

## Markup requirements

The hostility must be realistic, not gratuitous.

**Do:**
- Server-rendered Jinja templates, full page loads, no client-side framework
- Nested `<table>` elements for page layout, not just tabular data
- Auto-generated-looking IDs: `ctl00_ContentMain_txtMemberId`, `ctl00_btnSearch`
- Class names that convey nothing: `.f1`, `.c2`, `.pnl`
- The search form inside an `<iframe src="/members/search-frame">`
- Inline styles mixed with a small stylesheet
- Some values in table cells with no semantic association to their label

**Do not:**
- Omit `<label for=...>` — every input keeps a properly associated label
- Omit `role="alert"` on the not-found region or `role="dialog"` on the modal
- Randomize IDs per page load — hostile, but *stable*, which is the real environment
- Include any real or realistic PII

The test: a screen reader user could operate this app. It is ugly, not inaccessible.

---

## Acceptance checks

Before moving on, all of these must hold:

- [ ] Both tenants run simultaneously on 5001 and 5002
- [ ] Searching 10001 reaches a summary page showing 4,832.10
- [ ] Searching 99999 renders the not-found alert with HTTP 200
- [ ] Member 10004 renders permission denied with HTTP 403
- [ ] Each `/_test/config` flag produces its failure deterministically
- [ ] `/_test/reset` restores a clean state
- [ ] The sub-account flow requires all three stages; nothing commits before confirm
- [ ] No element in the app carries a `data-testid`
- [ ] Every input has an associated `<label for=...>`
- [ ] Page source contains no real personal data
