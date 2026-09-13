import { useEffect, useState } from "react";

/** Same `localStorage`-backed pattern for both the theme and the side nav's
 * collapsed state — read once at init, persist on change, swallow storage
 * errors (private-mode browsers). */
function persistedState(key: string, fallback: string): [string, (v: string) => void] {
  const [value, setValue] = useState<string>(() => {
    try {
      return localStorage.getItem(key) ?? fallback;
    } catch {
      return fallback;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* ignore */
    }
  }, [key, value]);
  return [value, setValue];
}

export function useTheme(): [string, (t: string) => void] {
  const [theme, setTheme] = persistedState("crucible.theme", "system");
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);
  return [theme, setTheme];
}

export function useSidenavCollapsed(): [boolean, (v: boolean) => void] {
  const [v, setV] = persistedState("crucible.sidenavCollapsed", "false");
  return [v === "true", (next) => setV(next ? "true" : "false")];
}
