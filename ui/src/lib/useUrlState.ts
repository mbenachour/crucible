import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

/** Read/write a set of URL search params as plain strings. */
export function useUrlState() {
  const [sp, setSp] = useSearchParams();

  const get = useCallback((k: string, dflt = "") => sp.get(k) ?? dflt, [sp]);
  const getAll = useCallback((k: string) => sp.getAll(k), [sp]);
  const getNum = useCallback((k: string, dflt: number) => {
    const v = sp.get(k);
    const n = v == null ? NaN : Number(v);
    return Number.isFinite(n) ? n : dflt;
  }, [sp]);

  const set = useCallback(
    (patch: Record<string, string | number | string[] | undefined | null>) => {
      setSp((prev) => {
        const next = new URLSearchParams(prev);
        for (const [k, v] of Object.entries(patch)) {
          next.delete(k);
          if (v == null || v === "") continue;
          if (Array.isArray(v)) v.forEach((x) => next.append(k, String(x)));
          else next.set(k, String(v));
        }
        return next;
      });
    },
    [setSp],
  );

  return { get, getAll, getNum, set, sp };
}
