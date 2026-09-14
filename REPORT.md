# REPORT

## 1. Architecture

The idea is simple. An LLM figures out a task in a UI once. I save that run as a typed artifact. After that, the artifact replays on its own, with no model involved. The artifact is the line between the two phases.

I split the code into eight packages:

- `target_app/` — the Flask app I automate, a fake credit union console
- `src/schema/` — the Pydantic models
- `src/surface/` — perception and action
- `src/agent/` — the discovery loop
- `src/compiler/` — turns a trace into an artifact
- `src/replay/` — runs artifacts, no LLM
- `src/policy/` — allowlist, risk, redaction
- `src/escalation/` — control state, session transfer, operator app

The most important decision is the `Surface` seam. Neither the agent nor the replay engine imports Playwright. They both go through one interface: `observe`, `resolve`, `act`, plus lifecycle and session release. I did this so one schema and one replay engine can drive a modern web app, a legacy frameset, and later a desktop app. I have a test that fails if anything under `src/agent/` or `src/replay/` imports Playwright, so it isn't just a rule I try to remember.

I also expose saved artifacts through the CLI as a catalog. `capabilities` lists each one with its typed inputs and outputs. `show` prints one so you can read it. That is the surface an AI agent would call. The invocation looks like `replay --capability <id> --input member_id=10001`, and the typed contract is checked before the browser even opens.

The compiler is its own phase, not a serializer. I learned why the hard way. My agent logs in during bootstrap, and that step ate the first navigation. So the artifact came out with no navigate step. It looked complete, and it was unreplayable — a cold replay died on the first resolve before any recovery could run. The trace records what the agent did. The artifact has to record what a replay needs. Those are different whenever the agent had setup state the replay won't have.

I built replay before I built the agent, and tested it against an artifact I wrote by hand. If I had done the agent first, I would have been debugging the model and the executor at the same time, with no way to tell which one was broken. I deleted the hand-written artifact once real discovery worked.

Everything runs in one process, from the CLI, with JSON on disk. The brief says scaling infrastructure isn't rewarded, so I built the seams instead: `ArtifactStore` is an interface, the policy gate is one call, evidence is per run. A queue and a database would have been work that proved nothing.

The trade-off: one process means a discovery run blocks. That's fine here. A real deployment would need a queue.

## 2. Artifact schema

I treat an artifact as a contract, not a list of steps. You can read the inputs, outputs, and description and decide whether to call it without reading the steps at all.

**Ranked targeting.** Every element has an ordered list of ways to find it: accessible role and name first, then visible label text, then position relative to nearby text, then CSS. I picked that order on purpose. The accessible name is what a human operator actually reads on screen, and it survives a re-skin. Label text is next because this app has real `<label for=...>` markup. Position is next. CSS is fastest and the most brittle, so it goes last — I store it as `dom_hint` and never trust it first.

The position strategy exists because of a real case. The savings balance resolves at `strategy_index=2`. "Savings" and "4,832.10" are two cells next to each other in a nested layout table with nothing linking them. No name and no label reaches that value. Position does.

**I record the matched strategy index on every resolve.** That is my drift detection. If a step recorded at rank 0 and now resolves at rank 2, it still works, but something changed. I get that for free — no extra monitoring.

**The error taxonomy lives in the artifact, not in the engine.** This is the schema choice I'd defend hardest. Business outcomes and recoveries are declared with detectors, and the engine matches them generically. The other option was hardcoding "if you see 'not found'" in the executor. That works for one app and falls apart at twenty, and you can't override it per tenant. That matters here: one tenant says "No member found" and the other says "No matching account".

**The model declares parameters. I don't infer them.** It emits `type(ref="n2", value="10001", parameter="member_id")`. The alternative was matching the typed value against the goal text, and I rejected it because it's unsafe, not just sloppy. A goal like "open a type 2 sub-account for member 10001" would parameterize that stray `2` and quietly write a wrong value into a form on someone's account.

The compiler also turns concrete routes into patterns: `/members/10001` becomes `/members/:member_id`. I get this for free from declared parameters — once the compiler knows `member_id` is an input, it knows which segment to generalize. The policy allowlist matches on those same patterns, so one route entry covers every member instead of a list of paths.

A few smaller things: `sensitive` flags on inputs and outputs drive redaction, so no module has to guess what's regulated. `approval_state` is always `draft` after discovery. `origin` on an outcome says whether its detector was probed, declared, or reviewed by a human. `$ref` resolves at validation time, so a bad reference fails before the browser opens.

## 3. Determinism & error handling

A replay ends in one of three ways: success with outputs, a known business outcome, or a failure. A Pydantic validator makes sure exactly one of the three is filled in, so I can't break the contract by accident.

The evidence shows the split. `evidence/replay-not-found/` has zero error events and ends on `MEMBER_NOT_FOUND`. The app returns HTTP 200 for that, so nothing in the transport says "error" — only the declared detector catches it. `evidence/replay-hard-failure/` hard-fails on an injected server error that matches no detector. Same engine, same code path. The only difference is what the artifact declared.

