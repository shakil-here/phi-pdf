// PHI PDF Studio · theme switch and the phone menu. No dependencies; every page works without it.
(() => {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const store = {
    get(key) { try { return localStorage.getItem(key); } catch (_) { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch (_) { /* private mode */ } },
  };

  const savedTheme = store.get("phipdf-theme");
  if (savedTheme) document.documentElement.dataset.theme = savedTheme;
  $$("[data-theme-toggle]").forEach((btn) => btn.addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("phipdf-theme", next);
  }));

  $$("[data-menu]").forEach((btn) => btn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    $("#sidebar").classList.toggle("is-open");
  }));
  document.addEventListener("click", (ev) => {
    const sidebar = $("#sidebar");
    if (sidebar && sidebar.classList.contains("is-open") && !sidebar.contains(ev.target)) sidebar.classList.remove("is-open");
  });
  document.addEventListener("keydown", (ev) => {
    const sidebar = $("#sidebar");
    if (ev.key === "Escape" && sidebar && sidebar.classList.contains("is-open")) sidebar.classList.remove("is-open");
  });

  // copy buttons next to code (Developers page, "use from code" panels)
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-copy]");
    if (!btn) return;
    const target = document.getElementById(btn.dataset.copy);
    if (!target) return;
    try {
      await navigator.clipboard.writeText(target.textContent);
      const before = btn.textContent;
      btn.textContent = "Copied";
      setTimeout(() => { btn.textContent = before; }, 1400);
    } catch (_) { /* clipboard blocked: the text can still be selected */ }
  });
})();
