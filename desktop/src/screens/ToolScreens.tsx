/**
 * Calendar, Email, Browser, Terminal and Memory.
 *
 * These screens are thin: they call the same tools the agent calls, so the
 * permission engine and the confirmation gate apply identically whether an
 * action came from a sentence or a button.
 */

import { useCallback, useState } from 'react';
import { useAsync } from '@/hooks/useAsync';
import { ApiRequestError, api } from '@/lib/api';
import type { CalendarEvent, ConfirmationRequest, EmailDraft, MemoryRecord } from '@/lib/types';
import { formatDateTime, formatRelative, shortenPath } from '@/lib/format';
import { pushToast } from '@/lib/store';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Chip, EmptyState, ErrorState, Icon, Section, SkeletonList } from '@/components/primitives';

/** Runs a tool, surfacing the confirmation dialog when the runtime demands one. */
function useToolRunner(onDone?: () => void) {
  const [confirmation, setConfirmation] = useState<ConfirmationRequest | null>(null);
  const [pending, setPending] = useState<{ tool: string; args: Record<string, unknown> } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);

  const run = useCallback(
    async (tool: string, args: Record<string, unknown>, confirmationId?: string) => {
      setBusy(true);
      try {
        const result = await api.execute(tool, args, confirmationId);
        if (!result.success) {
          pushToast({ kind: 'error', title: result.error ?? 'That did not work' });
          return null;
        }
        setConfirmation(null);
        setPending(null);
        onDone?.();
        return result;
      } catch (caught) {
        if (caught instanceof ApiRequestError && caught.isConfirmation) {
          const request = caught.details.confirmation as ConfirmationRequest | undefined;
          if (request) {
            setConfirmation(request);
            setPending({ tool, args });
            return null;
          }
        }
        pushToast({
          kind: 'error',
          title: 'Refused',
          detail: caught instanceof Error ? caught.message : undefined,
        });
        return null;
      } finally {
        setBusy(false);
      }
    },
    [onDone],
  );

  const dialog = confirmation ? (
    <ConfirmDialog
      request={confirmation}
      busy={busy}
      onApprove={() => {
        if (pending) void run(pending.tool, pending.args, confirmation.id);
      }}
      onReject={() => {
        setConfirmation(null);
        setPending(null);
      }}
    />
  ) : null;

  return { run, dialog, busy };
}

/* ------------------------------------------------------------------ Calendar */

