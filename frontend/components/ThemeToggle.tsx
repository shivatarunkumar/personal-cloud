"use client";

import { useState } from "react";
import { applyTheme, rememberTheme, type Theme } from "@/lib/theme";
import { MonitorIcon, MoonIcon, SunIcon } from "./icons";

const NEXT: Record<Theme, Theme> = { system: "light", light: "dark", dark: "system" };
const LABEL: Record<Theme, string> = { system: "Auto (follows your device)", light: "Light", dark: "Dark" };

/** One button cycling Auto → Light → Dark. The choice is a cookie, so the server renders it. */
export function ThemeToggle({ initial }: { initial: Theme }) {
  const [theme, setTheme] = useState<Theme>(initial);
  const Icon = theme === "light" ? SunIcon : theme === "dark" ? MoonIcon : MonitorIcon;
  return (
    <button
      type="button"
      className="rounded-lg p-2 text-muted hover:bg-surface-hover hover:text-fg"
      title={`Theme: ${LABEL[theme]}`}
      aria-label={`Theme: ${LABEL[theme]}. Change`}
      onClick={() => {
        const next = NEXT[theme];
        setTheme(next);
        rememberTheme(next);
        applyTheme(next);
      }}
    >
      <Icon />
    </button>
  );
}
