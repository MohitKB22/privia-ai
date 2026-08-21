/**
 * The confirmation gate.
 *
 * This dialog is the last thing between a proposed action and a real one. It
 * always shows the exact target, never pre-selects the confirming button, and
 * for destructive actions requires a deliberate second step rather than a
 * single reflexive click.
 */

import { useEffect, useRef, useState } from 'react';
import type { ConfirmationRequest } from '@/lib/types';
import { Icon, RiskChip } from './primitives';

interface Props {
  request: ConfirmationRequest;
  busy?: boolean;
  onApprove: () => void;
  onReject: () => void;
}

export function ConfirmDialog({ request, busy = false, onApprove, onReject }: Props) {
  const [armed, setArmed] = useState(!request.destructive);
  const rejectRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  // Focus lands on "Don't do it" so a stray Enter never approves anything.
  useEffect(() => rejectRef.current?.focus(), []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onReject();
        return;
      }
      if (event.key !== 'Tab' || !dialogRef.current) return;
      const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (focusable.length === 0) return;
      const first = focusable[0]!;
      const last = focusable[focusable.length - 1]!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onReject]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-graphite-950/80 p-6 backdrop-blur-sm"
      role="presentation"
    >
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-summary"
        className="w-full max-w-lg animate-slide-up rounded-2xl border border-graphite-700 bg-graphite-900 shadow-lift"
      >
        <header className="flex items-start gap-3 border-b border-graphite-800 px-5 py-4">
          <span
            className={`mt-0.5 rounded-lg p-1.5 ${
              request.destructive ? 'bg-danger-soft text-danger' : 'bg-accent-soft text-accent'
            }`}
          >
            <Icon name={request.destructive ? 'alert' : 'shield'} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 id="confirm-title" className="text-sm font-semibold text-graphite-100">
              {request.title}
            </h2>
            <p id="confirm-summary" className="mt-1 text-sm text-graphite-400">
              {request.summary}
            </p>
          </div>
          <RiskChip level={request.risk_level} />
        </header>

        <dl className="max-h-72 divide-y divide-graphite-800 overflow-y-auto px-5">
          {Object.entries(request.details).map(([key, value]) => (
            <div key={key} className="grid grid-cols-[7.5rem_1fr] gap-3 py-2.5 text-sm">
              <dt className="text-graphite-500">{key}</dt>
              <dd className="whitespace-pre-wrap break-words text-graphite-200">{value}</dd>
            </div>
          ))}
        </dl>

        {request.destructive ? (
          <div className="mx-5 mb-1 mt-3 rounded-lg border border-danger/30 bg-danger-soft px-3 py-2">
            <label className="flex items-start gap-2.5 text-sm text-graphite-300">
              <input
                type="checkbox"
                checked={armed}
                onChange={(event) => setArmed(event.target.checked)}
                className="mt-0.5 h-4 w-4 accent-current"
              />
              <span>
                I understand this cannot be undone
                {request.target ? (
                  <span className="mt-0.5 block break-all font-mono text-2xs text-graphite-500">
                    {request.target}
                  </span>
                ) : null}
              </span>
            </label>
          </div>
        ) : null}

        <footer className="flex items-center justify-between gap-3 px-5 py-4">
          <p className="text-2xs text-graphite-600">Nothing has happened yet. Esc cancels.</p>
          <div className="flex gap-2">
            <button ref={rejectRef} type="button" className="btn-ghost" onClick={onReject}>
              Don&apos;t do it
            </button>
            <button
              type="button"
              disabled={!armed || busy}
              onClick={onApprove}
              className={request.destructive ? 'btn-danger' : 'btn-primary'}
            >
              {busy ? 'Working…' : request.destructive ? 'Yes, do it' : 'Approve'}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
