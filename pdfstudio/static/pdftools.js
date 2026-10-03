// PHI PDF Studio · the hub, My files, each tool's page and a run's result.
// No dependencies; strict CSP (no inline styles or scripts: styles go through el.style). Shared helpers are put on
// window.PDFT for pdforganize.js and pdfedit.js.
(() => {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const csrf = () => ($('meta[name="csrf-token"]') || {}).content || "";
  const readJSON = (id, fallback) => {
    const node = document.getElementById(id);
    if (!node) return fallback;
    try { return JSON.parse(node.textContent); } catch (_) { return fallback; }
  };
  const TOOLS = readJSON("pdf-tools-data", {});
  const GROUP_OF = Object.fromEntries(Object.entries(TOOLS).map(([id, t]) => [id, t.group]));

  // ------------------------------------------------------------------ small helpers
  function el(tag, attrs = {}, ...kids) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) node.append(kid);
    return node;
  }
  function iconOf(name) {
    const holder = $(`#pdf-icons`);
    const found = holder && holder.content.querySelector(`[data-icon="${name}"] svg`);
    return found ? found.cloneNode(true) : el("span");
  }
  function size(num) {
    let value = Number(num) || 0;
    for (const unit of ["B", "KB", "MB", "GB"]) {
      if (value < 1024 || unit === "GB") return unit === "B" ? `${value.toFixed(0)} B` : `${value.toFixed(1)} ${unit}`;
      value /= 1024;
    }
    return "";
  }
  function when(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    const today = new Date();
    const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (d.toDateString() === today.toDateString()) return `today ${time}`;
    return d.toLocaleDateString([], { day: "numeric", month: "short" }) + ` ${time}`;
  }
  const dataUrl = (blob) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
  async function api(url, body, method = "POST") {
    const options = { method, headers: { "X-CSRF-Token": csrf() } };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const res = await fetch(url, options);
    let data = {};
    try { data = await res.json(); } catch (_) { /* not JSON */ }
    if (!res.ok) throw new Error(data.error || (res.status === 413 ? "That is too large to send." : `The server answered ${res.status}.`));
    return data;
  }
  let toastTimer = 0;
  function toast(message, kind = "", link = null) {
    $$(".pdf-toast").forEach((t) => t.remove());
    const box = el("div", { class: `pdf-toast${kind === "error" ? " is-error" : ""}`, role: "status" }, el("span", { text: message }));
    if (link) box.append(el("a", { href: link.href, text: link.text, target: link.target || null }));
    document.body.append(box);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => box.remove(), kind === "error" ? 7000 : 4500);
  }
  function fileMeta(f) {
    if (f.kind === "office" || f.kind === "text") return `${(f.ext || f.kind).toUpperCase()} · ${size(f.size)}`;
    if (f.kind === "pdf") {
      if (f.locked) return "locked with a password";
      if (f.broken) return "damaged";
      return `${f.pages} page${f.pages === 1 ? "" : "s"} · ${size(f.size)}${f.paper ? " · " + f.paper : ""}`;
    }
    if (f.kind === "image") return `${f.width || "?"}×${f.height || "?"} px · ${size(f.size)}`;
    return size(f.size);
  }
  const KINDS_OF = { pdf: ["pdf"], image: ["image", "pdf"], any: ["pdf", "image"], office: ["office"], text: ["text"] };
  const accepts = (tool, kind) => {
    const t = TOOLS[tool];
    return !!t && (KINDS_OF[t.accepts] || ["pdf"]).includes(kind);
  };
  const kindIcon = (f) => (f.locked ? "lock" : f.kind === "zip" ? "archive" : f.kind === "office" ? (/xls|ods|csv/.test(f.ext) ? "table" : /ppt|odp/.test(f.ext) ? "slides" : "word") : f.kind === "text" ? "doc" : "pdf");
  const toolHref = (tool, ids) => `/tools/${tool}?files=${ids.join(",")}`;

  // Upload with progress (XHR: fetch has no upload progress). Resolves {files, errors}.
  function upload(files, status) {
    return new Promise((resolve, reject) => {
      const body = new FormData();
      Array.from(files).forEach((f) => body.append("files", f));
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/files");
      xhr.setRequestHeader("X-CSRF-Token", csrf());
      const total = Array.from(files).reduce((n, f) => n + f.size, 0);
      let bar = null;
      if (status) {
        status.hidden = false;
        status.replaceChildren();
        const fill = el("div", { class: "progress-fill" });
        bar = fill;
        status.append(el("div", { class: "pdf-uploading" },
          el("span", { text: `Uploading ${files.length} file${files.length === 1 ? "" : "s"} (${size(total)})…` }),
          el("span", { class: "muted small", "data-pct": "", text: "0%" }),
          el("div", { class: "progress is-active" }, fill)));
      }
      xhr.upload.onprogress = (ev) => {
        if (!bar || !ev.lengthComputable) return;
        const pct = Math.round((100 * ev.loaded) / ev.total);
        bar.style.width = pct + "%";
        const label = $("[data-pct]", status);
        if (label) label.textContent = pct < 100 ? `${pct}%` : "reading the files…";
      };
      xhr.onload = () => {
        if (status) { status.hidden = true; status.replaceChildren(); }
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (_) { /* not JSON */ }
        if (xhr.status >= 400) reject(new Error(data.error || (xhr.status === 413 ? "A file is larger than the upload limit." : `Upload failed (${xhr.status}).`)));
        else resolve({ files: data.files || [], errors: data.errors || [] });
      };
      xhr.onerror = () => { if (status) status.hidden = true; reject(new Error("The upload was cut off. Check the connection and try again.")); };
      xhr.send(body);
    });
  }
  function wireDrop(zone, onFiles) {
    if (!zone) return;
    const input = $('input[type="file"]', zone);
    ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (ev) => { ev.preventDefault(); zone.classList.add("is-over"); }));
    ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, () => zone.classList.remove("is-over")));
    zone.addEventListener("drop", (ev) => {
      ev.preventDefault();
      if (ev.dataTransfer && ev.dataTransfer.files.length) onFiles(ev.dataTransfer.files);
    });
    if (input) input.addEventListener("change", () => { if (input.files.length) onFiles(input.files); input.value = ""; });
  }

  // ------------------------------------------------------------------ "use in" menu
  function toolMenu(ids, kind, { up = false, label = "Use in", exclude = "" } = {}) {
    const menu = el("details", { class: "pdf-menu" });
    const list = el("div", { class: `pdf-menu-list${up ? " is-up" : ""}` });
    menu.append(el("summary", { class: "btn btn-ghost btn-sm", title: "Send to a tool" }, iconOf("grid"), label), list);
    menu.addEventListener("toggle", () => {
      if (!menu.open) return;
      $$(".pdf-menu[open]").forEach((m) => { if (m !== menu) m.open = false; });
      if (list.childElementCount) return;
      let group = "";
      for (const [id, t] of Object.entries(TOOLS)) {
        if (id === exclude || (kind && !accepts(id, kind))) continue;
        if (t.group !== group) { group = t.group; list.append(el("div", { class: "pdf-menu-head", text: groupLabel(group) })); }
        list.append(el("a", { href: toolHref(id, typeof ids === "function" ? ids() : ids), class: `pdf-g-${t.group}` }, iconOf(t.icon), t.label));
      }
    });
    return menu;
  }
  const GROUP_LABELS = { organize: "Organize", optimize: "Optimize & OCR", convert: "Convert", edit: "Edit & stamp", security: "Security & privacy" };
  const groupLabel = (g) => GROUP_LABELS[g] || g;
  document.addEventListener("click", (ev) => {
    $$(".pdf-menu[open]").forEach((m) => { if (!m.contains(ev.target)) m.open = false; });
  });

  // ------------------------------------------------------------------ file tiles + selection (hub, files page)
  const selected = new Set();
  const known = new Map();
  function fileTile(f, { selectable = true, actions = true } = {}) {
    known.set(f.id, f);
    const tick = el("input", { type: "checkbox", "aria-label": `Tick ${f.name}` });
    tick.checked = selected.has(f.id);
    const thumb = el("div", { class: "pdf-file-thumb", title: selectable ? "Click to tick" : f.name },
      f.thumb ? el("img", { src: f.thumb, alt: "", loading: "lazy" }) : iconOf(kindIcon(f)),
      el("span", { class: "pdf-file-kind", text: f.kind === "pdf" || f.kind === "zip" ? f.kind : (f.ext || f.kind) }));
    const card = el("article", { class: `pdf-file${selected.has(f.id) ? " is-selected" : ""}`, dataset: { id: f.id } }, thumb);
    if (selectable) {
      const tickWrap = el("label", { class: "pdf-file-tick" }, tick);
      thumb.append(tickWrap);
      const toggle = (on) => {
        if (on) selected.add(f.id); else selected.delete(f.id);
        tick.checked = on;
        card.classList.toggle("is-selected", on);
        updateSelbar();
      };
      tick.addEventListener("change", () => toggle(tick.checked));
      tickWrap.addEventListener("click", (ev) => ev.stopPropagation());
      thumb.addEventListener("click", () => toggle(!selected.has(f.id)));
      card._toggle = toggle;
    }
    const star = el("button", { type: "button", class: `pdf-file-star${f.starred ? " is-on" : ""}`, title: f.starred ? "Starred: kept by clean-ups" : "Star (keep)", "aria-pressed": String(!!f.starred) }, iconOf("star"));
    star.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      try {
        const data = await api(`/api/files/${f.id}`, { starred: !f.starred });
        f.starred = data.file.starred;
        star.classList.toggle("is-on", f.starred);
        star.setAttribute("aria-pressed", String(f.starred));
      } catch (err) { toast(err.message, "error"); }
    });
    card.append(star);
    const name = el("div", { class: "pdf-file-name", title: f.name, text: f.name });
    const body = el("div", { class: "pdf-file-body" }, name,
      el("div", { class: "pdf-file-meta", text: fileMeta(f) }),
      el("div", { class: "pdf-file-meta", text: `${f.origin_label} · ${when(f.created_at)}` }));
    if (f.locked) body.append(el("div", { class: "pdf-file-meta pdf-lock", text: "Locked: use Unlock PDF" }));
    if (actions) {
      const acts = el("div", { class: "pdf-file-actions" },
        el("a", { class: "btn btn-secondary btn-sm", href: `/files/${f.id}/download`, title: "Download" }, iconOf("download")),
        ["pdf", "image", "text"].includes(f.kind) ? el("a", { class: "btn btn-ghost btn-sm", href: `/files/${f.id}/download?inline=1`, target: "_blank", rel: "noopener", title: "Open in a new tab" }, iconOf("eye")) : null);
      if (f.kind === "pdf" && !f.locked) acts.append(el("a", { class: "btn btn-ghost btn-sm", href: `/edit/${f.id}`, title: "Edit" }, iconOf("edit")));
      if (f.kind !== "zip") acts.append(toolMenu([f.id], f.locked ? "" : f.kind, { label: "" }));
      const more = el("details", { class: "pdf-menu" });
      const list = el("div", { class: "pdf-menu-list" },
        el("button", { type: "button", onclick: () => rename(f, name) }, iconOf("edit"), "Rename"),
        el("button", { type: "button", onclick: () => removeFiles([f.id]) }, iconOf("trash"), "Delete"));
      more.append(el("summary", { class: "btn btn-ghost btn-sm", title: "More", text: "⋯" }), list);
      acts.append(more);
      body.append(acts);
    }
    card.append(body);
    return card;
  }
  async function rename(f, nameNode) {
    const next = window.prompt("New name", f.name);
    if (!next || next === f.name) return;
    try {
      const data = await api(`/api/files/${f.id}`, { name: next });
      f.name = data.file.name;
      nameNode.textContent = f.name;
      nameNode.title = f.name;
    } catch (err) { toast(err.message, "error"); }
  }
  async function removeFiles(ids) {
    if (!ids.length) return;
    const names = ids.map((id) => (known.get(id) || {}).name).filter(Boolean);
    if (!window.confirm(`Delete ${ids.length === 1 ? `“${names[0]}”` : `${ids.length} files`} from My files? This cannot be undone.`)) return;
    try {
      const data = await api("/api/delete", { ids });
      ids.forEach((id) => { selected.delete(id); $$(`.pdf-file[data-id="${id}"]`).forEach((n) => n.remove()); });
      updateSelbar();
      toast(`${data.removed} file(s) deleted.`);
    } catch (err) { toast(err.message, "error"); }
  }
  function updateSelbar() {
    const bar = $("[data-selbar]");
    if (!bar) return;
    const ids = Array.from(selected);
    bar.hidden = !ids.length;
    if (!ids.length) return;
    const kinds = new Set(ids.map((id) => (known.get(id) || {}).kind));
    $("[data-sel-count]", bar).textContent = `${ids.length} selected`;
    $$("[data-sel-tool]", bar).forEach((a) => {
      const tool = a.dataset.selTool;
      const ok = Array.from(kinds).every((k) => accepts(tool, k)) && ids.length >= (TOOLS[tool] || {}).min && ids.length <= (TOOLS[tool] || {}).max;
      a.hidden = !ok;
      a.href = toolHref(tool, ids);
    });
    const menu = $("[data-sel-menu]", bar);
    menu.replaceChildren();
    let group = "";
    for (const [id, t] of Object.entries(TOOLS)) {
      if (!Array.from(kinds).every((k) => accepts(id, k))) continue;
      if (ids.length > t.max && t.max === 1) continue;
      if (t.group !== group) { group = t.group; menu.append(el("div", { class: "pdf-menu-head", text: groupLabel(group) })); }
      menu.append(el("a", { href: toolHref(id, ids), class: `pdf-g-${t.group}` }, iconOf(t.icon), t.label));
    }
    $("[data-sel-zip]", bar).href = `/zip?ids=${ids.join(",")}`;
  }
  function wireSelbar() {
    const bar = $("[data-selbar]");
    if (!bar) return;
    $("[data-sel-delete]", bar).addEventListener("click", () => removeFiles(Array.from(selected)));
    $("[data-sel-clear]", bar).addEventListener("click", () => {
      Array.from(selected).forEach((id) => { const card = $(`.pdf-file[data-id="${id}"]`); if (card && card._toggle) card._toggle(false); });
      selected.clear();
      updateSelbar();
    });
  }

  // ------------------------------------------------------------------ hub + My files
  function initList(page) {
    const grid = $("[data-file-grid]");
    const files = readJSON("pdf-files-data", []);
    if (grid) files.forEach((f) => grid.append(fileTile(f)));
    wireSelbar();
    const status = $("[data-upload-status]");
    const added = (list, errors = []) => {
      errors.forEach((e) => toast(e, "error"));
      if (!list.length) return;
      if (page === "home") {
        const box = $("[data-new-files]");
        const newGrid = $("[data-new-list]");
        box.hidden = false;
        list.forEach((f) => { selected.add(f.id); newGrid.prepend(fileTile(f)); });
        box.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } else if (grid) {
        $$(".pdf-empty").forEach((n) => n.remove());
        list.forEach((f) => { selected.add(f.id); grid.prepend(fileTile(f)); });
      }
      updateSelbar();
      toast(`${list.length} file${list.length === 1 ? "" : "s"} added and ticked: choose a tool in the bar below.`);
    };
    const send = async (fileList) => {
      try { const r = await upload(fileList, status); added(r.files, r.errors); } catch (err) { toast(err.message, "error"); }
    };
    wireDrop($("[data-pdf-drop]"), send);
    $$("[data-pdf-upload]").forEach((input) => input.addEventListener("change", () => { if (input.files.length) send(input.files); input.value = ""; }));
    const all = $("[data-select-all]");
    if (all) all.addEventListener("click", () => { $$(".pdf-file", grid).forEach((card) => card._toggle && card._toggle(true)); });
    const everything = $("[data-delete-all]");
    if (everything) everything.addEventListener("click", async () => {
      if (!window.confirm("Delete ALL your files, starred ones too? This cannot be undone.")) return;
      try {
        const data = await api("/api/delete", { everything: true });
        toast(`${data.removed} file(s) deleted.`);
        setTimeout(() => location.reload(), 700);
      } catch (err) { toast(err.message, "error"); }
    });
    $$("[data-cleanup]").forEach((b) => b.addEventListener("click", async () => {
      const days = Number(b.dataset.cleanup);
      if (!window.confirm(`Delete every file older than ${days} days that is not starred?`)) return;
      try {
        const data = await api("/api/delete", { older_than: days });
        toast(`${data.removed} old file(s) deleted.`);
        setTimeout(() => location.reload(), 700);
      } catch (err) { toast(err.message, "error"); }
    }));
    // a tool card clicked with files ticked takes them along
    $$("[data-tool-link]").forEach((a) => a.addEventListener("click", (ev) => {
      if (!selected.size) return;
      const ids = Array.from(selected).filter((id) => accepts(a.dataset.toolLink, (known.get(id) || {}).kind));
      if (ids.length) { ev.preventDefault(); location.href = toolHref(a.dataset.toolLink, ids); }
    }));
  }

  // ------------------------------------------------------------------ picker dialog
  function openPicker({ kinds, multiple, exclude = [], onPick }) {
    const dialog = $("#pdf-picker");
    if (!dialog) return;
    const list = $("[data-picker-list]", dialog);
    const q = $("[data-picker-q]", dialog);
    const kindSel = $("[data-picker-kind]", dialog);
    const count = $("[data-picker-count]", dialog);
    const picked = new Map();
    kindSel.hidden = kinds.length === 1;
    const refresh = async () => {
      const kind = kinds.length === 1 ? kinds[0] : kindSel.value;
      list.replaceChildren(el("div", { class: "muted small", text: "Loading…" }));
      let files = [];
      try { files = (await api(`/api/files?kind=${kind}&q=${encodeURIComponent(q.value)}`, undefined, "GET")).files; } catch (err) { toast(err.message, "error"); }
      files = files.filter((f) => kinds.includes(f.kind) && !exclude.includes(f.id));
      list.replaceChildren();
      if (!files.length) list.append(el("p", { class: "pdf-empty", text: "No files to choose. Upload them on the page instead." }));
      files.forEach((f) => {
        const card = fileTile(f, { selectable: false, actions: false });
        const mark = () => {
          card.classList.toggle("is-selected", picked.has(f.id));
          count.textContent = picked.size ? `${picked.size} ticked` : "Nothing ticked";
        };
        card.addEventListener("click", () => {
          if (!multiple) picked.clear();
          if (picked.has(f.id)) picked.delete(f.id); else picked.set(f.id, f);
          $$(".pdf-file", list).forEach((c) => c.classList.toggle("is-selected", picked.has(c.dataset.id)));
          mark();
          if (!multiple && picked.size) finish();
        });
        const star = $(".pdf-file-star", card);
        if (star) star.remove();
        list.append(card);
      });
    };
    const finish = () => { dialog.close(); onPick(Array.from(picked.values())); };
    $("[data-picker-ok]", dialog).onclick = finish;
    $$("[data-picker-close]", dialog).forEach((b) => { b.onclick = () => dialog.close(); });
    let timer = 0;
    q.oninput = () => { clearTimeout(timer); timer = setTimeout(refresh, 250); };
    kindSel.onchange = refresh;
    q.value = "";
    count.textContent = "Nothing ticked";
    $("[data-picker-ok]", dialog).hidden = !multiple;
    dialog.showModal();
    refresh();
  }

  // ------------------------------------------------------------------ results (tool page + run page)
  function resultCard(box, run, { onAgain } = {}) {
    box.hidden = false;
    box.classList.toggle("is-failed", run.status !== "done");
    box.replaceChildren();
    if (run.status !== "done") {
      box.append(el("div", { class: "pdf-result-head" },
        el("span", { class: "pdf-result-mark" }, iconOf("x")),
        el("div", {}, el("h2", { text: run.status === "interrupted" ? "Stopped" : "Could not finish" }), el("p", { text: run.error || "Something went wrong." }))));
      if (onAgain) box.append(el("div", { class: "pdf-result-foot" }, el("button", { type: "button", class: "btn btn-secondary btn-sm", onclick: onAgain }, iconOf("refresh"), "Change the options")));
      return;
    }
    box.append(el("div", { class: "pdf-result-head" },
      el("span", { class: "pdf-result-mark" }, iconOf("check")),
      el("div", {}, el("h2", { text: `Done in ${run.seconds < 1 ? "under a second" : run.seconds.toFixed(1) + " s"}` }), el("p", { text: run.summary }))));
    if (run.tool === "compress" && run.stats && run.stats.saved_pct !== undefined) {
      box.append(el("div", { class: "pdf-big-stat" }, el("strong", { text: `−${Math.round(run.stats.saved_pct)}%` }),
        el("span", { class: "muted", text: `${size(run.stats.before)} → ${size(run.stats.after)}` })));
    }
    const outs = el("ul", { class: "pdf-outs" });
    (run.outputs || []).forEach((f) => {
      const acts = el("div", { class: "pdf-out-acts" },
        el("a", { class: "btn btn-primary btn-sm", href: `/files/${f.id}/download` }, iconOf("download"), "Download"),
        ["pdf", "image", "text"].includes(f.kind) ? el("a", { class: "btn btn-ghost btn-sm", href: `/files/${f.id}/download?inline=1`, target: "_blank", rel: "noopener" }, iconOf("eye"), "Open") : null);
      if (f.kind === "pdf" && !f.locked) acts.append(el("a", { class: "btn btn-ghost btn-sm", href: `/edit/${f.id}` }, iconOf("edit"), "Edit"));
      if (f.kind !== "zip" && !f.locked) acts.append(toolMenu([f.id], f.kind, { label: "Continue", exclude: "" }));
      outs.append(el("li", {},
        f.thumb ? el("img", { src: f.thumb, alt: "" }) : el("span", { class: "pdf-chosen-ph" }, iconOf(kindIcon(f))),
        el("div", { class: "pdf-out-text" }, el("strong", { text: f.name }), el("small", { text: fileMeta(f) }), acts)));
    });
    box.append(outs);
    const foot = el("div", { class: "pdf-result-foot" });
    const ids = (run.outputs || []).map((f) => f.id);
    if (ids.length > 1) foot.append(el("a", { class: "btn btn-secondary btn-sm", href: `/zip?ids=${ids.join(",")}&name=${encodeURIComponent(run.label)}` }, iconOf("download"), `All ${ids.length} as ZIP`));
    if (onAgain) foot.append(el("button", { type: "button", class: "btn btn-ghost btn-sm", onclick: onAgain }, iconOf("refresh"), "Again with other options"));
    foot.append(el("a", { class: "btn btn-ghost btn-sm", href: "/files" }, iconOf("folder"), "My files"));
    box.append(foot);
  }
  async function poll(runId, onTick) {
    for (;;) {
      const run = await api(`/api/runs/${runId}`, undefined, "GET");
      if (run.status !== "running") return run;
      onTick(run);
      await new Promise((r) => setTimeout(r, 650));
    }
  }

  // ------------------------------------------------------------------ tool page
  function initTool(root) {
    const tool = root.dataset.tool;
    const meta = TOOLS[tool] || {};
    const max = Number(root.dataset.max) || 1;
    const kinds = KINDS_OF[root.dataset.accepts] || ["pdf"];
    const form = $("[data-options]", root);
    const list = $("[data-chosen]", root);
    const dropEmpty = $("[data-chosen-empty]", root);
    const moreUpload = $("[data-more-upload]", root);
    const status = $("[data-upload-status]", root);
    const result = $("[data-result]", root);
    let chosen = readJSON("pdf-chosen-data", []).filter((f) => kinds.includes(f.kind));
    const ranges = new Map();

    // -------- chosen files
    const renderChosen = () => {
      list.replaceChildren();
      chosen.forEach((f, i) => {
        const li = el("li", { draggable: max > 1 ? "true" : null, class: max > 1 ? null : "is-single", dataset: { id: f.id } },
          max > 1 ? el("span", { class: "pdf-n", text: String(i + 1), title: "Drag to move" }) : null,
          f.thumb ? el("img", { src: f.thumb, alt: "" }) : el("span", { class: "pdf-chosen-ph" }, iconOf(kindIcon(f))));
        const text = el("div", { class: "pdf-chosen-text" }, el("strong", { text: f.name }), el("small", { text: fileMeta(f) }));
        if (tool === "merge" && f.kind === "pdf" && f.pages > 1) {
          const input = el("input", { type: "text", placeholder: `pages: all ${f.pages}`, value: ranges.get(f.id) || "", "aria-label": `Pages of ${f.name}` });
          input.addEventListener("input", () => ranges.set(f.id, input.value));
          text.append(input);
        }
        if (f.locked && tool !== "unlock") text.append(el("small", { class: "pdf-lock", text: "Locked: unlock it first" }));
        const tools = el("div", { class: "pdf-chosen-tools" });
        if (max > 1) {
          tools.append(el("button", { type: "button", class: "btn btn-ghost btn-sm btn-icon", title: "Move up", "aria-label": "Move up", disabled: i === 0, onclick: () => move(i, i - 1), text: "↑" }),
            el("button", { type: "button", class: "btn btn-ghost btn-sm btn-icon", title: "Move down", "aria-label": "Move down", disabled: i === chosen.length - 1, onclick: () => move(i, i + 1), text: "↓" }));
        }
        tools.append(el("button", { type: "button", class: "btn btn-ghost btn-sm btn-icon", title: "Take out", "aria-label": `Take ${f.name} out`, onclick: () => { chosen.splice(i, 1); changed(); } }, iconOf("x")));
        li.append(text, tools);
        if (max > 1) wireDrag(li);
        list.append(li);
      });
      dropEmpty.hidden = chosen.length >= max || (max > 1 && chosen.length > 0);
      if (moreUpload) moreUpload.hidden = !(max > 1 && chosen.length > 0 && chosen.length < max);
      const ids = chosen.map((f) => f.id).join(",");
      history.replaceState(null, "", ids ? `?files=${ids}` : location.pathname);
    };
    let dragFrom = -1;
    const wireDrag = (li) => {
      li.addEventListener("dragstart", (ev) => { dragFrom = Array.from(list.children).indexOf(li); li.classList.add("is-dragging"); ev.dataTransfer.effectAllowed = "move"; ev.dataTransfer.setData("text/plain", li.dataset.id); });
      li.addEventListener("dragend", () => { li.classList.remove("is-dragging"); $$(".is-drop", list).forEach((n) => n.classList.remove("is-drop")); });
      li.addEventListener("dragover", (ev) => { if (dragFrom < 0) return; ev.preventDefault(); li.classList.add("is-drop"); });
      li.addEventListener("dragleave", () => li.classList.remove("is-drop"));
      li.addEventListener("drop", (ev) => { ev.preventDefault(); const to = Array.from(list.children).indexOf(li); if (dragFrom >= 0 && to >= 0) move(dragFrom, to); dragFrom = -1; });
    };
    const move = (from, to) => {
      if (to < 0 || to >= chosen.length) return;
      const [f] = chosen.splice(from, 1);
      chosen.splice(to, 0, f);
      changed();
    };
    const add = (files) => {
      const fresh = files.filter((f) => {
        if (!kinds.includes(f.kind)) { toast(`${f.name}: ${{ pdf: "this tool takes PDFs", office: "this tool takes Word, Excel or PowerPoint files", text: "this tool takes text, Markdown or HTML files" }[meta.accepts] || "not a PDF or a picture"}.`, "error"); return false; }
        return !chosen.some((c) => c.id === f.id);
      });
      if (!fresh.length) return;
      chosen = max === 1 ? [fresh[fresh.length - 1]] : chosen.concat(fresh).slice(0, max);
      changed();
    };
    const send = async (files) => {
      try { const r = await upload(files, status); r.errors.forEach((e) => toast(e, "error")); add(r.files); } catch (err) { toast(err.message, "error"); }
    };
    wireDrop(dropEmpty, send);
    $$("[data-pdf-upload]", root).forEach((input) => input.addEventListener("change", () => { if (input.files.length) send(input.files); input.value = ""; }));
    $("[data-open-picker]", root).addEventListener("click", () => openPicker({ kinds, multiple: max > 1, exclude: chosen.map((f) => f.id), onPick: add }));

    // -------- options: show/hide, values
    const valueOf = (name) => {
      const inputs = $$(`[name="${name}"]`, form);
      if (!inputs.length) return "";
      const radio = inputs.find((i) => i.type === "radio" && i.checked);
      if (radio) return radio.value;
      if (inputs[0].type === "checkbox") return inputs[0].checked ? "on" : "";
      return inputs[0].value;
    };
    const applyWhen = () => {
      $$("[data-when]", form).forEach((node) => {
        const rule = node.dataset.when;
        const neg = rule.includes("!=");
        const [name, values] = rule.split(neg ? "!=" : "=");
        const hit = values.split("|").includes(valueOf(name));
        node.hidden = neg ? hit : !hit;
      });
      $$('.pdf-range input[type="range"]', form).forEach((r) => { r.nextElementSibling.textContent = r.value + (r.dataset.unit || ""); });
    };
    const params = () => {
      const out = {};
      const multi = {};
      $$("[name]", form).forEach((input) => {
        if (input.dataset.noSend !== undefined || input.closest("[hidden]")) return;
        const name = input.name;
        if (input.type === "radio") { if (input.checked) out[name] = input.value; return; }
        if (input.type === "checkbox") {
          if (input.hasAttribute("value") && input.value !== "on") { multi[name] = multi[name] || []; if (input.checked) multi[name].push(input.value); }
          else out[name] = input.checked;
          return;
        }
        out[name] = input.value;
      });
      Object.assign(out, multi);
      if (tool === "merge") out.ranges = chosen.map((f) => ranges.get(f.id) || "");
      if (tool === "ocr" && out.engine === "ai") {
        out.output = out.output_ai || "md";
        ["layout", "clean", "deskew", "rotate", "mode", "keep", "dpi", "language"].forEach((k) => delete out[k]);
      }
      delete out.output_ai;
      return out;
    };
    const SECRET = { password: "YOUR-PASSWORD", owner_password: "PERMISSIONS-PASSWORD", ai_key: "YOUR-API-KEY", password2: null };
    const shellQuote = (v) => `'${String(v).replace(/'/g, "'\\''")}'`;
    const showCode = () => {
      const curlBox = $("[data-code-curl]", root);
      const cliBox = $("[data-code-cli]", root);
      if (!curlBox) return;
      const card = $("[data-code-card]", root);
      if (meta.page === "edit" || meta.page === "organize") { card.hidden = true; return; }
      const p = params();
      Object.keys(p).forEach((k) => {
        if (k in SECRET) { if (SECRET[k] === null || !p[k]) delete p[k]; else p[k] = SECRET[k]; }
        if (p[k] === "" || p[k] === false) delete p[k];
        if (Array.isArray(p[k]) && !p[k].some((x) => x)) delete p[k];
      });
      const names = chosen.length ? chosen.map((f) => f.name) : [meta.accepts === "image" ? "photo.jpg" : meta.accepts === "office" ? "report.docx" : meta.accepts === "text" ? "notes.md" : "input.pdf"];
      const extra = tool === "watermark" && p.kind === "image" ? ["image_file=@logo.png"] : tool === "insert" && p.what === "file" ? ["source_file=@other.pdf"] : [];
      delete p.image_file; delete p.source_file;
      const base = `${location.origin}/api/v1/tools/${tool}`;
      curlBox.textContent = ["curl -f -o result" + (names.length > 1 || ["split", "pdf2img", "extract_images"].includes(tool) ? ".zip" : ""), ...names.map((n) => `  -F files=@${shellQuote(n)}`),
        ...extra.map((x) => `  -F ${x}`), Object.keys(p).length ? `  -F params=${shellQuote(JSON.stringify(p))}` : null, `  ${base}`].filter(Boolean).join(" \\\n");
      const sets = Object.entries(p).map(([k, v]) => `--set ${k}=${shellQuote(Array.isArray(v) ? v.join("+") : v === true ? "on" : typeof v === "object" ? JSON.stringify(v) : v)}`);
      cliBox.textContent = [`phi-pdf run ${tool}`, names.map(shellQuote).join(" "), extra.length ? extra.map((x) => `--${x.split("=")[0]} ${x.split("@")[1]}`).join(" ") : "", ...sets, "-o out/"].filter(Boolean).join(" ");
    };
    form.addEventListener("input", () => { applyWhen(); schedulePreview(); showCode(); });
    form.addEventListener("change", () => { applyWhen(); schedulePreview(); showCode(); });
    $$("[data-show-pw]", form).forEach((b) => b.addEventListener("click", () => {
      const input = b.previousElementSibling;
      input.type = input.type === "password" ? "text" : "password";
      b.textContent = input.type === "password" ? "Show" : "Hide";
    }));

    // selects listing other workspace files (insert: the file to insert; watermark: a picture)
    const fillFileSelects = async () => {
      const selects = $$("[data-file-select]", form);
      if (!selects.length) return;
      let files = [];
      try { files = (await api("/api/files", undefined, "GET")).files; } catch (_) { return; }
      selects.forEach((select) => {
        const want = select.dataset.fileSelect;
        const keep = select.value;
        $$("option:not(:first-child)", select).forEach((o) => o.remove());
        files.filter((f) => (want === "image" ? f.kind === "image" : (f.kind === "pdf" || f.kind === "image")) && !f.locked)
          .forEach((f) => select.append(el("option", { value: f.id, text: `${f.name} · ${fileMeta(f)}` })));
        select.value = keep;
      });
    };
    // AI reading: provider → models; the key stays in this browser (localStorage), never in the server's records
    const providerSel = $("[data-ai-provider]", form);
    const modelSel = $("[data-ai-model]", form);
    const keyInput = $("[data-ai-key]", form);
    const AI_MODELS = readJSON("ai-models", {});
    const store = {
      get(k) { try { return localStorage.getItem(k) || ""; } catch (_) { return ""; } },
      set(k, v) { try { if (v) localStorage.setItem(k, v); else localStorage.removeItem(k); } catch (_) { /* private mode */ } },
    };
    const fillModels = () => {
      if (!providerSel || !modelSel) return;
      const provider = providerSel.value;
      modelSel.replaceChildren(...Object.entries(AI_MODELS[provider] || {}).map(([id, label]) => el("option", { value: id, text: label })));
      const savedModel = store.get(`phipdf-ai-model-${provider}`);
      if (savedModel && AI_MODELS[provider] && AI_MODELS[provider][savedModel]) modelSel.value = savedModel;
      if (keyInput) keyInput.value = store.get(`phipdf-ai-key-${provider}`);
    };
    if (providerSel) {
      providerSel.value = store.get("phipdf-ai-provider") || providerSel.value;
      providerSel.addEventListener("change", () => { store.set("phipdf-ai-provider", providerSel.value); fillModels(); });
      modelSel.addEventListener("change", () => store.set(`phipdf-ai-model-${providerSel.value}`, modelSel.value));
      keyInput.addEventListener("change", () => store.set(`phipdf-ai-key-${providerSel.value}`, keyInput.value.trim()));
      fillModels();
    }

    // Text → PDF: typed or pasted text becomes a file in My files and joins the list
    const typedAdd = $("[data-typed-add]", form);
    if (typedAdd) typedAdd.addEventListener("click", async () => {
      const box = $("[data-typed]", form);
      const format = $("[data-typed-format]", form).value;
      if (!box.value.trim()) { toast("Type or paste some text first.", "error"); return; }
      typedAdd.disabled = true;
      try {
        const firstLine = box.value.trim().split("\n")[0].replace(/^#+\s*/, "").slice(0, 60) || "Typed text";
        const data = await api("/api/files/text", { text: box.value, format, name: firstLine });
        add([data.file]);
        box.value = "";
        toast(`Added “${data.file.name}”.`);
      } catch (err) { toast(err.message, "error"); } finally { typedAdd.disabled = false; }
    });

    // metadata: fill in the file's own properties
    const fillMeta = () => {
      const f = chosen[0];
      if (!f || !f.meta) return;
      $$("[data-meta]", form).forEach((input) => { input.value = f.meta[input.dataset.meta] || ""; });
    };

    // -------- page picker (remove / extract / rotate)
    const pagesCard = $("[data-pages-card]", root);
    const pagesGrid = $("[data-pages]", root);
    const pagesInput = $('[data-pages-input="pages"]', form);
    let picked = new Set();
    let lastClick = -1;
    const toRanges = (set) => {
      const nums = Array.from(set).sort((a, b) => a - b);
      const runs = [];
      nums.forEach((n) => { const r = runs[runs.length - 1]; if (r && n === r[1] + 1) r[1] = n; else runs.push([n, n]); });
      return runs.map(([a, b]) => (a === b ? `${a}` : `${a}-${b}`)).join(", ");
    };
    const parseRanges = (text, count) => {
      const set = new Set();
      const clean = String(text).replace(/[০-৯]/g, (d) => "০১২৩৪৫৬৭৮৯".indexOf(d)).toLowerCase().replace(/\s*[-–—]\s*/g, "-");
      clean.split(/[,;\s]+/).forEach((part) => {
        if (!part) return;
        if (part === "odd" || part === "even") { for (let n = part === "odd" ? 1 : 2; n <= count; n += 2) set.add(n); return; }
        if (part === "all") { for (let n = 1; n <= count; n++) set.add(n); return; }
        const m = part.match(/^(\d+|last)?(-)?(\d+|last)?$/);
        if (!m) return;
        const num = (t, d) => (t === undefined ? d : t === "last" ? count : Number(t));
        const a = num(m[1], 1);
        const b = m[2] ? num(m[3], count) : a;
        for (let n = Math.min(a, b); n <= Math.max(a, b); n++) if (n >= 1 && n <= count) set.add(n);
      });
      return set;
    };
    const markPages = () => {
      const mode = pagesGrid.dataset.mode;
      $$(".pdf-page", pagesGrid).forEach((b) => {
        const on = picked.has(Number(b.dataset.n));
        b.classList.toggle(mode === "off" ? "is-off" : "is-on", on);
        b.setAttribute("aria-pressed", String(on));
      });
    };
    const renderPages = () => {
      if (!pagesCard) return;
      const f = chosen[0];
      pagesCard.hidden = !(f && f.kind === "pdf" && !f.locked && f.pages);
      pagesGrid.replaceChildren();
      if (pagesCard.hidden) return;
      picked = parseRanges(pagesInput.value, f.pages);
      for (let n = 1; n <= f.pages; n++) {
        const b = el("button", { type: "button", class: "pdf-page", dataset: { n: String(n) }, "aria-pressed": "false" },
          el("img", { src: `/files/${f.id}/page/${n}.png?w=160`, alt: `Page ${n}`, loading: "lazy" }), `${n}`);
        b.addEventListener("click", (ev) => {
          if (ev.shiftKey && lastClick > 0) {
            const on = !picked.has(n) || picked.has(lastClick);
            for (let k = Math.min(lastClick, n); k <= Math.max(lastClick, n); k++) { if (on) picked.add(k); else picked.delete(k); }
          } else if (picked.has(n)) picked.delete(n); else picked.add(n);
          lastClick = n;
          pagesInput.value = toRanges(picked);
          markPages();
        });
        pagesGrid.append(b);
      }
      markPages();
    };
    if (pagesInput && pagesGrid) pagesInput.addEventListener("input", () => { const f = chosen[0]; if (f) { picked = parseRanges(pagesInput.value, f.pages); markPages(); } });
    $$("[data-pick]", root).forEach((b) => b.addEventListener("click", () => {
      const f = chosen[0];
      if (!f) return;
      const all = Array.from({ length: f.pages }, (_, i) => i + 1);
      const how = b.dataset.pick;
      picked = new Set(how === "all" ? all : how === "none" ? [] : how === "odd" ? all.filter((n) => n % 2) : how === "even" ? all.filter((n) => !(n % 2)) : all.filter((n) => !picked.has(n)));
      pagesInput.value = toRanges(picked);
      markPages();
    }));

    // -------- live preview
    const previewCard = $("[data-preview-card]", root);
    const previewBox = $("[data-pdf-preview]", root);
    const previewImg = $("[data-preview-img]", root);
    let previewPage = 1;
    let previewTimer = 0;
    let previewSeq = 0;
    const schedulePreview = () => { if (previewCard) { clearTimeout(previewTimer); previewTimer = setTimeout(loadPreview, 450); } };
    const loadPreview = async () => {
      const f = chosen[0];
      previewCard.hidden = !(f && f.kind === "pdf" && !f.locked);
      if (previewCard.hidden) return;
      const seq = ++previewSeq;
      previewBox.classList.add("is-busy");
      const label = $("[data-preview-label]", root);
      const total = tool === "nup" ? null : f.pages;
      label.textContent = tool === "nup" ? `Sheet ${previewPage}` : `Page ${previewPage} of ${total}`;
      try {
        const res = await fetch("/api/preview", {
          method: "POST", headers: { "X-CSRF-Token": csrf(), "Content-Type": "application/json" },
          body: JSON.stringify({ tool, files: [f.id], params: params(), page: previewPage, width: Math.min(900, Math.round(previewBox.clientWidth * (window.devicePixelRatio || 1))) }),
        });
        if (seq !== previewSeq) return;
        $$(".pdf-preview-err", previewBox).forEach((n) => n.remove());
        if (!res.ok) {
          let msg = "No preview.";
          try { msg = (await res.json()).error || msg; } catch (_) { /* ignore */ }
          previewImg.hidden = true;
          previewBox.append(el("p", { class: "pdf-preview-err", text: msg }));
        } else {
          const url = await dataUrl(await res.blob());      // the CSP allows data: pictures, not blob:
          if (seq !== previewSeq) return;
          previewImg.src = url;
          previewImg.hidden = false;
        }
      } catch (_) { /* offline: keep the last one */ } finally {
        if (seq === previewSeq) previewBox.classList.remove("is-busy");
      }
    };
    $$("[data-preview-step]", root).forEach((b) => b.addEventListener("click", () => {
      const f = chosen[0];
      if (!f) return;
      const last = tool === "nup" ? 999 : f.pages;
      previewPage = Math.max(1, Math.min(last, previewPage + Number(b.dataset.previewStep)));
      loadPreview();
    }));

    const changed = () => {
      showCode();
      renderChosen();
      renderPages();
      if (tool === "metadata") fillMeta();
      previewPage = 1;
      schedulePreview();
      result.hidden = true;
    };

    // -------- run
    const runBtn = $("[data-run]", form);
    const progress = $("[data-run-progress]", form);
    const errorBox = $("[data-run-error]", form);
    const fail = (msg) => { errorBox.textContent = msg; errorBox.hidden = !msg; };
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      fail("");
      if (chosen.length < meta.min) { fail(meta.min > 1 ? `Choose at least ${meta.min} files.` : "Choose a file first."); return; }
      if (meta.page === "edit") { location.href = `/edit/${chosen[0].id}`; return; }
      if (meta.page === "organize") { location.href = `/organize?files=${chosen.map((f) => f.id).join(",")}`; return; }
      const p = params();
      if (tool === "protect") {
        const again = $('[name="password2"]', form).value;
        if (p.password !== again) { fail("The two passwords are not the same."); return; }
        if (!p.password && !p.owner_password) { fail("Type a password."); return; }
      }
      if (tool === "replace" && !String(p.find || "").trim()) { fail("Write the text to find."); return; }
      if (tool === "ocr" && p.engine === "ai" && !p.ai_key && !(keyInput && keyInput.placeholder.startsWith("optional"))) { fail("Paste your API key for AI reading (or choose the Tesseract engine)."); return; }
      if (tool === "ocr" && p.engine !== "ai" && Array.isArray(p.language) && !p.language.length) { fail("Tick at least one language."); return; }
      if ((tool === "remove" || tool === "extract") && !String(p.pages || "").trim()) { fail("Pick the pages first (click them on the left)."); return; }
      runBtn.disabled = true;
      progress.hidden = false;
      const fill = $("[data-progress-fill]", form);
      const label = $("[data-progress-label]", form);
      const time = $("[data-progress-time]", form);
      fill.style.width = "4%";
      label.textContent = "Starting…";
      const started = Date.now();
      try {
        const { run } = await api("/api/run", { tool, files: chosen.map((f) => f.id), params: p });
        const done = await poll(run, (r) => {
          const pr = r.progress || {};
          const pct = pr.total ? Math.max(4, Math.round((100 * pr.done) / pr.total)) : 10;
          fill.style.width = pct + "%";
          label.textContent = pr.total ? `${pr.label || "Working"} · ${pr.done} of ${pr.total}` : (pr.label || "Working…");
          time.textContent = `${Math.round((Date.now() - started) / 1000)} s`;
        });
        fill.style.width = "100%";
        resultCard(result, done, { onAgain: () => { result.hidden = true; form.scrollIntoView({ behavior: "smooth" }); } });
        if (done.status === "done") result.scrollIntoView({ behavior: "smooth", block: "nearest" });
        fillFileSelects();
      } catch (err) {
        fail(err.message);
      } finally {
        runBtn.disabled = false;
        progress.hidden = true;
      }
    });

    applyWhen();
    changed();
    fillFileSelects();
  }

  // ------------------------------------------------------------------ run page
  async function initRun(root) {
    const run = readJSON("pdf-run-data", null);
    const box = $("[data-result]", root);
    if (!run || !box) return;
    if (run.status === "running") {
      box.hidden = false;
      box.replaceChildren(el("div", { class: "spinner" }), el("p", { class: "muted small", text: "Still working…" }));
      try { resultCard(box, await poll(run.id, () => {})); } catch (err) { toast(err.message, "error"); }
    } else resultCard(box, run);
  }

  window.PDFT = { $, $$, el, api, dataUrl, fileTile, when, toast, iconOf, size, fileMeta, readJSON, TOOLS, GROUP_OF, poll, resultCard, openPicker, upload, csrf, toolMenu };

  const root = $("[data-pdf-page]");
  if (!root) return;
  const page = root.dataset.pdfPage;
  if (page === "home" || page === "files") initList(page);
  else if (page === "tool") initTool(root);
  else if (page === "run") initRun(root);
})();
