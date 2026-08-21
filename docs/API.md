# API reference

Base URL: `http://127.0.0.1:8756`. Interactive docs at `/docs`, machine-readable
schema at `/openapi.json`.

## Authentication

None on loopback: the operating system already restricts access to processes on
your machine, and requiring a token there would only push people to store one in
a file. Binding anywhere else requires `PRIVIA_API_TOKEN` and start-up **fails**
without it.

```http
Authorization: Bearer <PRIVIA_API_TOKEN>
```

`/health` and `/api/v1/status` are always reachable.

## Errors

Every failure uses one envelope. Stack traces never appear.

```json
{
  "error": {
    "code": "TOOL_PERMISSION_DENIED",
    "message": "PRIVIA needs your permission for: files:read",
    "request_id": "req_01J...",
    "details": { "missing_scopes": ["files:read"], "resources": ["/home/me/Documents"] }
  }
}
```

| Status | Codes |
|---|---|
| 400 | `BAD_REQUEST` |
| 401 | `UNAUTHORIZED` |
| 403 | `TOOL_PERMISSION_DENIED`, `PATH_NOT_ALLOWED`, `COMMAND_NOT_ALLOWED`, `SSRF_BLOCKED`, `CLOUD_DISABLED` |
| 404 | `NOT_FOUND`, `TOOL_NOT_FOUND` |
| 409 | `CONFLICT` |
| 413 | `PAYLOAD_TOO_LARGE` |
| 422 | `VALIDATION_ERROR`, `TOOL_INVALID_ARGUMENTS` |
| 428 | `CONFIRMATION_REQUIRED` |
| 429 | `RATE_LIMITED` |
| 500 | `INTERNAL_ERROR` |
| 503 | `LLM_UNAVAILABLE`, `STT_UNAVAILABLE`, `TTS_UNAVAILABLE`, `INTEGRATION_UNAVAILABLE` |
| 504 | `TOOL_TIMEOUT` |

Every response carries `x-request-id`; it appears in the local log and in the
error envelope, which is how you correlate the two.

---

## System

### `GET /health`

Liveness. Never touches the network.

```json
{ "status": "ok", "version": "1.0.0", "uptime_seconds": 42.1,
  "checks": { "database": "ok", "tools": 38, "allowed_directories": 1 } }
```

`status` is `degraded` when PRIVIA runs but something is limited, for example no
folders allowed yet.

### `GET /api/v1/status`

Everything the client needs for its status bar: models, speech, integrations,
privacy posture, database, warnings.

### `GET /api/v1/metrics`

In-process counters and timers. Local only; there is no exporter.

---

## Chat

### `POST /api/v1/chat`

```json
{
  "message": "Find the project report and summarise it",
  "session_id": "ses_01J...",
  "confirmation_id": null,
  "confirm": null,
  "speak": false,
  "prefer": null
}
```

Response:

```json
{
  "run_id": "run_01J...",
  "session_id": "ses_01J...",
  "response": "Summary of project_report.md: revenue grew 12 percent...",
  "status": "completed",
  "intent": "summarize",
  "processing_location": "local",
  "model_used": "ollama:llama3.1:8b",
  "tool_calls": [ ... ],
  "tool_results": [ ... ],
  "pending_confirmation": null,
  "accessed_resources": ["/home/me/Documents/project_report.md"],
  "permission_prompt": null,
  "duration_ms": 812
}
```

**The confirmation flow.** When an action has consequences, `status` is
`awaiting_confirmation` and `pending_confirmation` holds the preview:

```json
{
  "id": "cfm_7QK...",
  "tool_name": "email.send",
  "title": "Send this email?",
  "summary": "Send \"Q3 report\" to rahul@example.com.",
  "risk_level": "critical",
  "details": { "To": "rahul@example.com", "Subject": "Q3 report", "Body": "..." },
  "destructive": false
}
```

Resend the **same message** with the id and a decision:

```json
{ "message": "Send the email.", "session_id": "ses_...",
  "confirmation_id": "cfm_7QK...", "confirm": true }
```

The id is a hash of the session, the tool and the exact arguments, so it matches
only if nothing changed. Each id is single use and expires after 15 minutes.

**The permission flow.** When a scope is missing, the run completes, explains
itself, and returns `permission_prompt`. Grant it via `POST /api/v1/permissions`
and send the message again.

### `POST /api/v1/chat/stream`

Server-sent events: `start`, `status`, `tool`, `confirmation`, `token`, `done`,
and `error` if something goes wrong. The stream always closes cleanly.

---

## Tools

- `GET /api/v1/tools` — every registered tool with its schema, scopes, risk and
  whether it confirms.
- `GET /api/v1/tools/{name}` — one tool.
- `POST /api/v1/tools/execute` — run a tool directly. **The same runtime, the
  same permission checks and the same confirmation gate apply.** This exists so
  the UI can act on a button press, not to bypass policy.

```json
{ "tool_name": "files.search", "arguments": { "query": "report" } }
```

