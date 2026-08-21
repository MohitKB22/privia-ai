import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiRequestError } from '@/lib/api';

export interface AsyncState<T> {
  data: T | null;
  error: ApiRequestError | null;
  loading: boolean;
  reload: () => void;
}

/** Run an async loader on mount and whenever `deps` change. */
export function useAsync<T>(loader: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiRequestError | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loader()
      .then((value) => {
        if (!cancelled && mounted.current) {
          setData(value);
          setError(null);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled && mounted.current) {
          setError(
            caught instanceof ApiRequestError
              ? caught
              : new ApiRequestError('INTERNAL_ERROR', String(caught), 0),
          );
        }
      })
      .finally(() => {
        if (!cancelled && mounted.current) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, reload };
}

/** Poll a loader on an interval, pausing while the tab is hidden. */
export function usePolling<T>(loader: () => Promise<T>, intervalMs: number): AsyncState<T> {
  const state = useAsync(loader, []);
  const reloadRef = useRef(state.reload);
  reloadRef.current = state.reload;

  useEffect(() => {
    const tick = () => {
      if (document.visibilityState === 'visible') reloadRef.current();
    };
    const timer = window.setInterval(tick, intervalMs);
    document.addEventListener('visibilitychange', tick);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', tick);
    };
  }, [intervalMs]);

  return state;
}
