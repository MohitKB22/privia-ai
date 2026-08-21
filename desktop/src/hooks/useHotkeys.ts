import { useEffect } from 'react';

type Handler = (event: KeyboardEvent) => void;

const isTypingTarget = (target: EventTarget | null) => {
  const element = target as HTMLElement | null;
  if (!element) return false;
  const tag = element.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || element.isContentEditable;
};

/**
 * Bind keyboard shortcuts.
 *
 * Keys are written as `mod+k` (mod = Cmd on macOS, Ctrl elsewhere). Shortcuts
 * without a modifier are ignored while the user is typing, so pressing "n" in
 * the message box does not navigate away.
 */
export function useHotkeys(bindings: Record<string, Handler>, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    const onKeyDown = (event: KeyboardEvent) => {
      const parts: string[] = [];
      if (event.metaKey || event.ctrlKey) parts.push('mod');
      if (event.shiftKey) parts.push('shift');
      if (event.altKey) parts.push('alt');
      parts.push(event.key.toLowerCase());
      const combo = parts.join('+');
      const handler = bindings[combo];
      if (!handler) return;
      if (!combo.includes('mod') && isTypingTarget(event.target)) return;
      event.preventDefault();
      handler(event);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [bindings, enabled]);
}

export const MOD_LABEL =
  typeof navigator !== 'undefined' && /mac|iphone|ipad/i.test(navigator.platform ?? '')
    ? '⌘'
    : 'Ctrl';