**Order of checks after every action:** observe, run recovery detectors, run outcome detectors, judge the checkpoint, then default deny.

Detectors go before the checkpoint for a reason. If the session expires, the checkpoint fails. If I judged the checkpoint first, I'd hard-fail with "expected Account summary, saw Login" and never reach the `SESSION_EXPIRED` recovery sitting right there in the artifact. Detectors tell me what state I'm in. The checkpoint tells me whether the step worked. The first can explain why the second failed.

Recoveries go before outcomes because a modal sitting over a not-found page has to be dismissed before I can read what the page says.

I run detectors on every step, not only when something fails. A modal can appear over a page whose heading still passes the checkpoint. If I skip detection there, the modal breaks the *next* step, and the error message points at the wrong place.

**Default deny.** Anything that matches no detector and fails its checkpoint is a hard failure with the step ID, expected, observed, and a screenshot. The engine never improvises and never asks a model.

**What makes it repeatable.** No LLM import anywhere under `src/replay/`, enforced by a test. No `sleep()` anywhere — every wait is condition-based. If a match is ambiguous, I refuse instead of taking the first hit. I hash page state before and after each action to catch "the click did nothing". Recovery is bounded by an attempt ledger. Three replays in a row gave identical event sequences and identical `strategy_index` values.

**One open question I want to be honest about.** Discovery only walks the happy path, so it can't discover the error taxonomy by watching. My answer is one bounded probe with a deliberately bad input, which grounds the not-found detector in a page I actually saw. I can't probe a session timeout without injecting failures into a live system, so those detectors get added in review and gated by approval state. I did not let the model guess detector patterns. Unverified detectors in regulated automation are exactly the brittle assumption I'm trying to avoid.

## 4. Heterogeneity & multi-tenant

The seam is `Surface`. The artifact never names a technology. It names roles, accessible names, label text, and nearby anchor text.

A legacy frameset is the same implementation plus frame flattening, which I already do — the search form is inside an iframe. A desktop app would be a new `Surface` over UIA or AT-SPI with the same six methods. The schema and the replay engine don't change. `surface.type` picks the driver.

One honest note: this version of Playwright dropped the old accessibility API, so I compute role and name in the page and flatten in Python. That's me rebuilding an accessibility tree, not reading the real one. A desktop UIA tree would be better input than what I have now, not worse.

Where there's no tree at all — Citrix, remote desktop — a coordinate-driven surface can implement the same six methods with visual templates instead of nodes. I designed for that and didn't build it. Coordinates are deliberately not expressible as a recorded action anywhere else, because a human can't review a coordinate and it breaks on a ten-pixel shift.

**Multi-tenant reuse** is a base artifact plus a per-tenant override file, merged at load. Overrides can patch the entry point, step targets, and detector patterns, keyed by step ID rather than position.

The interesting part is the limit I put on it: an override can't add or remove steps. Without that, "reuse across tenants" quietly turns into "a fork per tenant with extra ceremony." If a tenant really needs different steps, the merge refuses and I find out loudly instead of running a silent hybrid.

**Drift detection, two signals, both almost free.** The recorded `strategy_index` tells me a target is now matching by a weaker strategy than when it was recorded. And I parse `app_version` from the page chrome (`v4.2.1`) and compare it to `provenance.recorded_against`. A mismatch is a warning, not a stop — a stable UI is the premise of the whole system — but it's the first thing a human should look at when something reliable starts failing.

What I didn't do: the merge is implemented and unit-tested, and the base artifact fails against `riverbend` with `NOT_FOUND` instead of matching the wrong thing. I kept that as an acceptance check instead of making it pass, because a resolver loose enough to match both "Member ID" and "Account Number" would be matching too loosely. I didn't write the override file and show it end to end. That's the first thing I'd do next.

## 5. Escalation & handoff

Three triggers, one path. The agent says `stuck` or stops making progress. A replay runs out of recovery attempts. The policy gate returns `requires_approval` on a risky step. All three go through the same mechanism. It works from discovery too — if a human unblocks a discovery run, their actions go into the trace tagged `actor: "human"` and become part of the recorded flow.

**Control is a state, not a boolean.** `AUTOMATION`, `AWAITING_HUMAN`, `HUMAN`, `RETURNING`, `ABANDONED`, saved to `control.json`. The brief's phrase "who is (or should be) in control" is what pushed me here. An intervention can be raised and never claimed. A human can take over and then walk away. A boolean can't express either of those. So I have a claim timeout and a hold timeout.

Only whoever holds control may act. `Surface.act()` raises if automation tries to act while control is ceded. I enforce it rather than trusting myself to follow it — same reasoning as the single policy gate.

**The handoff is the same live session, literally.** `release()` stops sending commands and returns a CDP endpoint. The browser context stays alive; I close nothing. The operator opens that endpoint and drives the same browser, same cookies, same page. `reacquire()` reconnects over it. I verify it's really the same session by comparing the session cookie, not by matching a URL, because a matching URL proves nothing.

