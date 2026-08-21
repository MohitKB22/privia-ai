import { appStore, dismissToast } from '@/lib/store';
import { Icon } from './primitives';

const TONES = {
  info: 'border-graphite-700 bg-graphite-850 text-graphite-200',
  success: 'border-accent/40 bg-accent-soft text-graphite-100',
  warning: 'border-caution/40 bg-caution-soft text-graphite-100',
  error: 'border-danger/40 bg-danger-soft text-graphite-100',
} as const;

export function Toasts() {
  const toasts = appStore.useStore((state) => state.toasts);
  if (toasts.length === 0) return null;
  return (
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-40 flex w-80 flex-col gap-2"
      role="region"
      aria-live="polite"
      aria-label="Notifications"
    >
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`pointer-events-auto animate-slide-up rounded-xl border px-3.5 py-2.5 shadow-lift ${TONES[toast.kind]}`}
        >
          <div className="flex items-start gap-2.5">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">{toast.title}</p>
              {toast.detail ? (
                <p className="mt-0.5 break-words text-sm opacity-80">{toast.detail}</p>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() => dismissToast(toast.id)}
              aria-label="Dismiss"
              className="text-graphite-500 hover:text-graphite-200"
            >
              <Icon name="x" className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
