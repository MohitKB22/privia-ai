/** Small shared building blocks: states, chips, icons, panels. */

import type { ReactNode } from 'react';
import { RISK_STYLES } from '@/lib/format';

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden />;
}

export function SkeletonList({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2" role="status" aria-label="Loading">
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-12 w-full" />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  detail,
  action,
  icon,
}: {
  title: string;
  detail?: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-graphite-800 px-6 py-14 text-center">
      {icon ? <div className="mb-3 text-graphite-600">{icon}</div> : null}
      <p className="text-sm font-medium text-graphite-300">{title}</p>
      {detail ? <p className="mt-1.5 max-w-md text-sm text-graphite-500">{detail}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function ErrorState({
  title = 'Something went wrong',
  detail,
  code,
  onRetry,
}: {
  title?: string;
  detail?: string;
  code?: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="rounded-xl border border-danger/30 bg-danger-soft px-4 py-4 text-sm"
    >
      <p className="font-medium text-danger">{title}</p>
      {detail ? <p className="mt-1 text-graphite-300">{detail}</p> : null}
      {code ? <p className="mt-2 font-mono text-2xs text-graphite-500">{code}</p> : null}
      {onRetry ? (
        <button type="button" onClick={onRetry} className="btn-ghost mt-3">
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function RiskChip({ level }: { level: string }) {
  return <span className={`chip ${RISK_STYLES[level] ?? RISK_STYLES.low}`}>{level}</span>;
}

export function Chip({
  children,
  tone = 'neutral',
  title,
}: {
  children: ReactNode;
  tone?: 'neutral' | 'accent' | 'danger' | 'caution';
  title?: string;
}) {
  const tones = {
    neutral: 'border-graphite-700 text-graphite-400',
    accent: 'border-accent/40 bg-accent-soft text-accent',
    danger: 'border-danger/40 bg-danger-soft text-danger',
    caution: 'border-caution/40 bg-caution-soft text-caution',
  };
  return (
    <span className={`chip ${tones[tone]}`} title={title}>
      {children}
    </span>
  );
}

export function Dot({ tone }: { tone: 'ok' | 'warn' | 'bad' | 'idle' }) {
  const tones = {
    ok: 'bg-accent',
    warn: 'bg-caution',
    bad: 'bg-danger',
    idle: 'bg-graphite-600',
  };
  return <span className={`inline-block h-1.5 w-1.5 rounded-full ${tones[tone]}`} aria-hidden />;
}

export function Section({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel p-5">
      <header className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold text-graphite-100">{title}</h2>
          {description ? (
            <p className="mt-1 max-w-2xl text-sm text-graphite-500">{description}</p>
          ) : null}
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  description,
  disabled,
  tone = 'accent',
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  description?: string;
  disabled?: boolean;
  tone?: 'accent' | 'caution';
}) {
  return (
    <label
      className={`flex items-start justify-between gap-6 py-3 ${disabled ? 'opacity-50' : ''}`}
    >
      <span className="min-w-0">
        <span className="block text-sm text-graphite-200">{label}</span>
        {description ? (
          <span className="mt-0.5 block text-sm text-graphite-500">{description}</span>
        ) : null}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors ${
          checked ? (tone === 'caution' ? 'bg-caution' : 'bg-accent') : 'bg-graphite-700'
        }`}
      >
        <span
          className={`absolute top-0.5 h-4 w-4 rounded-full bg-graphite-950 transition-transform ${
            checked ? 'translate-x-4' : 'translate-x-0.5'
          }`}
        />
      </button>
    </label>
  );
}

/** Inline SVG icons. No icon dependency, no network fetch. */
export function Icon({ name, className = 'h-4 w-4' }: { name: IconName; className?: string }) {
  const path = ICONS[name];
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      {path}
    </svg>
  );
}

export type IconName = keyof typeof ICONS;

const ICONS = {
  home: <path d="M3 10.5 12 3l9 7.5M5 9.5V21h14V9.5" />,
  chat: <path d="M21 12a8 8 0 0 1-11.6 7.1L3 21l1.9-6.4A8 8 0 1 1 21 12Z" />,
  activity: <path d="M3 12h4l3 8 4-16 3 8h4" />,
  files: (
    <path d="M4 6a2 2 0 0 1 2-2h3.5l2 2.5H18a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z" />
  ),
  calendar: (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18M8 3v4M16 3v4" />
    </>
  ),
  mail: (
    <>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="m3 7 9 6 9-6" />
    </>
  ),
  globe: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18Z" />
    </>
  ),
  terminal: <path d="m5 8 4 4-4 4M12 16h7" />,
  note: (
    <>
      <path d="M5 4h9l5 5v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z" />
      <path d="M14 4v5h5M8 13h8M8 17h5" />
    </>
  ),
  brain: (
    <path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-1 5.8A3 3 0 0 0 8 18a3 3 0 0 0 4 2 3 3 0 0 0 4-2 3 3 0 0 0 3-5.2A3 3 0 0 0 18 7a3 3 0 0 0-3-3 3 3 0 0 0-3 1.5A3 3 0 0 0 9 4Z" />
  ),
  shield: <path d="M12 3 5 6v6c0 4.5 3 7.7 7 9 4-1.3 7-4.5 7-9V6l-7-3Z" />,
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2V21a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 7 19.4a1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 3 15a1.7 1.7 0 0 0-1.6-1H1a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 2.7 8.7a1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 9 3V3a2 2 0 1 1 4 0v.1A1.7 1.7 0 0 0 15 4.6" />
    </>
  ),
  mic: (
    <>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
    </>
  ),
  send: <path d="m4 12 16-8-6 16-2.5-6.5L4 12Z" />,
  search: (
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="m20 20-4.3-4.3" />
    </>
  ),
  lock: (
    <>
      <rect x="4" y="10" width="16" height="10" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </>
  ),
  check: <path d="m5 12 5 5 9-11" />,
  x: <path d="M6 6l12 12M18 6 6 18" />,
  alert: <path d="M12 4 2.5 20h19L12 4Zm0 6v5m0 3h.01" />,
  trash: <path d="M4 7h16M9 7V5h6v2m-8 0 1 13h8l1-13" />,
  plus: <path d="M12 5v14M5 12h14" />,
  refresh: (
    <path d="M20 11A8 8 0 0 0 6.3 6.3L4 8.5M4 13a8 8 0 0 0 13.7 4.7L20 15.5M4 4v4.5h4.5M20 20v-4.5h-4.5" />
  ),
  cpu: (
    <>
      <rect x="7" y="7" width="10" height="10" rx="1.5" />
      <path d="M4 10h3M4 14h3M17 10h3M17 14h3M10 4v3M14 4v3M10 17v3M14 17v3" />
    </>
  ),
  cloud: <path d="M7 18a4 4 0 0 1 .5-8 5.5 5.5 0 0 1 10.6 1.6A3.5 3.5 0 0 1 17.5 18H7Z" />,
  eye: (
    <>
      <path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6-10-6-10-6Z" />
      <circle cx="12" cy="12" r="2.5" />
    </>
  ),
} as const;