export function Calendar() {
  const [days, setDays] = useState(7);
  const { data, error, loading, reload } = useAsync(() => api.calendarEvents(days), [days]);
  const { run, dialog, busy } = useToolRunner(reload);
  const [form, setForm] = useState({ title: '', start: '', participants: '' });

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto max-w-3xl space-y-4">
        <header className="flex items-end justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-graphite-100">Calendar</h1>
            <p className="mt-1 text-sm text-graphite-500">
              Stored as iCalendar files on this machine. No account, no sync.
            </p>
          </div>
          <div className="flex gap-1.5">
            {[1, 7, 30].map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setDays(value)}
                className={`chip ${days === value ? 'border-accent/40 bg-accent-soft text-accent' : 'border-graphite-700 text-graphite-400'}`}
              >
                {value === 1 ? 'Today' : `${value} days`}
              </button>
            ))}
          </div>
        </header>

        {loading ? (
          <SkeletonList rows={4} />
        ) : error ? (
          <ErrorState detail={error.message} code={error.code} onRetry={reload} />
        ) : (data ?? []).length === 0 ? (
          <EmptyState title="Nothing scheduled" detail="Create an event below, or just ask." />
        ) : (
          <ul className="panel divide-y divide-graphite-850">
            {(data ?? []).map((event: CalendarEvent) => (
              <li key={event.id} className="flex items-center gap-4 px-4 py-3">
                <div className="w-32 shrink-0">
                  <p className="text-sm text-graphite-200">{formatDateTime(event.start)}</p>
                  <p className="text-2xs text-graphite-600">{event.timezone}</p>
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-graphite-100">{event.title}</p>
                  <p className="mt-0.5 truncate text-2xs text-graphite-500">
                    {[event.location, event.participants.join(', ')].filter(Boolean).join(' · ')}
                  </p>
                </div>
                <button
                  type="button"
                  className="btn-quiet"
                  disabled={busy}
                  aria-label={`Cancel ${event.title}`}
                  onClick={() => void run('calendar.cancel_event', { event_id: event.id })}
                >
                  <Icon name="x" className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
        )}

        <Section
          title="New event"
          description="You will see the full details before anything is created."
        >
          <form
            className="grid grid-cols-1 gap-2 sm:grid-cols-3"
            onSubmit={(event) => {
              event.preventDefault();
              void run('calendar.create_event', {
                title: form.title,
                start: form.start,
                participants: form.participants
                  .split(',')
                  .map((value) => value.trim())
                  .filter(Boolean),
              });
            }}
          >
            <input
              className="field"
              placeholder="Title"
              aria-label="Event title"
              value={form.title}
              onChange={(event) => setForm({ ...form, title: event.target.value })}
            />
            <input
              className="field"
              type="datetime-local"
              aria-label="Start"
              value={form.start}
              onChange={(event) => setForm({ ...form, start: event.target.value })}
            />
            <div className="flex gap-2">
              <input
                className="field"
                placeholder="Participants"
                aria-label="Participants"
                value={form.participants}
                onChange={(event) => setForm({ ...form, participants: event.target.value })}
              />
              <button
                type="submit"
                className="btn-primary"
                disabled={busy || !form.title || !form.start}
              >
                Create
              </button>
            </div>
          </form>
        </Section>
      </div>
      {dialog}
    </div>
  );
}

/* --------------------------------------------------------------------- Email */

export function Email() {
  const { data, error, loading, reload } = useAsync(() => api.emailDrafts(), []);
  const { run, dialog, busy } = useToolRunner(reload);
  const [form, setForm] = useState({ to: '', subject: '', body: '' });

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto max-w-3xl space-y-4">
        <header>
          <h1 className="text-xl font-semibold tracking-tight text-graphite-100">Email</h1>
          <p className="mt-1 text-sm text-graphite-500">
            Drafts live on this machine. Sending always shows you the exact message first and
            requires your approval — every time, with no way to remember the answer.
          </p>
        </header>

        <Section title="New draft" description="Writing a draft never sends anything.">
          <form
            className="space-y-2"
            onSubmit={(event) => {
              event.preventDefault();
              void run('email.draft', {
                to: form.to
                  .split(',')
                  .map((value) => value.trim())
                  .filter(Boolean),
                subject: form.subject,
                body: form.body,
              });
              setForm({ to: '', subject: '', body: '' });
            }}
          >
            <input
              className="field"
              placeholder="To (comma separated)"
              aria-label="To"
              value={form.to}
              onChange={(event) => setForm({ ...form, to: event.target.value })}
            />
            <input
              className="field"
              placeholder="Subject"
              aria-label="Subject"
              value={form.subject}
              onChange={(event) => setForm({ ...form, subject: event.target.value })}
            />
            <textarea
              className="field min-h-24"
              placeholder="Message"
              aria-label="Message body"
              value={form.body}
              onChange={(event) => setForm({ ...form, body: event.target.value })}
            />
            <button type="submit" className="btn-ghost" disabled={busy || !form.to}>
              Save draft
            </button>
          </form>
        </Section>

        {loading ? (
          <SkeletonList rows={3} />
        ) : error ? (
          <ErrorState detail={error.message} code={error.code} onRetry={reload} />
        ) : (data ?? []).length === 0 ? (
          <EmptyState
            title="No drafts"
            detail="Ask PRIVIA to draft something, or write one above."
          />
        ) : (
          <ul className="panel divide-y divide-graphite-850">
            {(data ?? []).map((draft: EmailDraft) => (
              <li key={draft.id} className="px-4 py-3">
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm text-graphite-100">
                      {draft.subject || '(no subject)'}
                    </p>
                    <p className="mt-0.5 truncate text-2xs text-graphite-500">
                      To {draft.to.map((address) => address.address).join(', ')} ·{' '}
                      {formatRelative(draft.updated_at)}
                    </p>
                    <p className="mt-1.5 line-clamp-2 text-sm text-graphite-400">{draft.body}</p>
                  </div>
                  <Chip tone={draft.status === 'sent' ? 'accent' : 'neutral'}>{draft.status}</Chip>
                  {draft.status === 'draft' ? (
                    <button
                      type="button"
                      className="btn-ghost shrink-0"
                      disabled={busy}
                      onClick={() => void run('email.send', { draft_id: draft.id })}
                    >
                      Send…
                    </button>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
      {dialog}
    </div>
  );
}

/* ------------------------------------------------------------------- Browser */

export function Browser() {
  const [url, setUrl] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState<Record<string, unknown> | null>(null);
  const [results, setResults] = useState<{ title: string; url: string; snippet: string }[]>([]);
  const { run, dialog, busy } = useToolRunner();

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto max-w-3xl space-y-4">
        <header>
          <h1 className="text-xl font-semibold tracking-tight text-graphite-100">Browser</h1>
          <p className="mt-1 text-sm text-graphite-500">
            A reader, not a browser. No scripts run, no cookies are sent, and private or loopback
            addresses are always blocked. Everything a page says is treated as data.
          </p>
        </header>

        <Section title="Read a page">
          <form
            className="flex gap-2"
            onSubmit={async (event) => {
              event.preventDefault();
              const result = await run('browser.open_url', { url });
              if (result) setPage(result.data as Record<string, unknown>);
            }}
          >
            <input
              className="field font-mono text-2xs"
              placeholder="https://example.com/page"
              aria-label="URL"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
            />
            <button type="submit" className="btn-primary" disabled={busy || !url}>
              Open
            </button>
            <button
              type="button"
              className="btn-ghost"
              disabled={busy || !url}
              onClick={async () => {
                const result = await run('browser.inspect_url', { url });
                if (result) {
                  const data = result.data as { allowed: boolean; reason: string };
                  pushToast({
                    kind: data.allowed ? 'success' : 'warning',
                    title: data.allowed ? 'That URL is allowed' : 'That URL is blocked',
                    detail: data.reason,
                  });
                }
              }}
            >
              Check first
            </button>
          </form>
        </Section>

        <Section title="Search the web">
          <form
            className="flex gap-2"
            onSubmit={async (event) => {
              event.preventDefault();
              const result = await run('browser.search', { query });
              if (result) {
                setResults((result.data as { results: typeof results }).results);
              }
            }}
          >
            <input
              className="field"
              placeholder="What are you looking for?"
              aria-label="Search query"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <button type="submit" className="btn-primary" disabled={busy || !query}>
              Search
            </button>
          </form>
          {results.length > 0 ? (
            <ul className="mt-3 space-y-2">
              {results.map((result) => (
                <li key={result.url} className="rounded-lg border border-graphite-800 px-3 py-2">
                  <p className="text-sm text-graphite-200">{result.title}</p>
                  <p className="mt-0.5 truncate font-mono text-2xs text-accent">{result.url}</p>
                  <p className="mt-1 text-sm text-graphite-500">{result.snippet}</p>
                  <button
                    type="button"
                    className="btn-quiet mt-1 text-2xs"
                    onClick={() => setUrl(result.url)}
                  >
                    Use this URL
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </Section>

        {page ? (
          <Section
            title={String(page.title || page.final_url || 'Page')}
            description={String(page.final_url ?? '')}
            action={
              Array.isArray(page.injection_flags) && page.injection_flags.length > 0 ? (
                <Chip tone="caution" title={String(page.injection_flags)}>
                  <Icon name="alert" className="h-3 w-3" /> injection markers
                </Chip>
              ) : (
                <Chip>untrusted content</Chip>
              )
            }
          >
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-graphite-800 bg-graphite-950 p-3 text-2xs leading-relaxed text-graphite-300">
              {String(page.text ?? '')}
            </pre>
          </Section>
        ) : null}
      </div>
      {dialog}
    </div>
  );
}

/* ------------------------------------------------------------------ Terminal */

export function Terminal() {
  const [command, setCommand] = useState('');
  const [cwd, setCwd] = useState('');
  const [output, setOutput] = useState<
    { command: string; stdout: string; stderr: string; code: number }[]
  >([]);
  const { run, dialog, busy } = useToolRunner();
  const allowed = useAsync(() => api.execute('terminal.list_allowed', {}), []);
  const roots = useAsync(() => api.fileRoots(), []);

  const allowedData = allowed.data?.success
    ? (allowed.data.data as {
        allowed: { program: string; description: string; always_confirms: boolean }[];
      })
    : null;

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto max-w-3xl space-y-4">
        <header>
          <h1 className="text-xl font-semibold tracking-tight text-graphite-100">Terminal</h1>
          <p className="mt-1 text-sm text-graphite-500">
            Commands are parsed into arguments and run without a shell. Only allowlisted programs,
            only inside your workspace folders, always with a timeout and an output cap.
          </p>
        </header>

        <Section title="Run a command">
          <form
            className="space-y-2"
            onSubmit={async (event) => {
              event.preventDefault();
              const result = await run('terminal.run', { command, cwd });
              if (result) {
                const data = result.data as {
                  stdout: string;
                  stderr: string;
                  exit_code: number;
                };
                setOutput((current) => [
                  { command, stdout: data.stdout, stderr: data.stderr, code: data.exit_code },
                  ...current,
                ]);
              }
            }}
          >
            <div className="flex gap-2">
              <input
                className="field font-mono text-2xs"
                placeholder="pytest -q"
                aria-label="Command"
                value={command}
                onChange={(event) => setCommand(event.target.value)}
              />
              <button
                type="button"
                className="btn-ghost"
                disabled={!command}
                onClick={async () => {
                  const result = await run('terminal.inspect', { command });
                  if (result) {
                    const data = result.data as { allowed: boolean; reason: string };
                    pushToast({
                      kind: data.allowed ? 'success' : 'warning',
                      title: data.allowed ? 'Allowed' : 'Not allowed',
                      detail: data.reason,
                    });
                  }
                }}
              >
                Explain
              </button>
            </div>
            <div className="flex gap-2">
              <select
                className="field font-mono text-2xs"
                aria-label="Working directory"
                value={cwd}
                onChange={(event) => setCwd(event.target.value)}
              >
                <option value="">Choose a workspace folder…</option>
                {(roots.data?.roots ?? []).map((root) => (
                  <option key={root.path} value={root.path}>
                    {root.path}
                  </option>
                ))}
              </select>
              <button type="submit" className="btn-primary" disabled={busy || !command || !cwd}>
                Run…
              </button>
            </div>
          </form>
        </Section>

        {output.length > 0 ? (
          <Section title="Output">
            <ul className="space-y-3">
              {output.map((entry, index) => (
                <li key={index} className="rounded-lg border border-graphite-800">
                  <div className="flex items-center gap-2 border-b border-graphite-850 px-3 py-1.5">
                    <span className="font-mono text-2xs text-graphite-400">$ {entry.command}</span>
                    <Chip tone={entry.code === 0 ? 'accent' : 'danger'}>exit {entry.code}</Chip>
                  </div>
                  <pre className="max-h-64 overflow-auto whitespace-pre-wrap px-3 py-2 font-mono text-2xs text-graphite-300">
                    {entry.stdout || '(no output)'}
                    {entry.stderr ? `\n\nstderr:\n${entry.stderr}` : ''}
                  </pre>
                </li>
              ))}
            </ul>
          </Section>
        ) : null}

        <Section
          title="What PRIVIA may run"
          description="Anything not on this list is refused before it reaches the operating system."
        >
          {allowed.loading ? (
            <SkeletonList rows={3} />
          ) : allowedData ? (
            <div className="flex flex-wrap gap-1.5">
              {allowedData.allowed.map((rule) => (
                <span
                  key={rule.program}
                  title={rule.description}
                  className={`chip ${rule.always_confirms ? 'border-caution/40 text-caution' : 'border-graphite-700 text-graphite-400'}`}
                >
                  {rule.program}
                </span>
              ))}
            </div>
          ) : (
            <EmptyState title="Could not load the allowlist" />
          )}
        </Section>
      </div>
      {dialog}
    </div>
  );
}

/* -------------------------------------------------------------------- Memory */

export function Memory() {
  const [query, setQuery] = useState('');
  const { data, loading, reload } = useAsync(() => api.memories(query), [query]);
  const stats = useAsync(() => api.memoryStats(), []);
  const [content, setContent] = useState('');

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto max-w-3xl space-y-4">
        <header>
          <h1 className="text-xl font-semibold tracking-tight text-graphite-100">Memory</h1>
          <p className="mt-1 text-sm text-graphite-500">
            Only what you asked PRIVIA to remember. Every entry shows where it came from, and
            credentials are refused outright.
          </p>
        </header>

        {stats.data ? (
          <div className="flex flex-wrap gap-2 text-2xs">
            <Chip tone={data?.enabled ? 'accent' : 'caution'}>
              {data?.enabled ? 'memory on' : 'memory off'}
            </Chip>
            <Chip>{String(stats.data.total)} stored</Chip>
            <Chip>{String(stats.data.indexed)} indexed</Chip>
            <Chip>{String(stats.data.embedding_model)}</Chip>
          </div>
        ) : null}

        <Section title="Remember something">
          <form
            className="flex gap-2"
            onSubmit={async (event) => {
              event.preventDefault();
              try {
                await api.remember(content);
                setContent('');
                pushToast({ kind: 'success', title: 'Noted' });
                reload();
                stats.reload();
              } catch (caught) {
                pushToast({
                  kind: 'error',
                  title: 'Not stored',
                  detail: caught instanceof Error ? caught.message : undefined,
                });
              }
            }}
          >
            <input
              className="field"
              placeholder="I prefer concise answers"
              aria-label="New memory"
              value={content}
              onChange={(event) => setContent(event.target.value)}
            />
            <button type="submit" className="btn-primary" disabled={!content.trim()}>
              Remember
            </button>
          </form>
        </Section>

        <div className="relative">
          <Icon
            name="search"
            className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-graphite-600"
          />
          <input
            className="field pl-8"
            placeholder="Search memories…"
            aria-label="Search memories"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>

        {loading ? (
          <SkeletonList rows={4} />
        ) : (data?.memories ?? []).length === 0 ? (
          <EmptyState
            title="Nothing remembered"
            detail="PRIVIA only stores what you explicitly ask it to keep."
          />
        ) : (
          <ul className="panel divide-y divide-graphite-850">
            {(data?.memories ?? []).map((record: MemoryRecord) => (
              <li key={record.id} className="flex items-start gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-graphite-200">{record.content}</p>
                  <p className="mt-1 flex flex-wrap items-center gap-2 text-2xs text-graphite-600">
                    <span>{record.kind}</span>
                    <span>· from {shortenPath(record.provenance, 30)}</span>
                    <span>· {formatRelative(record.created_at)}</span>
                    {record.score !== null ? <span>· score {record.score.toFixed(2)}</span> : null}
                  </p>
                </div>
                <button
                  type="button"
                  className="btn-quiet"
                  aria-label="Forget this"
                  onClick={async () => {
                    await api.forget(record.id);
                    pushToast({ kind: 'success', title: 'Forgotten' });
                    reload();
                    stats.reload();
                  }}
                >
                  <Icon name="trash" className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
