/** Manual theme toggle stamping data-theme on <html>; defaults to OS scheme. */

export type ThemeChoice = "light" | "dark" | "auto";

const STORAGE_KEY = "wsp-theme";

export function getThemeChoice(): ThemeChoice {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    /* storage unavailable */
  }
  return "auto";
}

export function effectiveTheme(choice: ThemeChoice = getThemeChoice()): "light" | "dark" {
  if (choice !== "auto") return choice;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function setTheme(choice: ThemeChoice): void {
  try {
    if (choice === "auto") localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, choice);
  } catch {
    /* storage unavailable */
  }
  const root = document.documentElement;
  if (choice === "auto") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
}

/** Flip to the opposite of what is currently rendered. */
export function toggleTheme(): "light" | "dark" {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  setTheme(next);
  return next;
}
