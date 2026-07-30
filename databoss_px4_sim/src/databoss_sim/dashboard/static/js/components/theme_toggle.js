function currentTheme() {
  return document.documentElement.dataset.theme || "system";
}

function applyTheme(theme) {
  if (theme === "system") {
    delete document.documentElement.dataset.theme;
    localStorage.removeItem("databoss_theme");
  } else {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("databoss_theme", theme);
  }
}

function nextTheme(theme) {
  if (theme === "light") return "dark";
  if (theme === "dark") return "system";
  return "light";
}

function renderButton(button) {
  const theme = currentTheme();
  const label = theme === "light" ? "Light" : theme === "dark" ? "Dark" : "System";
  button.textContent = `Theme: ${label}`;
  button.setAttribute("aria-label", `Theme: ${theme}. Activate to switch theme.`);
  button.title = `Theme: ${theme}`;
}

export function mountThemeToggle(container) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "theme-toggle";
  button.addEventListener("click", () => {
    applyTheme(nextTheme(currentTheme()));
    renderButton(button);
  });
  renderButton(button);
  container.replaceChildren(button);
}