When confirmation is required this returns `428` with the preview in
`error.details.confirmation`. Retry with `confirmation_id`; the session is taken
from the stored record, so an approval cannot be moved between sessions.

---

## Permissions

- `GET /api/v1/permissions` — every scope, its state, and any resource narrowing.
- `POST /api/v1/permissions` — `{"scope": "files:read", "grant": true, "resources": ["/home/me/Documents"]}`
- `POST /api/v1/permissions/directories` — allow a folder. Refuses `/`, `/etc`,
  `/proc` and friends. Allowing a folder grants no scope on its own.
- `DELETE /api/v1/permissions/directories` — stop allowing a folder.
- `POST /api/v1/permissions/reset` — revoke everything.

Scopes: `files:read|write|delete`, `notes:read|write`,
`calendar:read|write|delete`, `email:read|draft|send`, `browser:read`,
`terminal:exec`, `memory:read|write`, `cloud:inference`.

---

## Memory

- `GET /api/v1/memory?query=` — list or hybrid search.
- `GET /api/v1/memory/stats`
- `POST /api/v1/memory` — refuses credentials with `422`.
- `DELETE /api/v1/memory/{id}`
- `POST /api/v1/memory/clear?keep_pinned=true`
- `POST /api/v1/memory/reindex`

## Notes, files, sessions

- `GET|POST /api/v1/notes`, `GET|PUT|DELETE /api/v1/notes/{id}`
- `GET /api/v1/files/roots|list|search|read|metadata` — read-only, and subject to
  exactly the same path guard the tools use.
- `GET|POST /api/v1/sessions`, `GET|DELETE /api/v1/sessions/{id}`

## Audit

- `GET /api/v1/audit` — filter by `action`, `run_id`, `session_id`, `minutes`.
- `GET /api/v1/audit/runs` and `/runs/{id}` — a run with its calls and events.
- `GET /api/v1/audit/stream` — live SSE feed.
- `DELETE /api/v1/audit` — clearing the log is itself the first new entry.

## Privacy

- `GET /api/v1/privacy` — full posture.
- `POST /api/v1/privacy` — toggles. Enabling cloud without a configured provider
  and key is a `400`.
- `GET /api/v1/privacy/export` — everything stored about you, as JSON.
  Credentials are excluded by design.
- `POST /api/v1/privacy/purge?conversations=&memories=&audit_log=` — each
  category is opt-in, so nothing goes by accident.

## Integrations and secrets

- `GET /api/v1/integrations` — health of every adapter.
- `GET /api/v1/integrations/secrets` — which credentials exist and where.
  **Never their values.**
- `POST /api/v1/integrations/secrets` — only a fixed set of keys is accepted.
- `DELETE /api/v1/integrations/secrets/{key}`

## Voice

- `GET /api/v1/voice/status` — availability and the recording policy.
- `POST /api/v1/voice/transcribe` — multipart WAV. Silence returns
  `speech_detected: false` and an empty transcript rather than an invented one.
- `POST /api/v1/voice/synthesize` — WAV as base64.

Audio is processed in memory and never written to disk by the server.

---

## Example: a complete session

```bash
BASE=http://127.0.0.1:8756

# 1. Allow a folder, then grant the capability. Both are needed.
curl -s $BASE/api/v1/permissions/directories \
  -H 'content-type: application/json' \
  -d '{"path":"'"$HOME"'/Documents"}'

curl -s $BASE/api/v1/permissions \
  -H 'content-type: application/json' \
  -d '{"scope":"files:read","grant":true,"resources":["'"$HOME"'/Documents"]}'

# 2. Ask something.
curl -s $BASE/api/v1/chat \
  -H 'content-type: application/json' \
  -d '{"message":"Find the project report and summarise it"}' | jq .response

# 3. A high-impact action pauses.
SESSION=$(curl -s $BASE/api/v1/sessions -H 'content-type: application/json' \
  -d '{"title":"demo"}' | jq -r .session_id)

curl -s $BASE/api/v1/permissions -H 'content-type: application/json' \
  -d '{"scope":"email:draft","grant":true}' >/dev/null
curl -s $BASE/api/v1/permissions -H 'content-type: application/json' \
  -d '{"scope":"email:send","grant":true}' >/dev/null

curl -s $BASE/api/v1/chat -H 'content-type: application/json' \
  -d '{"message":"Draft an email to rahul@example.com saying the report is ready.","session_id":"'"$SESSION"'"}' >/dev/null

CFM=$(curl -s $BASE/api/v1/chat -H 'content-type: application/json' \
  -d '{"message":"Send the email.","session_id":"'"$SESSION"'"}' | jq -r .pending_confirmation.id)

# 4. Approve it explicitly.
curl -s $BASE/api/v1/chat -H 'content-type: application/json' \
  -d '{"message":"Send the email.","session_id":"'"$SESSION"'","confirmation_id":"'"$CFM"'","confirm":true}' | jq .response

# 5. See exactly what happened.
curl -s "$BASE/api/v1/audit?limit=20" | jq -r '.events[] | "\(.timestamp) \(.action) \(.target // "")"'
```
