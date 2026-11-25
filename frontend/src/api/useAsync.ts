import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "./client";

export interface AsyncState<T> { data: T | null; error: string | null; loading: boolean; reload: () => void }

/** Loads data on mount / dependency change and optionally polls. Keeps previous data while refreshing. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = [], pollMs = 0): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const alive = useRef(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const run = useCallback(async () => {
    try {
      const result = await fnRef.current();
      if (!alive.current) return;
      setData(result);
      setError(null);
    } catch (e) {
      if (alive.current) setError(errorMessage(e));
    } finally {
      if (alive.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    setLoading(true);
    run();
    const t = pollMs > 0 ? setInterval(run, pollMs) : undefined;
    return () => { alive.current = false; if (t) clearInterval(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, pollMs, ...deps]);

  return { data, error, loading, reload: run };
}
