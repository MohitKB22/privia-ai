# Troubleshooting

Start here:

```bash
make doctor          # configuration, database, models, integrations, permissions
./scripts/health_check.sh
./scripts/model_check.sh
```

---

## PRIVIA will not start

**"PRIVIA cannot start with the current configuration"**

Start-up validation refuses unsafe configurations rather than silently
downgrading. The message lists every problem. The common ones:

| Problem | Fix |
|---|---|
| `PRIVIA_HOST is '0.0.0.0' but PRIVIA_API_TOKEN is empty` | Bind to `127.0.0.1`, or set a token of 16+ characters |
| `CLOUD_PROCESSING_ENABLED is true but CLOUD_LLM_PROVIDER is not set` | Set the provider, or turn cloud off |
| `ALLOWED_DIRECTORIES entry must be absolute` | Use full paths |
| `EMAIL_PROVIDER=smtp requires: SMTP_HOST, ...` | Configure SMTP, or use `local` |

**"Migration was modified after it was applied"**

Migrations are immutable. Restore the original file and add a new migration, or
for a development database: `make db-reset`.

**Port 8756 is in use**

```bash
lsof -i :8756          # macOS/Linux
PRIVIA_PORT=8757 make dev-api
```

---

## The UI says the backend is not running

The client talks to `127.0.0.1:8756`. Check `./scripts/health_check.sh` first.
If the backend is up but the browser cannot reach it, you are probably running
the UI from a different origin than the CSP allows; use `make dev`, which sets
up the proxy correctly.

---

## Replies feel templated

You have no local model, so the offline planner is answering. Confirm:

```bash
curl -s localhost:8756/api/v1/status | jq .models.local
```

If `provider` is `offline-planner`, install one:

```bash
ollama pull llama3.1:8b
ollama serve
```

This is a capability difference, not a fault. Every tool still works.

**Ollama is running but PRIVIA says it is not:**

- Check the URL: `OLLAMA_BASE_URL` defaults to `http://127.0.0.1:11434`.
- Check the model is pulled: `ollama list`. The name must match
  `LOCAL_LLM_MODEL` exactly.
- In Docker, use `host.docker.internal`, not `localhost`.

---

## "PRIVIA needs your permission"

Two independent things are required, and this catches almost everyone:

1. **The folder must be allowed.** Privacy Center → allow a folder. This is the
   path guard.
2. **The capability must be granted.** Privacy Center → Permissions, or answer
   the prompt in the conversation. This is the policy engine.

Allowing a folder does not grant a capability, and granting a capability does not
allow a folder. Both layers apply.

**"The permission you granted does not cover ..."** means the scope is granted
but narrowed to different paths. Re-grant without narrowing, or add the path.

---

## "That path is outside the folders you have allowed"

The path guard resolves symlinks and `../` *before* checking, so:

- a symlink pointing outside an allowed root is refused,
- `.ssh`, `.gnupg`, `.aws`, `.env`, `id_rsa`, `*.pem`, `*.key` are refused even
  inside an allowed root,
- `/etc`, `/proc`, `/sys`, `/dev`, `/System` are permanently blocked.

These are deliberate and not configurable.

---

## "That command is not on the allowlist"

`terminal.inspect` explains any command without running it:

```bash
curl -s localhost:8756/api/v1/tools/execute -H 'content-type: application/json' \
  -d '{"tool_name":"terminal.inspect","arguments":{"command":"npm run build"}}' | jq .data
```

Common refusals:

| Message | Why |
|---|---|
| "shell substitution, which PRIVIA never evaluates" | `$()`, backticks, `&&`, `\|\|` |
| "contains shell metacharacters" | `;`, `\|`, `>`, `<`, `&` |
| "is not on the command allowlist" | Program not in the table |
| "PRIVIA never escalates privileges" | `sudo`, `su`, `doas` |
| "points outside the allowed workspace" | An argument resolves outside your roots |
| "Give the program name only" | An absolute program path could be a shim |

To allow something, add a `CommandRule` in
`packages/security/privia_security/commands.py`.

---

## Web pages will not load

| Message | Meaning |
|---|---|
| "resolves to a private address" | SSRF protection. The hostname resolved inward. |
| "Only http and https are supported" | Scheme allowlist |
| "not on the allowed port list" | Ports 80, 443, 8080, 8443 only |
| "not on your allowlist" | `BROWSER_ALLOWED_DOMAINS` is set |
| "Web search is unavailable" | No network. Everything else still works. |

Loopback and private addresses are blocked by design and cannot be configured
away, because that is exactly the control that stops an injected page from
making PRIVIA read your router's admin panel.

---

## Voice does not work

**"faster-whisper is not installed"**

```bash
pip install 'privia[speech]'
```

**"I did not hear anything"** — voice activity detection found no speech. Check
the input level; hold the button while speaking.

**"Only WAV audio is accepted"** — PRIVIA refuses formats it cannot decode
safely rather than guessing a sample rate and producing plausible nonsense.

**Microphone permission denied** — grant it in your OS settings. Typing keeps
working regardless.

---

## Email

Default is a **local mailbox**: drafts and "sent" messages go to `~/.privia/mail`
and nothing is transmitted. That is the point, not a bug.

For real sending, set `EMAIL_PROVIDER=smtp`, configure the host and username,
and store the password through the API (it goes to the keychain, not the
database):

```bash
curl -s localhost:8756/api/v1/integrations/secrets -H 'content-type: application/json' \
  -d '{"key":"smtp_password","value":"..."}'
```

**"The mail server rejected the credentials"** — the draft is kept, never lost.

---

## Memory

**"PRIVIA does not keep credentials in memory"** — working as intended. Use a
password manager.

**Recall misses obvious things** — the dependency-free embedder is lexical. It
finds exact tokens well and paraphrases poorly. For semantic recall:

```bash
ollama pull nomic-embed-text
# then set LOCAL_EMBEDDING_PROVIDER=ollama
curl -s -X POST localhost:8756/api/v1/memory/reindex
```

---

## Database

**"database is locked"** — something else has the file open. PRIVIA uses WAL mode
and short-lived connections; if you have a `sqlite3` session open, close it.

**Inspecting it:**

```bash
sqlite3 ~/.privia/privia.db '.tables'
sqlite3 ~/.privia/privia.db 'SELECT action, target FROM audit_events ORDER BY timestamp DESC LIMIT 20;'
```

**Starting over:**

```bash
# Export first if you want to keep anything.
curl -s localhost:8756/api/v1/privacy/export > ~/privia-backup.json
rm -rf ~/.privia && make migrate
```

---

## Building the desktop application

**"Rust is required"** — install from <https://rustup.rs>. Until then, `make dev`
runs the same UI in a browser.

**"Icons are missing"** — `npm run tauri icon path/to/privia-1024.png`. The
repository ships no binary assets on purpose.

**Linux build failures** — install the WebKit development packages listed in
`.github/workflows/release.yml`.

---

## Getting more detail

```bash
LOG_LEVEL=DEBUG make dev-api
tail -f ~/.privia/logs/privia.log | jq .
```

Every log line and every error carries a `request_id`. Correlate them:

```bash
grep req_01J... ~/.privia/logs/privia.log | jq .
curl -s "localhost:8756/api/v1/audit?limit=100" | jq '.events[] | select(.request_id=="req_01J...")'
```
