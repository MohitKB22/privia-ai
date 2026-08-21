import { useState } from 'react';
import { useAsync } from '@/hooks/useAsync';
import { api } from '@/lib/api';
import { pushToast } from '@/lib/store';
import { formatBytes, formatDuration } from '@/lib/format';
import { Chip, Dot, ErrorState, Icon, Section, SkeletonList } from '@/components/primitives';

export function Settings() {
  const status = useAsync(() => api.status(), []);
  const secrets = useAsync(() => api.secrets(), []);
  const tools = useAsync(() => api.tools(), []);
  const [secretKey, setSecretKey] = useState('');
  const [secretValue, setSecretValue] = useState('');
  const [busy, setBusy] = useState(false);

  if (status.error) {
    return (
      <div className="p-6">
        <ErrorState
          detail={status.error.message}
          code={status.error.code}
          onRetry={status.reload}
        />
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto max-w-3xl space-y-4">
        <header>
          <h1 className="text-xl font-semibold tracking-tight text-graphite-100">Settings</h1>
          <p className="mt-1 text-sm text-graphite-500">
            Models, credentials and diagnostics. Privacy controls live in the Privacy Center.
          </p>
        </header>

        {status.loading ? (
          <SkeletonList rows={5} />
        ) : status.data ? (
          <>
            <Section
              title="Models"
              description="Configured through environment variables or a .env file."
            >
              <dl className="divide-y divide-graphite-850 text-sm">
                <Row label="Local model">
                  <span className="flex items-center gap-2">
                    <Dot tone={status.data.models.local.available ? 'ok' : 'warn'} />
                    <span className="font-mono">
                      {status.data.models.local.provider}:{status.data.models.local.model}
                    </span>
                  </span>
                </Row>
                <Row label="Detail">
                  <span className="text-graphite-500">{status.data.models.local.detail}</span>
                </Row>
                <Row label="Cloud model">
                  {status.data.models.cloud ? (
                    <span className="font-mono">
                      {status.data.models.cloud.provider}:{status.data.models.cloud.model}
                    </span>
                  ) : (
                    <span className="text-graphite-500">not configured (local only)</span>
                  )}
                </Row>
                <Row label="Embeddings">
                  <span className="font-mono">
                    {status.data.models.embeddings.model} ·{' '}
                    {status.data.models.embeddings.dimensions}d
                  </span>
                </Row>
                <Row label="Speech to text">
                  <span className="flex items-center gap-2">
                    <Dot tone={status.data.speech.stt.status === 'ready' ? 'ok' : 'warn'} />
                    <span className="text-graphite-500">{status.data.speech.stt.detail}</span>
                  </span>
                </Row>
                <Row label="Text to speech">
                  <span className="text-graphite-500">{status.data.speech.tts.detail}</span>
                </Row>
              </dl>
              <div className="mt-4 rounded-lg border border-graphite-800 bg-graphite-950/60 p-3">
                <p className="text-2xs text-graphite-500">
                  To use a local language model, install Ollama and pull a model:
                </p>
                <pre className="mt-1.5 font-mono text-2xs text-accent">
                  {`ollama pull ${status.data.models.local.model}
ollama serve`}
                </pre>
              </div>
            </Section>

            <Section
              title="Credentials"
              description="Stored in the OS keychain or an encrypted local file. Never written to the database, never logged, never returned by the API."
            >
              {secrets.loading ? (
                <SkeletonList rows={2} />
              ) : secrets.data ? (
                <>
                  <div className="mb-3 flex flex-wrap items-center gap-2 text-2xs">
                    <Chip tone="accent">
                      <Icon name="lock" className="h-3 w-3" />
                      {secrets.data.writable_backend}
                    </Chip>
                    {secrets.data.stored_keys.length === 0 ? (
                      <span className="text-graphite-600">nothing stored</span>
                    ) : (
                      secrets.data.stored_keys.map((key) => (
                        <span key={key} className="chip border-graphite-700 text-graphite-400">
                          {key}
                          <button
                            type="button"
                            aria-label={`Delete ${key}`}
                            className="ml-1 text-graphite-600 hover:text-danger"
                            onClick={async () => {
                              await api.deleteSecret(key);
                              pushToast({ kind: 'success', title: 'Credential removed' });
                              secrets.reload();
                            }}
                          >
                            <Icon name="x" className="h-3 w-3" />
                          </button>
                        </span>
                      ))
                    )}
                  </div>
                  <form
                    className="flex gap-2"
                    onSubmit={async (event) => {
                      event.preventDefault();
                      setBusy(true);
                      try {
                        await api.setSecret(secretKey, secretValue);
                        setSecretKey('');
                        setSecretValue('');
                        pushToast({ kind: 'success', title: 'Credential stored' });
                        secrets.reload();
                      } catch (caught) {
                        pushToast({
                          kind: 'error',
                          title: 'Refused',
                          detail: caught instanceof Error ? caught.message : undefined,
                        });
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    <select
                      className="field"
                      aria-label="Credential"
                      value={secretKey}
                      onChange={(event) => setSecretKey(event.target.value)}
                    >
                      <option value="">Choose…</option>
                      {secrets.data.settable_keys.map((key) => (
                        <option key={key} value={key}>
                          {key}
                        </option>
                      ))}
                    </select>
                    <input
                      className="field"
                      type="password"
                      autoComplete="off"
                      placeholder="Value"
                      aria-label="Credential value"
                      value={secretValue}
                      onChange={(event) => setSecretValue(event.target.value)}
                    />
                    <button
                      type="submit"
                      className="btn-ghost"
                      disabled={busy || !secretKey || !secretValue}
                    >
                      Store
                    </button>
                  </form>
                </>
              ) : null}
            </Section>

            <Section title="Runtime" description="Diagnostics for this installation.">
              <dl className="divide-y divide-graphite-850 text-sm">
                <Row label="Version">
                  <span className="font-mono">{status.data.version}</span>
                </Row>
                <Row label="Environment">
                  <span className="font-mono">{status.data.app_env}</span>
                </Row>
                <Row label="Python">
                  <span className="font-mono">
                    {status.data.python} on {status.data.platform}
                  </span>
                </Row>
                <Row label="Uptime">
                  <span>{formatDuration(status.data.uptime_seconds * 1000)}</span>
                </Row>
                <Row label="Database">
                  <span className="break-all font-mono text-2xs">
                    {status.data.database.path} · v{status.data.database.schema_version} ·{' '}
                    {formatBytes(status.data.database.size_bytes)}
                  </span>
                </Row>
                <Row label="Tools">
                  <span>{status.data.tools} registered</span>
                </Row>
              </dl>
            </Section>

            <Section
              title="Tool catalogue"
              description="Everything PRIVIA can do, with the permission each needs."
            >
              {tools.loading ? (
                <SkeletonList rows={5} />
              ) : (
                <ul className="divide-y divide-graphite-850">
                  {(tools.data ?? []).map((tool) => (
                    <li key={tool.name} className="flex items-start gap-3 py-2.5">
                      <div className="min-w-0 flex-1">
                        <p className="font-mono text-2xs text-graphite-200">{tool.name}</p>
                        <p className="mt-0.5 text-sm text-graphite-500">{tool.description}</p>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1">
                        <span
                          className={`chip ${
                            tool.risk_level === 'critical' || tool.risk_level === 'high'
                              ? 'border-caution/40 text-caution'
                              : 'border-graphite-700 text-graphite-500'
                          }`}
                        >
                          {tool.risk_level}
                        </span>
                        {tool.requires_confirmation ? (
                          <span className="chip border-accent/30 text-accent">asks first</span>
                        ) : null}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
          </>
        ) : null}
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[8rem_1fr] items-center gap-3 py-2">
      <dt className="text-graphite-500">{label}</dt>
      <dd className="min-w-0 text-graphite-200">{children}</dd>
    </div>
  );
}
