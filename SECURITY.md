# Security

## Reporting a vulnerability

Email `security@privia.app` with details and, ideally, a reproduction. Please do
not open a public issue for an unpatched vulnerability. We aim to acknowledge
within 72 hours.

---

## Threat model

PRIVIA runs on a machine the user controls, with access to that user's files,
mailbox and shell. That makes the interesting attacks *indirect*: an attacker who
cannot reach your machine can still reach the assistant, through the content the
assistant reads.

### Assets

1. The user's files, especially credentials and keys.
2. The user's mailbox and calendar (both readable data and a sending capability).
3. Shell execution on the user's machine.
4. Configured API keys and passwords.
5. The audit log, which is the record of what happened.

### Adversaries we defend against

| Adversary | Capability | Primary defence |
|---|---|---|
| **Hostile web page** | Controls text the browser tool fetches | Untrusted-content envelope, injection scoring, tools-not-text execution |
| **Hostile email** | Controls a message body the user asks about | Same, plus email bodies are never auto-read |
| **Hostile file** | Controls a document the user asks about | Same, plus path and sensitivity guards |
| **A confused or misaligned model** | Emits any tool call it likes | Schema validation, capability check, confirmation gate, allowlists |
| **A local process** | Can reach loopback | Loopback binding, token required off-loopback, no dangerous GET endpoints |
| **A supply-chain dependency** | Runs in-process | Small dependency surface, no vendor SDKs, pinned ranges, CI audit |

### Explicitly out of scope

- An attacker who already has code execution as the user. PRIVIA's data is
  readable by that user by design.
- Physical access to an unlocked machine.
- A malicious operating system or hypervisor.
- Denial of service against your own machine.

---

## Controls

### The core invariant

The language model cannot perform an action. It emits a
`ToolCall{tool_name, arguments}`. `ToolRuntime.execute` is the only path to a
side effect, and it always runs, in this order:

```
observability → rate limit → schema validation → capability check
              → confirmation gate → output limit → timeout → retry → execute
```

Removing any middleware breaks tests in `tests/security/`.

### Capability model

A tool declares the scopes it needs. A grant is a scope plus an optional
resource narrowing (paths, domains, program names). The engine returns
ALLOW / PROMPT / DENY and **defaults to PROMPT** — an ungranted scope never
falls through to allow. An explicit denial is sticky until the user reverses it.

### Confirmation binding

High-impact actions return `428 CONFIRMATION_REQUIRED` with a full preview. The
confirmation id is:

```
sha256(session_id ‖ tool_name ‖ canonical_json(arguments))
```

Consequences, both intentional:

- Approving in one turn and executing in the next works, because the re-planned
  call hashes identically.
- Changing *any* argument changes the hash, so an approval can never be replayed
  against different content. The classic "get a benign draft approved, then swap
  the recipient" attack fails.

Freshness is enforced separately by the stored confirmation record: single use,
session scoped, and expiring after 15 minutes.

### Filesystem

- Paths are `resolve()`d **before** the allowlist check, so `../` and symlinks
  cannot escape a root.
- Sensitive locations are denied even inside an allowed root: `.ssh`, `.gnupg`,
  `.aws`, `.env`, `id_rsa`, `*.pem`, `*.key`, and others.
- `/etc`, `/proc`, `/sys`, `/dev`, `/System`, `Library/Keychains` are permanently
  blocked regardless of configuration.
- Only regular files are read: FIFOs, devices and sockets are refused, because a
  read on one can hang forever.
- Deletion is one file at a time, never a folder, always with the absolute path
  shown, and never retried.

### Terminal

- No shell. Ever. Commands are parsed with `shlex` into an argv list and executed
  with `shell=False`.
- Shell metacharacters and substitution (`;`, `|`, `&&`, `$()`, backticks, `>`)
  are refused at parse time.
- A declarative allowlist of ~47 programs, with per-program subcommand and flag
  rules. Bundled short flags are expanded, so `-rf`, `-fr` and `-rvf` are all
  caught.
- ~40 programs are hard-denied with a specific reason: `sudo`, `curl`, `ssh`,
  `docker`, `systemctl`, package managers, and every shell.
- Arguments that resolve outside the workspace roots are refused.
- The environment is rebuilt from a small allowlist with every credential-shaped
  variable stripped.
- The child runs in its own process group, with stdin closed, a wall-clock
  timeout, and a capped output. Timeout escalates SIGTERM → SIGKILL to the group.

### Network

- `http`/`https` only, no embedded credentials, port allowlist.
- The hostname is **resolved**, and every resulting address is classified.
  Loopback, private, link-local, multicast, reserved, unspecified and cloud
  metadata addresses are refused. IPv4-in-IPv6 is unwrapped first, so
  `::ffff:127.0.0.1` is caught.
- Redirects are followed manually and re-validated at every hop, capped at five.
- Responses are size-capped, and only text content types are read.

### Prompt injection

Three trust tiers are kept lexically distinct in every prompt: SYSTEM (policy,
never derived from data), USER (what the person said), UNTRUSTED (everything a
tool returned from outside).

Untrusted text is scanned for injection markers, stripped of zero-width and
bidirectional control characters and Unicode Tags smuggling, and wrapped in an
envelope that states the rule before *and* after the content.

The scoring is defence in depth, not the defence. The actual defence is that
text cannot become a tool call: only a validated `ToolCall` reaches the runtime,
and the runtime checks capabilities regardless of what any text said.

Note the deliberate asymmetry: `scan_user_input` downgrades escalation patterns,
because the *owner* of the assistant is allowed to say "turn off cloud AI" or
"stop asking me to confirm". Injection defences that make the product unusable
for its owner are not security, they are theatre.

### Secrets

- Stored in the OS keychain when available, otherwise in an AES-GCM encrypted
  file with a scrypt-derived key and `0600` permissions.
- Never written to the database, never logged, never in a data export, never in
  a child process's environment.
- All logging and audit output passes through a redactor that catches both
  sensitive key names and credential-shaped values at any nesting depth.
- Only a fixed set of credential keys can be written through the API.

### API

- Binds to `127.0.0.1`. Binding elsewhere **without** a token is a start-up
  failure, not a warning.
- Bearer token comparison uses `hmac.compare_digest`.
- Body size limit before buffering; sliding-window rate limit; strict CORS.
- One error envelope everywhere. Stack traces never reach a client.
- Security headers on every response, including a `default-src 'none'` CSP.

---

## Testing

`tests/security/` is an adversarial suite, not a unit test file. It asserts
outcomes for path traversal, symlink escape, shell injection, privilege
escalation, SSRF and DNS rebinding, prompt injection, malformed and oversized
tool calls, permission escalation, and approval replay.

```bash
make test-security
```

CI additionally runs `bandit`, `pip-audit`, `npm audit`, `gitleaks`, and a check
that no credential-shaped file is tracked in git.

---

## Cryptography

PRIVIA rolls no cryptography. The encrypted secret store uses AES-GCM from
`cryptography` with a key derived by `hashlib.scrypt` (N=2^14, r=8, p=1) from a
machine-local 48-byte key file. Nonces are 96-bit and randomly generated per
write.
