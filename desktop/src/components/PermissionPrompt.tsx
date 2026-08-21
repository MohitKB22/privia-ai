/**
 * Asks for a capability the assistant needs.
 *
 * Scopes are described in plain language, and the concrete resource is always
 * shown, so "allow file access" is never an abstract decision.
 */

import { useState } from 'react';
import type { PermissionPrompt as Prompt } from '@/lib/types';
import { Icon } from './primitives';
import { shortenPath } from '@/lib/format';

const SCOPE_TEXT: Record<string, string> = {
  'files:read': 'read files in the folders you allow',
  'files:write': 'create and edit files in the folders you allow',
  'files:delete': 'delete files (it will still ask before each one)',
  'notes:read': 'read your notes',
  'notes:write': 'create and edit your notes',
  'calendar:read': 'see your calendar',
  'calendar:write': 'create and update events',
  'calendar:delete': 'cancel events (it will still ask first)',
  'email:read': 'read your email',
  'email:draft': 'write drafts (it cannot send them)',
  'email:send': 'send email (it will still ask before every send)',
  'browser:read': 'fetch and read public web pages',
  'terminal:exec': 'run allowlisted commands in your project folders',
  'memory:read': 'use what it remembers about you',
  'memory:write': 'remember new things you approve',
  'cloud:inference': 'send this request to a cloud AI provider',
};

interface Props {
  prompt: Prompt;
  busy?: boolean;
  onAllow: (scopes: string[], resources: string[]) => void;
  onDeny: () => void;
}

export function PermissionPrompt({ prompt, busy, onAllow, onDeny }: Props) {
  const [scopeOnly, setScopeOnly] = useState(true);
  const resources = prompt.resources.length ? prompt.resources : prompt.out_of_scope_resources;
  const cloud = prompt.missing_scopes.includes('cloud:inference');

  return (
    <div className="panel animate-fade-in p-4">
      <div className="flex items-start gap-3">
        <span
          className={`mt-0.5 rounded-lg p-1.5 ${cloud ? 'bg-caution-soft text-caution' : 'bg-accent-soft text-accent'}`}
        >
          <Icon name={cloud ? 'cloud' : 'lock'} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-graphite-100">
            {cloud ? 'Send this to the cloud?' : 'PRIVIA needs your permission'}
          </p>
          <ul className="mt-2 space-y-1">
            {prompt.missing_scopes.map((scope) => (
              <li key={scope} className="flex items-start gap-2 text-sm text-graphite-300">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-graphite-600" />
                <span>
                  Allow it to {SCOPE_TEXT[scope] ?? scope}
                  <code className="ml-1.5 font-mono text-2xs text-graphite-600">{scope}</code>
                </span>
              </li>
            ))}
          </ul>

          {resources.length > 0 ? (
            <div className="mt-3 rounded-lg border border-graphite-800 bg-graphite-950/60 px-3 py-2">
              <p className="label mb-1">For</p>
              {resources.slice(0, 4).map((resource) => (
                <p key={resource} className="break-all font-mono text-2xs text-graphite-400">
                  {shortenPath(resource, 68)}
                </p>
              ))}
              {resources.length > 4 ? (
                <p className="mt-1 text-2xs text-graphite-600">and {resources.length - 4} more</p>
              ) : null}
              <label className="mt-2 flex items-center gap-2 text-2xs text-graphite-400">
                <input
                  type="checkbox"
                  checked={scopeOnly}
                  onChange={(event) => setScopeOnly(event.target.checked)}
                  className="h-3.5 w-3.5"
                />
                Only for these, not everywhere
              </label>
            </div>
          ) : null}

          <div className="mt-3 flex items-center gap-2">
            <button
              type="button"
              className="btn-primary"
              disabled={busy}
              onClick={() => onAllow(prompt.missing_scopes, scopeOnly ? resources : [])}
            >
              Allow
            </button>
            <button type="button" className="btn-ghost" disabled={busy} onClick={onDeny}>
              Not now
            </button>
          </div>
          <p className="mt-2 text-2xs text-graphite-600">
            You can change this at any time in the Privacy Center.
          </p>
        </div>
      </div>
    </div>
  );
}
