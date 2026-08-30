import { useCallback, useEffect, useRef, useState } from "react";

export function usePolling<T>(loader: () => Promise<T>, intervalMs = 0, dependencies: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const mounted = useRef(true);
  const refresh = useCallback(async () => {
    try {
      const next = await loader();
      if (mounted.current) { setData(next); setError(""); }
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (mounted.current) setLoading(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    const timer = intervalMs ? window.setInterval(() => void refresh(), intervalMs) : 0;
    const changed = () => void refresh();
    window.addEventListener("hive:data-changed", changed);
    return () => {
      mounted.current = false;
      if (timer) window.clearInterval(timer);
      window.removeEventListener("hive:data-changed", changed);
    };
  }, [refresh, intervalMs]);

  return { data, error, loading, refresh, setData };
}

export function useNow(intervalMs = 30_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}
