import { useEffect, useMemo, useState } from 'react';
import { useAsync } from '@/hooks/useAsync';
import { api, subscribeToActivity } from '@/lib/api';
import type { AuditEvent } from '@/lib/types';
import { OUTCOME_STYLES, formatRelative, shortenPath } from '@/lib/format';
import { Chip, EmptyState, ErrorState, Icon, SkeletonList } from '@/components/primitives';

const FILTERS = [
  { label: 'Everything', value: '' },
  { label: 'Tools', value: 'tool.' },
  { label: 'Permissions', value: 'permission.' },
  { label: 'Confirmations', value: 'confirmation.' },
  { label: 'Files', value: 'file.' },
  { label: 'Security', value: 'security.' },
];

export function Activity() {
  const { data, error, loading, reload } = useAsync(() => api.audit({ limit: 200 }), []);
  const [live, setLive] = useState<AuditEvent[]>([]);
  const [filter, setFilter] = useState('');
  const [streaming, setStreaming] = useState(true);

  useEffect(() => {
    if (!streaming) return;
    return subscribeToActivity((event) => {
      setLive((current) => [event, ...current].slice(0, 200));
    });
  }, [streaming]);

  const events = useMemo(() => {
    const merged = [...live, ...(data?.events ?? [])];
    const seen = new Set<string>();
    const unique = merged.filter((event) => {
      if (seen.has(event.id)) return false;
      seen.add(event.id);
      return true;
    });
    return filter ? unique.filter((event) => event.action.startsWith(filter)) : unique;
  }, [data, live, filter]);

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto max-w-4xl space-y-4">
        <header className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-graphite-100">Activity</h1>
            <p className="mt-1 text-sm text-graphite-500">
              Every tool call, permission decision and side effect, in order. This is the record
              PRIVIA writes before it answers you.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setStreaming((value) => !value)}
              className={streaming ? 'btn-ghost border-accent/40 text-accent' : 'btn-ghost'}
            >
              <Icon name="activity" className="h-3.5 w-3.5" />
              {streaming ? 'Live' : 'Paused'}
            </button>
            <button type="button" onClick={reload} className="btn-quiet" aria-label="Refresh">
              <Icon name="refresh" />
            </button>
          </div>
        </header>

        <div className="flex flex-wrap gap-1.5">
          {FILTERS.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => setFilter(option.value)}
              className={`chip ${
                filter === option.value
                  ? 'border-accent/40 bg-accent-soft text-accent'
                  : 'border-graphite-700 text-graphite-400 hover:text-graphite-200'
              }`}
            >
              {option.label}
            </button>
          ))}
          <span className="ml-auto self-center text-2xs text-graphite-600">
            {events.length} shown · {data?.total ?? 0} recorded
          </span>
        </div>

        {loading ? (
          <SkeletonList rows={8} />
        ) : error ? (
          <ErrorState detail={error.message} code={error.code} onRetry={reload} />
        ) : events.length === 0 ? (
          <EmptyState
            title="No activity yet"
            detail="Ask PRIVIA to do something and every step will appear here."
          />
        ) : (
          <ol className="panel divide-y divide-graphite-850">
            {events.map((event) => (
              <li key={event.id} className="flex items-start gap-3 px-4 py-2.5">
                <span className="w-16 shrink-0 pt-0.5 text-2xs text-graphite-600">
                  {formatRelative(event.timestamp)}
                </span>
                <span
                  className={`w-40 shrink-0 truncate font-mono text-2xs ${OUTCOME_STYLES[event.outcome]}`}
                  title={event.action}
                >
                  {event.action}
                </span>
                <div className="min-w-0 flex-1">
                  {event.target ? (
                    <p className="break-all font-mono text-2xs text-graphite-400">
                      {shortenPath(event.target, 70)}
                    </p>
                  ) : null}
                  {Object.keys(event.detail).length > 0 ? (
                    <p className="mt-0.5 truncate text-2xs text-graphite-600">
                      {Object.entries(event.detail)
                        .slice(0, 4)
                        .map(([key, value]) => `${key}=${String(value).slice(0, 40)}`)
                        .join('  ')}
                    </p>
                  ) : null}
                </div>
                {event.tool_name ? <Chip>{event.tool_name}</Chip> : null}
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
