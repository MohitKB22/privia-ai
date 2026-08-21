# Architecture

## The organising principle

Every design decision in PRIVIA follows from one rule:

> The language model proposes. A deterministic runtime disposes.

A model is a probabilistic component. Wiring one directly to a filesystem, a
mailbox and a shell means the security properties of the product are the
security properties of a sampled distribution, which is to say, none. So the
model's only output is a structured `ToolCall`, and a separate, boring,
fully-tested runtime decides whether it happens.

That is why the codebase looks the way it does: the interesting code is in
`packages/security` and `packages/tool-runtime`, not in the prompts.

---

## Layers

```
┌──────────────────────────────────────────────────────────────┐
│ Desktop client            React + TypeScript + Tauri         │
│                           12 screens, command palette        │
└──────────────────────────────┬───────────────────────────────┘
                               │  HTTP/JSON + SSE, loopback only
┌──────────────────────────────▼───────────────────────────────┐
│ API                       FastAPI                            │
│                           request id · size limit · auth     │
│                           rate limit · one error envelope    │
└──────────────────────────────┬───────────────────────────────┘
┌──────────────────────────────▼───────────────────────────────┐
│ Agent                     deterministic state graph          │
│                           INPUT → CLASSIFY → PLAN →          │
│                           POLICY_CHECK → TOOL_SELECTION →    │
│                           EXECUTION → VERIFY → RESPOND       │
└───────┬──────────────────────┬───────────────────┬───────────┘
        │                      │                   │
┌───────▼────────┐  ┌──────────▼─────────┐  ┌──────▼──────────┐
│ LLM router     │  │ Tool runtime       │  │ Memory          │
│ local first    │  │ schema→policy→     │  │ short · long ·  │
│ cloud opt-in   │  │ confirm→timeout→   │  │ semantic        │
│ offline planner│  │ retry→execute      │  │                 │
└───────┬────────┘  └──────────┬─────────┘  └──────┬──────────┘
        │                      │                   │
┌───────▼──────────────────────▼───────────────────▼───────────┐
│ Security       paths · commands · URLs · injection · policy  │
│                secrets · redaction · limits · audit          │
└──────────────────────────────┬───────────────────────────────┘
┌──────────────────────────────▼───────────────────────────────┐
│ Providers      files · notes · calendar · email · browser ·  │
│                terminal   (adapters, no vendor SDKs)         │
└──────────────────────────────┬───────────────────────────────┘
┌──────────────────────────────▼───────────────────────────────┐
│ Storage        SQLite · migrations · repositories            │
└──────────────────────────────────────────────────────────────┘
```

Dependencies point downward only. `packages/security` imports nothing from
`tool-runtime` or `agent-core`, which is what makes it independently testable and
independently trustworthy.

---

## Packages

| Package | Responsibility | Depends on |
|---|---|---|
| `shared-types` | Every cross-boundary model, defined once | pydantic |
| `security` | Every "may this happen?" decision | shared-types |
| `storage` | SQLite engine, migrations, repositories | shared-types, sqlalchemy |
| `observability` | Structured logging with redaction, metrics | shared-types, security |
| `memory` | Layered memory and recall | storage, embeddings, security |
| `tool-runtime` | Registry, middleware chain, execution | security, storage, integrations |
| `agent-core` | The state graph | tool-runtime, llm, memory |
| `services/llm` | Provider interface and implementations | shared-types |
| `services/embeddings` | Local and Ollama embedders | shared-types |
| `services/speech` | VAD, STT, TTS | shared-types |
| `services/integrations` | Provider adapters | security, storage |
| `apps/api` | HTTP surface and the container | everything |
| `apps/desktop` | The client | the API |

---

## The agent graph

Every request produces one `AgentRun`, persisted whole. It carries the request
id, session id, intent, plan, permission decisions, tool calls, tool results,
verification checks, per-phase timings and the final response. That record is
what the Activity screen renders, and it is why "what did it just do?" is always
answerable.

