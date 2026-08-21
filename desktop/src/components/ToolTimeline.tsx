/** What the assistant did, in order, with what it touched. */

import { useState } from 'react';
import type { ToolCall, ToolResult } from '@/lib/types';
import { formatDuration, shortenPath, titleCase } from '@/lib/format';
import { Icon, RiskChip } from './primitives';

interface Props {
  calls: ToolCall[];
  results: ToolResult[];
  accessed?: string[];
  compact?: boolean;
}

export function ToolTimeline({ calls, results, accessed = [], compact = false }: Props) {
  const [open, setOpen] = useState<string | null>(null);
  if (calls.length === 0 && accessed.length === 0) return null;

  const resultFor = (call: ToolCall) =>
    results.find((result) => result.call_id === call.id) ??
    results.find((result) => result.tool_name === call.tool_name);

  return (
    <div className={compact ? 'mt-2' : 'mt-3'}>
      <ol className="space-y-1.5">
        {calls.map((call, index) => {
          const result = resultFor(call);
          const failed = result ? !result.success : false;
          const untrusted = Boolean(result?.metadata?.untrusted);
          const expanded = open === call.id;
          return (
            <li key={call.id}>
              <button
                type="button"
                onClick={() => setOpen(expanded ? null : call.id)}
                aria-expanded={expanded}
                className="flex w-full items-center gap-2.5 rounded-lg border border-graphite-800 bg-graphite-950/50 px-2.5 py-1.5 text-left transition-colors hover:border-graphite-700"
              >
                <span className="font-mono text-2xs text-graphite-600">{index + 1}</span>
                <span
                  className={failed ? 'text-danger' : result ? 'text-accent' : 'text-graphite-500'}
                >
                  <Icon
                    name={failed ? 'x' : result ? 'check' : 'refresh'}
                    className="h-3.5 w-3.5"
                  />
                </span>
                <span className="flex-1 truncate font-mono text-2xs text-graphite-300">
                  {call.tool_name}
                </span>
                {untrusted ? (
                  <span
                    className="chip border-caution/40 text-caution"
                    title="This result contains third-party content. It was treated as data, never as instructions."
                  >
                    untrusted
                  </span>
                ) : null}
                <RiskChip level={call.risk_level} />
                {result ? (
                  <span className="font-mono text-2xs text-graphite-600">
                    {formatDuration(result.duration_ms)}
                  </span>
                ) : null}
              </button>

              {expanded ? (
                <div className="mt-1 space-y-2 rounded-lg border border-graphite-800 bg-graphite-950 px-3 py-2.5 text-2xs">
                  {call.justification ? (
                    <p className="text-graphite-400">{call.justification}</p>
                  ) : null}
                  <div>
                    <p className="label mb-1">Arguments</p>
                    <pre className="overflow-x-auto font-mono text-graphite-400">
                      {JSON.stringify(call.arguments, null, 2)}
                    </pre>
                  </div>
                  {result?.error ? (
                    <div>
                      <p className="label mb-1 text-danger">Error</p>
                      <p className="text-danger">{result.error}</p>
                      {result.error_code ? (
                        <p className="mt-0.5 font-mono text-graphite-600">{result.error_code}</p>
                      ) : null}
                    </div>
                  ) : null}
                  {result?.accessed_resources?.length ? (
                    <div>
                      <p className="label mb-1">Touched</p>
                      {result.accessed_resources.slice(0, 8).map((resource) => (
                        <p key={resource} className="break-all font-mono text-graphite-500">
                          {shortenPath(resource, 72)}
                        </p>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </li>
          );
        })}
      </ol>

      {accessed.length > 0 ? (
        <details className="mt-2 rounded-lg border border-graphite-800 bg-graphite-950/40 px-2.5 py-1.5">
          <summary className="cursor-pointer text-2xs text-graphite-500">
            {accessed.length} resource{accessed.length === 1 ? '' : 's'} accessed
          </summary>
          <ul className="mt-1.5 space-y-0.5">
            {accessed.map((resource) => (
              <li key={resource} className="break-all font-mono text-2xs text-graphite-500">
                {resource.includes('/') ? shortenPath(resource, 72) : titleCase(resource)}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
