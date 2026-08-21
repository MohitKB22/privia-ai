/**
 * The Privacy Center.
 *
 * Everything about where data goes, what is permitted, and how to erase it,
 * on one screen. Enabling cloud processing is a two-step, clearly-worded action;
 * turning it off is one click.
 */

import { useState } from 'react';
import { useAsync } from '@/hooks/useAsync';
import { api } from '@/lib/api';
import { pushToast } from '@/lib/store';
import { formatRelative } from '@/lib/format';
import {
  Chip,
  Dot,
  ErrorState,
  Icon,
  Section,
  SkeletonList,
  Toggle,
} from '@/components/primitives';

export function Privacy() {
  const { data, error, loading, reload } = useAsync(() => api.privacy(), []);
  const permissions = useAsync(() => api.permissions(), []);
  const [busy, setBusy] = useState(false);
  const [confirmCloud, setConfirmCloud] = useState(false);
  const [newDirectory, setNewDirectory] = useState('');

  const update = async (patch: Record<string, unknown>, label: string) => {
    setBusy(true);
    try {
      await api.setPrivacy(patch);
      pushToast({ kind: 'success', title: label });
      reload();
    } catch (caught) {
      pushToast({
        kind: 'error',
        title: 'That change was refused',
        detail: caught instanceof Error ? caught.message : undefined,
      });
    } finally {
      setBusy(false);
      setConfirmCloud(false);
    }
  };

  if (loading)
    return (
      <div className="p-6">
        <SkeletonList rows={6} />
      </div>
    );
  if (error || !data) {
    return (
      <div className="p-6">
        <ErrorState detail={error?.message} code={error?.code} onRetry={reload} />
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto max-w-3xl space-y-4">
        <header>
          <h1 className="text-xl font-semibold tracking-tight text-graphite-100">Privacy Center</h1>
          <p className="mt-1 text-sm text-graphite-500">
            Where your data is processed, what PRIVIA may touch, and how to erase it.
          </p>
        </header>

        <div
          className={`panel flex items-center gap-3 p-4 ${
            data.data_leaving_device
              ? 'border-caution/40 bg-caution-soft'
              : 'border-accent/30 bg-accent-soft'
          }`}
        >
          <Icon
            name={data.data_leaving_device ? 'cloud' : 'lock'}
            className={`h-5 w-5 ${data.data_leaving_device ? 'text-caution' : 'text-accent'}`}
          />
          <div>
            <p className="text-sm font-medium text-graphite-100">
              {data.data_leaving_device
                ? 'Some data can leave this device'
                : 'Nothing is leaving this device'}
            </p>
            <p className="mt-0.5 text-sm text-graphite-400">
              {data.data_leaving_device
                ? `Requests may be sent to ${data.cloud_provider}. Your files themselves are never uploaded; the text you ask about is.`
                : 'Local model, local speech, local embeddings, local database. No telemetry, no sync.'}
            </p>
          </div>
        </div>

        <Section
          title="Processing"
          description="Local is the default and always works. Cloud is opt-in and reversible."
        >
          <div className="divide-y divide-graphite-850">
            <div className="flex items-center justify-between py-3">
              <div>
                <p className="text-sm text-graphite-200">Local processing</p>
                <p className="mt-0.5 text-sm text-graphite-500">
                  {data.current_llm?.available
                    ? `${data.current_llm.provider}: ${data.current_llm.model}`
                    : 'No local model installed; the offline planner is handling requests.'}
                </p>
              </div>
              <Chip tone="accent">
                <Dot tone="ok" /> always on
              </Chip>
            </div>

            <Toggle
              tone="caution"
              checked={data.cloud_processing}
              disabled={busy || !data.cloud_provider}
              label="Cloud processing"
              description={
                data.cloud_provider
                  ? `Send requests to ${data.cloud_provider} when you ask for it or when no local model is available.`
                  : 'No cloud provider is configured. Set one in Settings first.'
              }
              onChange={(next) => {
                if (next) setConfirmCloud(true);
                else void update({ cloud_processing: false }, 'Cloud processing turned off');
              }}
            />

            {confirmCloud ? (
              <div className="my-2 rounded-lg border border-caution/40 bg-caution-soft p-3">
                <p className="text-sm font-medium text-graphite-100">
                  Turning this on sends data off this device
                </p>
                <ul className="mt-2 space-y-1 text-sm text-graphite-300">
                  <li>· Your messages and conversation context go to {data.cloud_provider}</li>
                  <li>· Text from files you ask about is included in the request</li>
                  <li>· Your files, credentials and audit log are never sent</li>
                  <li>· PRIVIA will still ask before every high-impact action</li>
                </ul>
                <div className="mt-3 flex gap-2">
                  <button
                    type="button"
                    className="btn-primary"
                    disabled={busy}
                    onClick={() =>
                      void update(
                        { cloud_processing: true, cloud_consent: true },
                        'Cloud processing turned on',
                      )
                    }
                  >
                    I understand, turn it on
                  </button>
                  <button
                    type="button"
                    className="btn-ghost"
                    onClick={() => setConfirmCloud(false)}
                  >
                    Keep it local
                  </button>
                </div>
              </div>
            ) : null}

            <Toggle
              checked={data.memory_enabled}
              disabled={busy}
              label="Memory"
              description="Let PRIVIA remember things you explicitly ask it to. Never stores credentials."
              onChange={(next) =>
                void update({ memory_enabled: next }, next ? 'Memory on' : 'Memory off')
              }
            />

            <div className="flex items-center justify-between py-3">
              <div>
                <p className="text-sm text-graphite-200">Telemetry</p>
                <p className="mt-0.5 text-sm text-graphite-500">
                  PRIVIA has no telemetry sink. There is nothing to send and nowhere to send it.
                </p>
              </div>
              <Chip tone="accent">never</Chip>
            </div>
          </div>
        </Section>

        <Section
          title="Folders PRIVIA may see"
          description="File tools refuse every path outside this list, before any permission check."
        >
          <ul className="space-y-1.5">
            {data.allowed_directories.length === 0 ? (
              <li className="rounded-lg border border-dashed border-graphite-800 px-3 py-4 text-center text-sm text-graphite-500">
                No folders allowed. PRIVIA cannot read anything on this machine.
              </li>
            ) : (
              data.allowed_directories.map((directory) => (
                <li
                  key={directory}
                  className="flex items-center gap-3 rounded-lg border border-graphite-800 px-3 py-2"
                >
                  <Icon name="files" className="h-3.5 w-3.5 text-graphite-600" />
                  <span className="min-w-0 flex-1 break-all font-mono text-2xs text-graphite-300">
                    {directory}
                  </span>
                  <button
                    type="button"
                    className="btn-quiet text-2xs"
                    disabled={busy}
                    onClick={async () => {
                      setBusy(true);
                      try {
                        await api.removeDirectory(directory);
                        pushToast({ kind: 'success', title: 'Folder removed' });
                        reload();
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    Remove
                  </button>
                </li>
              ))
            )}
          </ul>
          <form
            className="mt-3 flex gap-2"
            onSubmit={async (event) => {
              event.preventDefault();
              if (!newDirectory.trim()) return;
              setBusy(true);
              try {
                await api.addDirectory(newDirectory.trim());
                setNewDirectory('');
                pushToast({ kind: 'success', title: 'Folder allowed' });
                reload();
              } catch (caught) {
                pushToast({
                  kind: 'error',
                  title: 'That folder was refused',
                  detail: caught instanceof Error ? caught.message : undefined,
                });
              } finally {
                setBusy(false);
              }
            }}
          >
            <input
              value={newDirectory}
              onChange={(event) => setNewDirectory(event.target.value)}
              placeholder="/Users/you/Documents"
              aria-label="Folder to allow"
              className="field font-mono text-2xs"
            />
            <button type="submit" className="btn-ghost" disabled={busy || !newDirectory.trim()}>
              Allow
            </button>
          </form>
        </Section>

        <Section
          title="Permissions"
          description="What PRIVIA is allowed to do. Revoking takes effect immediately."
          action={
            <button
              type="button"
              className="btn-danger"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await api.resetPermissions();
                  pushToast({ kind: 'success', title: 'All permissions revoked' });
                  permissions.reload();
                  reload();
                } finally {
                  setBusy(false);
                }
              }}
            >
              Revoke everything
            </button>
          }
        >
          {permissions.loading ? (
            <SkeletonList rows={5} />
          ) : (
            <ul className="divide-y divide-graphite-850">
              {(permissions.data?.scopes ?? []).map((scope) => (
                <li key={scope.scope} className="flex items-center justify-between gap-4 py-2.5">
                  <div className="min-w-0">
                    <p className="text-sm text-graphite-200">{scope.description}</p>
                    <p className="mt-0.5 font-mono text-2xs text-graphite-600">
                      {scope.scope}
                      {scope.resources.length
                        ? ` · limited to ${scope.resources.length} location(s)`
                        : ''}
                    </p>
                  </div>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={async () => {
                      setBusy(true);
                      try {
                        await api.setPermission(scope.scope, scope.state !== 'granted');
                        permissions.reload();
                        reload();
                      } finally {
                        setBusy(false);
                      }
                    }}
                    className={`chip shrink-0 ${
                      scope.state === 'granted'
                        ? 'border-accent/40 bg-accent-soft text-accent'
                        : scope.state === 'denied'
                          ? 'border-danger/40 bg-danger-soft text-danger'
                          : 'border-graphite-700 text-graphite-500'
                    }`}
                  >
                    {scope.state === 'granted'
                      ? 'allowed'
                      : scope.state === 'denied'
                        ? 'denied'
                        : 'not asked'}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="Recent activity" description="The last 25 recorded events.">
          <ul className="space-y-1">
            {data.recent_activity.slice(0, 12).map((event) => (
              <li key={event.id} className="flex items-center gap-3 text-2xs">
                <span className="w-16 text-graphite-600">{formatRelative(event.timestamp)}</span>
                <span className="font-mono text-graphite-400">{event.action}</span>
                <span className="min-w-0 flex-1 truncate text-graphite-600">
                  {event.target ?? ''}
                </span>
              </li>
            ))}
          </ul>
        </Section>

        <Section
          title="Your data"
          description="Export a complete copy, or erase what PRIVIA keeps. Both are local operations."
        >
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="btn-ghost"
              onClick={async () => {
                const payload = await api.exportData();
                const blob = new Blob([JSON.stringify(payload, null, 2)], {
                  type: 'application/json',
                });
                const url = URL.createObjectURL(blob);
                const anchor = document.createElement('a');
                anchor.href = url;
                anchor.download = `privia-export-${new Date().toISOString().slice(0, 10)}.json`;
                anchor.click();
                URL.revokeObjectURL(url);
                pushToast({ kind: 'success', title: 'Export downloaded' });
              }}
            >
              Export everything
            </button>
            <PurgeButton
              label="Delete conversations"
              options={{ conversations: true }}
              onDone={reload}
            />
            <PurgeButton label="Delete memories" options={{ memories: true }} onDone={reload} />
            <PurgeButton label="Delete audit log" options={{ audit_log: true }} onDone={reload} />
          </div>
          <p className="mt-3 text-2xs text-graphite-600">
            Database: <span className="font-mono">{data.database_path}</span>
          </p>
        </Section>
      </div>
    </div>
  );
}

function PurgeButton({
  label,
  options,
  onDone,
}: {
  label: string;
  options: Record<string, boolean>;
  onDone: () => void;
}) {
  const [armed, setArmed] = useState(false);
  if (!armed) {
    return (
      <button type="button" className="btn-ghost" onClick={() => setArmed(true)}>
        {label}
      </button>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-lg border border-danger/40 bg-danger-soft px-2 py-1">
      <span className="text-2xs text-graphite-300">Sure?</span>
      <button
        type="button"
        className="btn-danger px-2 py-0.5 text-2xs"
        onClick={async () => {
          const result = await api.purge(options);
          setArmed(false);
          pushToast({
            kind: 'success',
            title: 'Deleted',
            detail: Object.entries(result.deleted)
              .map(([key, value]) => `${value} ${key}`)
              .join(', '),
          });
          onDone();
        }}
      >
        Yes
      </button>
      <button
        type="button"
        className="btn-quiet px-2 py-0.5 text-2xs"
        onClick={() => setArmed(false)}
      >
        No
      </button>
    </span>
  );
}
