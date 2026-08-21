/** Small, dependency-free formatting helpers. */

export function formatBytes(bytes: number): string {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** index;
  return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

export function formatRelative(iso: string | number | null | undefined): string {
  if (!iso) return '';
  const then = typeof iso === 'number' ? iso : Date.parse(iso);
  if (Number.isNaN(then)) return '';
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604_800) return `${Math.floor(seconds / 86_400)}d ago`;
  return new Date(then).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? ''
    : date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

/** Shorten a long path so both ends stay readable. */
export function shortenPath(path: string, max = 52): string {
  if (path.length <= max) return path;
  const parts = path.split('/');
  const file = parts.pop() ?? '';
  const head = parts.slice(0, 2).join('/');
  return `${head}/…/${file}`.slice(0, max + 8);
}

export function basename(path: string): string {
  return path.split('/').filter(Boolean).pop() ?? path;
}

export function titleCase(value: string): string {
  return value.replace(/[._-]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export const RISK_STYLES: Record<string, string> = {
  none: 'border-graphite-700 text-graphite-400',
  low: 'border-graphite-700 text-graphite-300',
  medium: 'border-caution/40 text-caution bg-caution-soft',
  high: 'border-caution/50 text-caution bg-caution-soft',
  critical: 'border-danger/50 text-danger bg-danger-soft',
};

export const OUTCOME_STYLES: Record<string, string> = {
  success: 'text-accent',
  failure: 'text-danger',
  denied: 'text-caution',
  pending: 'text-graphite-400',
};