**INPUT** normalises the text, scans it for escalation phrasing, and assembles
context: recent turns, pinned memories, and anything semantically recalled.

**CLASSIFY** runs the rule engine first, then asks the model. The model's intent
label wins if it is available; the *entities* are merged, with the rule engine's
kept, because deterministic date and path parsing beats model guessing. With no
model, the rule engine's answer stands alone.

**PLAN** produces a minimal sequence of steps. A step may reference an earlier
result with `${0.files.0.path}`, which is how "find the report and summarise it"
becomes two chained calls. A model that invents a tool name has that step
dropped rather than failing the run.

**TOOL_SELECTION** converts steps into `ToolCall`s, attaching the *tool's*
declared risk and confirmation requirement — never the model's opinion of them.

**EXECUTION** resolves references against prior results, then hands each call to
the runtime. A `ConfirmationRequiredError` pauses the whole run and returns the
preview; nothing further executes.

**VERIFY** is mechanical, not "ask the model if it did well". It checks that
every planned tool ran, that none failed silently, that no side effect happened
without a matching confirmation, that untrusted content was isolated, and that
the reply does not claim an action that did not happen. Failed checks are
appended to the answer rather than hidden.

**RESPOND** generates the reply. With a model, tool results plus quarantined
untrusted blocks go into the prompt. Without one, the composer renders results
from templates.

---

## The middleware chain

Ordering is deliberate:

1. **observability** — outermost, so failures anywhere are still timed and audited.
2. **rate limit** — a runaway loop stops before any work happens.
3. **validation** — invalid arguments never reach the permission engine.
4. **policy** — capabilities checked against the *resolved* resources.
5. **confirmation** — high-impact calls stop and return a preview.
6. **output limit** — a huge file cannot blow up the client.
7. **timeout** — a hard wall-clock budget.
8. **retry** — transient failures only; permission, validation and destructive
   operations never retry.

---

## Trust boundaries

```
   TRUSTED                    │  SEMI-TRUSTED      │  UNTRUSTED
   ───────────────────────────┼────────────────────┼──────────────────────
   PRIVIA's own policy        │  What the user     │  Web pages
   The security package       │  typed or said     │  Email bodies
   The tool registry          │                    │  File contents
   Migrations                 │  (trusted to       │  Command output
                              │   express intent,  │  Model output
                              │   not to grant     │  (structurally
                              │   itself rights)   │   validated, never
                              │                    │   executed as text)
```

Untrusted content crosses into a prompt only inside a `wrap_untrusted` envelope,
and it can never cross into execution at all: only a validated `ToolCall`
reaches the runtime.

---

## Storage

SQLite in WAL mode with foreign keys enforced, accessed through SQLAlchemy Core
so every statement is visible SQL. Migrations are numbered `.sql` files applied
once inside a transaction and recorded with their SHA-256; editing an applied
migration is detected and refused.

Repositories are the only place that writes SQL. Nothing else in the codebase
contains a query.

---

## Extending it

**A new tool.** Subclass `Tool`, declare an `Args` model, scopes, risk level and
timeout, implement `execute`, and add it to `ALL_TOOLS`. If it can change
anything outside PRIVIA, set `requires_confirmation` and implement
`confirmation()`. A test in `tests/unit/test_misc_units.py` enforces that
high-risk tools confirm.

**A new provider.** Implement the relevant interface in
`services/integrations/base.py`, including a `health_check` that never raises,
and wire it in `registry.build_providers`. Nothing upstream changes.

**A new model backend.** Implement `LLMProvider` (`generate`, `stream`,
`structured_output`, `health_check`) and add it to `build_local_provider` or
`build_cloud_provider`.

**A new allowlisted command.** Add a `CommandRule`. Prefer denying flags over
allowing programs, and set `always_confirm` for anything that changes state.
