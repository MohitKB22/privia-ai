import { useAsync } from '@/hooks/useAsync';
import { api } from '@/lib/api';
import { navigate } from '@/lib/store';
import { formatBytes, formatRelative } from '@/lib/format';
import { Chip, Dot, EmptyState, ErrorState, Icon, SkeletonList } from '@/components/primitives';

export function Home() {
  const status = useAsync(() => api.status(), []);
  const activity = useAsync(() => api.audit({ limit: 8 }), []);

  if (status.error) {
    return (
      <div className="p-6">
        <ErrorState
          title="PRIVIA cannot reach its backend"
          detail={status.error.message}
          code={status.error.code}
          onRetry={status.reload}
        />
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto max-w-4xl space-y-5">
        <header>
          <h1 className="text-xl font-semibold tracking-tight text-graphite-100">
            Everything is running on this machine
          </h1>
          <p className="mt-1 text-sm text-graphite-500">
            {status.data?.privacy.data_leaving_device
              ? 'Cloud processing is enabled. Some requests can leave this device.'
              : 'No data is leaving this device. Cloud AI is off and there is no telemetry.'}
          </p>
        </header>

        {status.loading ? (
          <SkeletonList rows={3} />
        ) : status.data ? (
          <>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <Card
                icon="cpu"
                title="Language model"
                value={
                  status.data.models.local.available
                    ? status.data.models.local.model
                    : 'Offline planner'
                }
                detail={status.data.models.local.detail}
                tone={status.data.models.local.available ? 'ok' : 'warn'}
              />
              <Card
                icon="lock"
                title="Processing"
                value={status.data.privacy.data_leaving_device ? 'Local + cloud' : 'Local only'}
                detail={
                  status.data.models.cloud
                    ? `Cloud provider: ${status.data.models.cloud.provider}`
                    : 'No cloud provider configured'
                }
                tone={status.data.privacy.data_leaving_device ? 'warn' : 'ok'}
                onClick={() => navigate('privacy')}
              />
              <Card
                icon="files"
                title="Database"
                value={formatBytes(status.data.database.size_bytes)}
                detail={`Schema v${status.data.database.schema_version} · ${status.data.database.path}`}
                tone="ok"
              />
            </div>

            {status.data.warnings.length > 0 ? (
              <div className="panel border-caution/30 bg-caution-soft p-4">
                <p className="flex items-center gap-2 text-sm font-medium text-caution">
                  <Icon name="alert" className="h-4 w-4" />
                  Worth knowing
                </p>
                <ul className="mt-2 space-y-1 text-sm text-graphite-300">
                  {status.data.warnings.map((warning) => (
                    <li key={warning}>· {warning}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            <section className="panel p-5">
              <h2 className="mb-3 text-sm font-semibold text-graphite-100">Integrations</h2>
              <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {status.data.integrations.map((integration) => (
                  <li
                    key={integration.name}
                    className="flex items-start gap-2.5 rounded-lg border border-graphite-800 px-3 py-2"
                  >
                    <Dot
                      tone={
                        integration.status === 'ready'
                          ? 'ok'
                          : integration.status === 'error'
                            ? 'bad'
                            : 'warn'
                      }
                    />
                    <div className="min-w-0">
                      <p className="text-sm text-graphite-200">{integration.name}</p>
                      <p
                        className="mt-0.5 truncate text-2xs text-graphite-500"
                        title={integration.detail}
                      >
                        {integration.detail}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          </>
        ) : null}

        <section className="panel p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-graphite-100">Recent activity</h2>
            <button type="button" onClick={() => navigate('activity')} className="btn-quiet">
              See all
            </button>
          </div>
          {activity.loading ? (
            <SkeletonList rows={4} />
          ) : activity.data && activity.data.events.length > 0 ? (
            <ul className="space-y-1">
              {activity.data.events.map((event) => (
                <li
                  key={event.id}
                  className="flex items-center gap-3 rounded-lg px-2 py-1.5 text-sm hover:bg-graphite-850"
                >
                  <Chip
                    tone={
                      event.outcome === 'success'
                        ? 'accent'
                        : event.outcome === 'denied'
                          ? 'caution'
                          : 'neutral'
                    }
                  >
                    {event.action}
                  </Chip>
                  <span className="min-w-0 flex-1 truncate text-graphite-400">
                    {event.target ?? event.tool_name ?? ''}
                  </span>
                  <span className="text-2xs text-graphite-600">
                    {formatRelative(event.timestamp)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              title="Nothing has happened yet"
              detail="Anything PRIVIA does will be recorded here, including every permission decision."
            />
          )}
        </section>
      </div>
    </div>
  );
}

function Card({
  icon,
  title,
  value,
  detail,
  tone,
  onClick,
}: {
  icon: 'cpu' | 'lock' | 'files';
  title: string;
  value: string;
  detail: string;
  tone: 'ok' | 'warn';
  onClick?: () => void;
}) {
  const Wrapper = onClick ? 'button' : 'div';
  return (
    <Wrapper
      {...(onClick ? { type: 'button' as const, onClick } : {})}
      className={`panel p-4 text-left ${onClick ? 'transition-colors hover:border-graphite-700' : ''}`}
    >
      <div className="flex items-center gap-2 text-graphite-500">
        <Icon name={icon} className="h-3.5 w-3.5" />
        <span className="label">{title}</span>
        <Dot tone={tone} />
      </div>
      <p className="mt-2 truncate font-mono text-sm text-graphite-100">{value}</p>
      <p className="mt-1 line-clamp-2 text-2xs text-graphite-500">{detail}</p>
    </Wrapper>
  );
}
