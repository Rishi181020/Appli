import { useEffect, useState } from "react";

export type Theme = "light" | "dark";

function initial(): Theme {
  try {
    const saved = localStorage.getItem("appli-theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* storage blocked: fall through */
  }
  return "light"; // the app is designed light-first
}

/** Light / dark theme, remembered per browser. index.html applies the saved one before first paint. */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(initial);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#0b0d0d" : "#f4f5f6");
    try {
      localStorage.setItem("appli-theme", theme);
    } catch {
      /* not remembered: fine */
    }
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))];
}
