// PHI PDF Studio · Settings: add OCR languages, keep AI keys in this browser.
(() => {
  "use strict";
  const root = document.querySelector("[data-settings]");
  if (!root) return;
  const $ = (sel, r = root) => r.querySelector(sel);
  const $$ = (sel, r = root) => Array.from(r.querySelectorAll(sel));
  const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const store = {
    get(k) { try { return localStorage.getItem(k) || ""; } catch (_) { return ""; } },
    set(k, v) { try { if (v) localStorage.setItem(k, v); else localStorage.removeItem(k); } catch (_) { /* private mode */ } },
  };

  const form = $("[data-add-language]");
  if (form) form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const code = (form.custom.value.trim() || form.code.value).toLowerCase();
    const error = $("[data-language-error]");
    error.hidden = true;
    if (!code) { error.textContent = "Choose a language or type its code."; error.hidden = false; return; }
    const button = $('button[type="submit"]', form);
    button.disabled = true;
    const label = button.lastChild.textContent;
    button.lastChild.textContent = " Downloading…";
    try {
      const res = await fetch("/api/ocr/languages", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
                                                       body: JSON.stringify({ code, quality: form.quality.value }) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `The server answered ${res.status}.`);
      location.reload();
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    } finally {
      button.disabled = false;
      button.lastChild.textContent = label;
    }
  });

  const keys = $$("[data-key-for]");
  keys.forEach((input) => { input.value = store.get(`phipdf-ai-key-${input.dataset.keyFor}`); });
  const save = $("[data-save-keys]");
  if (save) save.addEventListener("click", () => {
    keys.forEach((input) => store.set(`phipdf-ai-key-${input.dataset.keyFor}`, input.value.trim()));
    save.lastChild.textContent = " Saved";
    setTimeout(() => { save.lastChild.textContent = " Save in this browser"; }, 1500);
  });
  const forget = $("[data-forget-keys]");
  if (forget) forget.addEventListener("click", () => {
    keys.forEach((input) => { input.value = ""; store.set(`phipdf-ai-key-${input.dataset.keyFor}`, ""); });
  });
  $$("[data-show-pw]").forEach((b) => b.addEventListener("click", () => {
    const input = b.previousElementSibling;
    input.type = input.type === "password" ? "text" : "password";
    b.textContent = input.type === "password" ? "Show" : "Hide";
  }));
})();
