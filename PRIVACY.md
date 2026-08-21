# Privacy

The short version: PRIVIA runs on your machine, stores everything in one SQLite
file you own, sends nothing anywhere by default, and can show you and delete
every byte it keeps.

---

## What runs where

| Component | Default | Leaves your machine? |
|---|---|---|
| Language model | Ollama, locally | No |
| Speech to text | Whisper, locally | No |
| Text to speech | OS voices | No |
| Embeddings | Local, in-process | No |
| Database | SQLite at `~/.privia/privia.db` | No |
| Notes, memories, drafts | Local | No |
| Calendar | iCalendar files at `~/.privia/calendar` | No |
| Email | Local mailbox at `~/.privia/mail` | No, until you configure SMTP |
| Web pages | Fetched only when you ask | The request itself |
| Cloud model | Disabled | Only when you enable it |

The only outbound connection PRIVIA makes on its own initiative is fetching a
web page you asked it to read.

---

## What is stored

Everything lives in one SQLite file:

| Table | Contents | Cleared by |
|---|---|---|
| `sessions`, `messages` | Your conversations | Delete conversations |
| `runs`, `tool_calls`, `tool_results` | What the agent did | Delete conversations |
| `memories`, `memory_vectors` | Facts you asked it to remember | Delete memories |
| `notes` | Your notes | Per note |
| `email_drafts` | Drafts, and sent metadata | Per draft |
| `permissions` | What you granted | Revoke everything |
| `audit_events` | The activity log | Delete audit log |
| `settings` | Your preferences | — |

**Not stored:** passwords, API keys, or tokens. Those live in the OS keychain or
an encrypted file, and are excluded from exports by design.

---

## Cloud processing

Off by default. Turning it on requires three separate things:

1. A provider and API key in your configuration.
2. `CLOUD_PROCESSING_ENABLED=true`, or the toggle in the Privacy Center, which
   shows exactly what would be sent before you confirm.
3. The `cloud:inference` capability.

When it is on:

- **Sent:** your message, recent conversation context, and the text of content
  you asked about.
- **Not sent:** your files themselves, your credentials, your audit log, your
  memories unless relevant to the message.
- The status bar reads "Cloud enabled" and every reply is labelled with where it
  was processed.

Turning it off takes one click and takes effect immediately.

---

## Memory

Memory stores only what you explicitly ask it to. It:

- refuses anything containing credentials or financial identifiers, and anything
  that matches a live-credential pattern,
- records provenance on every entry, so "why do you know that?" is answerable,
- is fully inspectable and deletable, entry by entry,
- can be switched off entirely.

---

## Telemetry

There is none. No analytics, no crash reporting, no usage statistics, no update
pings. There is no telemetry sink anywhere in the codebase; the
`TELEMETRY_ENABLED` flag only affects how long local metrics are retained in
memory.

Your data is never used to train anything.

---

## Logs

Structured logs go to stderr and a rotating file in `~/.privia/logs`. Every field
passes through a redactor first. The logger explicitly refuses to record file
contents, email bodies, page text or full command output.

---

## Your rights, as buttons

| You want to | Do this |
|---|---|
| See everything stored about you | Privacy Center → Export everything |
| See what it did | Activity, or `GET /api/v1/audit` |
| Delete conversations | Privacy Center → Delete conversations |
| Delete memories | Privacy Center → Delete memories |
| Delete the audit log | Privacy Center → Delete audit log |
| Revoke all access | Privacy Center → Revoke everything |
| Delete absolutely everything | `rm -rf ~/.privia` |

The export is complete, portable JSON. There is no server-side copy, because
there is no server.

---

## Data retention

Nothing expires on its own. PRIVIA keeps what you keep and deletes what you
delete. If you set a retention period for cloud requests, it is recorded and
surfaced in the Privacy Center, but the enforcement of retention on the
provider's side is the provider's, not ours, and we say so rather than implying
otherwise.
