# CLAUDE.md

Project context. Read this before doing anything in this repo.

## What this is

A computer-use automation system for legacy back-office applications that have no API.

An LLM discovers how to accomplish a goal by driving a real UI. The successful run is
compiled into a typed, versioned **capability artifact**. That artifact is then replayed
deterministically with **no LLM in the decision loop**. AI agents invoke capabilities by
name in production.

One line to keep in mind: *the model discovers, the artifact is the capability,
deterministic replay is how it runs in production.*

## Non-negotiable invariants

Violating any of these is a design bug, not a style preference.

1. **No LLM call anywhere on the replay path.** Not for locating, not for recovery, not
   for classification. If replay needs a model, the design is wrong.
2. **The agent loop and the replay engine never touch Playwright directly.** Both go
   through the `Surface` interface. If either imports Playwright, the seam has leaked.
3. **Every action from both paths passes through the policy gate.** One choke point, not
   two copies of the rules.
4. **The error taxonomy is data, not code.** Business outcomes and recoveries are
   declared in the artifact. The engine matches declared detectors generically.
5. **Unrecognized state is a hard failure.** Default deny. The engine never improvises.
6. **Nothing marked `sensitive` reaches disk.** Not logs, not evidence, not artifacts.
7. **Steps are addressed by stable ID**, never array index.

## Stack

- Python 3.11+, `uv` or venv
- Pydantic v2 for all schema models
- Playwright (sync API) for the web surface, driven via the accessibility tree
- LLM access behind a provider-agnostic `LLMClient` interface. Default: Gemini via `google-genai`, using function calling for constrained actions. An Anthropic implementation is a drop-in alternative.
- Flask + Jinja2 for the target app
- pytest
- Typer for the CLI
- Artifacts serialized as canonical JSON on disk

## Layout

```
/target_app/          Flask app being automated (the stand-in for a bank system)
/src/
  schema/             Pydantic models: artifact, result contract, overrides
  surface/            Surface protocol + web implementation (Playwright/a11y)
  agent/              LLM discovery loop, action tools, prompts
  compiler/           trace -> artifact
  replay/             deterministic executor, detectors, result builder
  policy/             allowlist, risk classification, redaction
  escalation/         intervention requests, session control state machine
  evidence/           structured logging, screenshots
  cli.py
/artifacts/           saved capability artifacts
/evidence/            run logs, screenshots, example artifacts (a deliverable)
/tests/
CLAUDE.md
README.md
REPORT.md
```

## Conventions

- Type hints everywhere. Pydantic models over dicts for anything crossing a module edge.
- No bare `except`. Failures carry a code, the step ID, what was expected, what was seen.
- Redaction happens at the logging boundary, not at call sites.
- Tests where they carry weight: schema validation, detector matching, override merge,
  redaction. Not on Playwright glue.
- Small, readable functions. This code will be read by an interviewer and defended out
  loud.

## Build order

Follow this. Do not skip ahead.

1. Target app
2. Schema models
3. Surface abstraction + web implementation
4. Replay engine — tested against a hand-written artifact
5. Discovery agent + compiler
6. Policy gate
7. Escalation + mock operator UI
8. Evidence, README, REPORT

Step 4 before step 5 is deliberate: replay must be debuggable without the LLM in play.

## Out of scope

Do not build: queues, workers, containers, a database, a real operator console, auth
beyond a mock login, desktop surface support. The brief explicitly does not reward
scaling infrastructure. Design the seams; do not build the plumbing.
