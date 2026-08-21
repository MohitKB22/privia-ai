# Contributing

## Setup

```bash
./scripts/setup.sh
make install-dev
make check          # lint, types, all tests, security
```

## The rule everything else follows from

> The language model proposes. A deterministic runtime disposes.

Before writing code, check your change against it. If a change lets model output
reach a side effect without passing through `ToolRuntime.execute`, it will not be
merged, however useful it is.

## Standards

**Python.** Ruff and Black at 100 columns, type hints on public functions, mypy
clean. No bare `except`. Errors are `PriviaError` subclasses with a stable code.

**TypeScript.** Strict mode, ESLint clean, Prettier formatted, no `any` in new
code.

**Comments explain why, not what.** `# increment counter` is noise. A comment
explaining why a check happens *before* another one is worth its line.

**Error messages are for people.** "That path is outside the folders you have
allowed" beats "PermissionError: EACCES". Say what happened, and what to do.

## Adding a tool

1. Subclass `Tool` in `packages/tool-runtime/privia_tools/tools/`.
2. Declare an `Args` Pydantic model — it *is* the input schema. Bound every free
   text field.
3. Declare `scopes`, `risk_level`, `timeout_seconds`.
4. If it can change anything outside PRIVIA: set `requires_confirmation = True`,
   implement `confirmation()` showing the exact target, and set
   `retry_policy = RetryPolicy(max_attempts=1)`.
5. Implement `resources()` so the permission engine sees what will be touched.
6. Register it in `ALL_TOOLS`.
7. Write tests, including at least one adversarial case.

A test enforces that every high-risk tool confirms; you cannot forget.

## Adding a provider

Implement the interface in `services/integrations/base.py`. `health_check` must
**never raise** — return an `IntegrationInfo` describing the problem instead. Map
vendor errors onto `PriviaError` subclasses so nothing vendor-specific escapes.

## Tests

```bash
make test-unit           # fast, pure logic
make test-integration    # real database, real filesystem, real subprocesses
make test-security       # adversarial — treat failures here as release blockers
make test-e2e            # through the HTTP API
```

Tests must pass offline with no model installed. That is why the offline planner
is deterministic: a security test asserting on a sampled distribution proves
nothing.

New security-relevant code needs a test in `tests/security/` that demonstrates
the attack failing.

## Commits and pull requests

Conventional commits (`feat:`, `fix:`, `security:`, `docs:`, `refactor:`,
`test:`, `chore:`). A pull request should say what changed, why, and — if it
touches the security model — which threat it addresses and which test covers it.

## Reporting security issues

Do not open a public issue. See [SECURITY.md](SECURITY.md).
