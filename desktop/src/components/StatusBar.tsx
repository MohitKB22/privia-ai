/**
 * The privacy status bar.
 *
 * It answers, at a glance and at all times: where is this running, is anything
 * leaving the machine, and is the microphone open. These are the three facts a
 * private assistant must never make you hunt for.
 */

import { usePolling } from '@/hooks/useAsync';
import { api } from '@/lib/api';
import { appStore, navigate } from '@/lib/store';
import { formatDuration } from '@/lib/format';
import { Dot, Icon } from './primitives';

export function StatusBar({ micActive }: { micActive: boolean }) {
  const { data, error } = usePolling(() => api.status(), 15_000);
  const online = !error || error.code !== 'NETWORK_UNREACHABLE';

  if (appStore.getState().backendOnline !== online) {
    appStore.setState({ backendOnline: online });
  }

  if (!online) {
    return (
      <div className="flex items-center gap-2 border-t border-danger/30 bg-danger-soft px-4 py-1.5 text-2xs text-danger">
        <Dot tone="bad" />
        The backend is not running. Start it with <code className="font-mono">make dev</code>.
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex items-center gap-3 border-t border-graphite-850 px-4 py-1.5 text-2xs text-graphite-600">
        <Dot tone="idle" /> Connecting…
      </div>
    );
  }

  const leaving = data.privacy.data_leaving_device;
  const local = data.models.local;

  return (
    <div className="flex items-center gap-4 border-t border-graphite-850 bg-graphite-900/60 px-4 py-1.5 text-2xs">
      <button
        type="button"
        onClick={() => navigate('privacy')}
        className={`flex items-center gap-1.5 rounded px-1 py-0.5 transition-colors hover:bg-graphite-800 ${
          leaving ? 'text-caution' : 'text-accent'
        }`}
        title={
          leaving
            ? `Cloud processing is on. Requests can go to ${data.models.cloud?.provider ?? 'a provider'}.`
            : 'Everything runs on this machine. Nothing is being sent anywhere.'
        }
      >
        <Icon name={leaving ? 'cloud' : 'lock'} className="h-3.5 w-3.5" />
        {leaving ? 'Cloud enabled' : 'Local only'}
      </button>

      <span className="flex items-center gap-1.5 text-graphite-500" title={local.detail}>
        <Dot tone={local.available ? 'ok' : 'warn'} />
        <Icon name="cpu" className="h-3.5 w-3.5" />
        {local.available ? `${local.model}` : 'offline planner'}
        {local.latency_ms ? (
          <span className="text-graphite-600">{formatDuration(local.latency_ms)}</span>
        ) : null}
      </span>

      <span className="text-graphite-600" title="Semantic memory index">
        {data.models.embeddings.model}
      </span>

      {micActive ? (
        <span className="flex animate-pulse-soft items-center gap-1.5 text-danger">
          <Dot tone="bad" />
          <Icon name="mic" className="h-3.5 w-3.5" />
          Recording
        </span>
      ) : (
        <span className="flex items-center gap-1.5 text-graphite-600" title="Microphone is closed">
          <Icon name="mic" className="h-3.5 w-3.5" />
          {data.speech.stt.status === 'ready' ? 'Mic ready' : 'Mic off'}
        </span>
      )}

      <span className="ml-auto flex items-center gap-3 text-graphite-600">
        {!data.privacy.telemetry_enabled ? (
          <span title="PRIVIA sends no telemetry">No telemetry</span>
        ) : null}
        <span>{data.tools} tools</span>
        <span title={data.database.path}>v{data.version}</span>
      </span>
    </div>
  );
}