**Recording the human.** I inject a capture script at release that records clicks and input as role and accessible name — the same vocabulary everything else uses, so human actions can be compared to recorded steps. For typed values I record that a field was filled with `value_redacted: true`, never the content, because that content is potentially regulated.

**Handback has three outcomes: complete, resume, or re-escalate.** I check the capability's success condition first, because the human may have finished the whole flow — in that case I return success and re-execute nothing. Then I check the current step's checkpoint and resume. Otherwise I escalate again.

One thing I chose not to do: I don't search forward through later steps to guess how far the human got. If I guessed wrong I could repeat an irreversible step. My app's one-time review token would catch that specific case, but a real bank system might not. Forward search driven by the captured action log as evidence, instead of by guessing, is what I'd build next.

What's mocked: the operator UI has no auth, no identity beyond a name, no queueing across runs, no live co-browsing. The brief allows that. The handoff mechanism and the control model are real.

## 6. Safety

**One choke point.** Every action, from the agent and from the replay engine, goes through `PolicyGate.check()`. Not "replay is gated" — one set of rules for the whole system.

I got this wrong twice, both times in recovery code. First, recoveries called `act()` directly. Later, `_apply_recovery_action` called the gate and then threw the decision away. Both are fixed with regression tests. It's worth saying why it happened twice: recovery code is written in a "handle it and keep going" frame, which is the opposite of "ask permission first." That's a pattern, and it's exactly why the choke point has to be enforced instead of remembered.

**Deny by default.** I list domains, `:param` route patterns, and action kinds explicitly. Anything else is denied, not warned about. `/_test/*` isn't on the list, so the agent can't reach my failure-injection endpoints even though they're on the same host.

**Risk comes from two places.** On replay, the artifact's declared `risk` wins, because a human reviewed it. During discovery nothing is declared, so I classify with heuristics: route patterns and risky control names. Risky plus unattended is denied. Risky plus attended needs approval and escalates.

I made the classification deliberately conservative, because the two mistakes aren't equal. A false positive stops a run and a human unblocks it. A false negative commits an irreversible action on a member's account. Over-blocking is recoverable. Over-permitting isn't.

I report denials back to the model instead of failing silently, so it can try another route rather than retrying until it stalls.

Unattended replay refuses a `draft` artifact, and discovery never auto-approves. I didn't build reliability scoring to promote `draft` to `approved` — that's next work.

**Redaction** runs in three layers: declarations first (`sensitive` on inputs and outputs), then config key names, then patterns as a net. Declarations have to come first, because a balance like `4,832.10` matches no sensitive pattern and is regulated data anyway. I apply redaction at the serialization boundary inside `EvidenceWriter`, not at call sites, because I hit a bug where a failed checkpoint's `expected` and `observed` fields quoted a sensitive value that lived outside the outputs dict. Error paths leak because they're written assuming more detail is more helpful.

That's also why I split in-memory from on-disk. The `ReplayResult` I hand back to the caller has real values. Everything written to disk is redacted. The capability returns real data and persists none of it.

### Limits

- The gate sees actions, not consequences. It can allow a click on an allowed route that the app treats as irreversible. It has no idea what the app means.
- Discovery-time risk classification is name matching, not understanding. A risky control with a boring label gets through.
- Pattern redaction catches shapes, not meaning.
- Traces can't really be redacted. A DOM snapshot of a member record *is* the record. So I access-gate traces and keep them off by default instead of pretending I sanitized them. This is a real tension between 3.4 and 3.5 in the brief and I haven't resolved it.
- The allowlist is deployment config derived from nothing. A misconfigured allowlist is a genuine risk and nothing in my system detects one.

## 7. Cuts

**Left out on purpose.**

- **Desktop and coordinate surfaces.** I designed the `Surface` interface for them and built neither. Claiming both would have meant two shallow drivers instead of one that works.
- **The multi-tenant override end to end.** Two tenants exist, the merge is implemented and unit-tested, and the base artifact correctly fails against the second. I just didn't demo it live.
- **Operator console auth, identity, queueing, co-browsing.** The brief allows mocking this, and none of it tests the handoff itself.
- **Forward search on handback.** Deliberate, for the irreversible-step reason above.
- **Queues, workers, a database, containers.** The brief says this isn't rewarded.
- **Code generation from artifacts.** I could emit a page object or a test file from the schema, but I couldn't see where it fits their business — the artifact already *is* the executable form.
- **Assisted LLM recovery on replay failure.** Rejected, not just descoped. A recovery leash that isn't airtight puts the model back in the production path, which is the opposite of the point of compiling it out. I'd only build it if the bound were enforced by the policy gate rather than by a prompt.

**What I'd build next, in order.**

1. The override applied live against `riverbend`, with the drift signal shown.
2. Forward search on handback, using the captured human action log as evidence instead of guessing.
3. A confidence score that promotes `draft` to `approved` based on multi-run replay stability.
4. A desktop `Surface` over UIA, to find out whether my schema really is surface-agnostic.

I time-boxed this. I went deep on the schema, replay, and safety because the brief says those carry the most weight, and I kept everything else thin but real instead of broad and sketched.
