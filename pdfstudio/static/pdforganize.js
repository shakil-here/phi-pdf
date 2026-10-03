// PHI PDF Studio · Organize pages: every page as a tile; reorder (drag or move buttons), turn, duplicate, delete,
// add blank pages and pages of other PDFs, then save as a new PDF (the "organize" tool with a page plan).
(() => {
  "use strict";
  const P = window.PDFT;
  const root = document.querySelector('[data-pdf-page="organize"]');
  if (!P || !root) return;
  const { $, $$, el, api, toast, poll, resultCard, openPicker, upload, readJSON } = P;
  const COLORS = ["#4f46e5", "#0f8a5f", "#db2777", "#d97706", "#0891b2", "#7c3aed", "#dc2626", "#65a30d"];

  const sources = [];               // {id, name, pages, sizes, color, letter}
  let pages = [];                   // {key, f, p, r} or {key, blank, w, h}
  const selected = new Set();
  let anchor = null;
  let seq = 0;
  let dirty = false;
  const undo = [];
  const redo = [];
  const grid = $("[data-grid]", root);
  const nameInput = $("[data-name]", root);

  const addSource = (file) => {
    let index = sources.findIndex((s) => s.id === file.id);
    if (index < 0) {
      index = sources.length;
      sources.push({ id: file.id, name: file.name, pages: file.pages, sizes: file.sizes || [], color: COLORS[index % COLORS.length],
                     letter: String.fromCharCode(65 + (index % 26)) });
    }
    return index;
  };
  readJSON("org-data", []).forEach((file) => {
    const f = addSource(file);
    for (let p = 0; p < file.pages; p++) pages.push({ key: ++seq, f, p, r: 0 });
  });
  nameInput.value = sources.length ? `${sources[0].name.replace(/\.pdf$/i, "")} (organized).pdf` : "organized.pdf";

  const snapshot = () => JSON.stringify(pages);
  const change = (fn) => {
    undo.push(snapshot());
    if (undo.length > 100) undo.shift();
    redo.length = 0;
    fn();
    dirty = true;
    render();
  };
  const restore = (from, to) => {
    if (!from.length) return;
    to.push(snapshot());
    pages = JSON.parse(from.pop());
    const keys = new Set(pages.map((pg) => pg.key));
    Array.from(selected).forEach((k) => { if (!keys.has(k)) selected.delete(k); });
    dirty = true;
    render();
  };
  const indexes = () => pages.map((pg, i) => (selected.has(pg.key) ? i : -1)).filter((i) => i >= 0);
  const pageSize = (pg) => {
    if (pg.blank) return [pg.w, pg.h];
    const s = (sources[pg.f].sizes || [])[pg.p];
    return s ? [s[0], s[1]] : [595.28, 841.89];
  };

  // ------------------------------------------------------------------ render
  function tile(pg, position) {
    const src = pg.blank ? null : sources[pg.f];
    const sheet = el("div", { class: "org-sheet" });
    if (pg.blank) sheet.append(el("div", { class: "org-blank", text: "blank" }));
    else {
      const img = el("img", { src: `/files/${src.id}/page/${pg.p + 1}.png?w=240`, alt: `Page ${pg.p + 1} of ${src.name}`, loading: "lazy", draggable: "false" });
      const fit = () => {
        const turned = pg.r % 180 !== 0;
        let k = 1;
        if (turned && img.offsetWidth && img.offsetHeight) k = Math.min(1, sheet.clientWidth / img.offsetHeight, sheet.clientHeight / img.offsetWidth);
        img.style.transform = `rotate(${pg.r}deg) scale(${k.toFixed(3)})`;
      };
      img.addEventListener("load", fit);
      if (img.complete) requestAnimationFrame(fit);
      sheet.append(img);
    }
    const label = el("div", { class: "org-label" });
    if (src) {
      const dot = el("i");
      dot.style.background = src.color;
      label.append(dot, sources.length > 1 ? `${src.letter} · p.${pg.p + 1}` : `p.${pg.p + 1}`);
    } else label.append("blank page");
    if (pg.r) label.append(` · ${pg.r}°`);
    const acts = el("div", { class: "org-acts" },
      el("button", { type: "button", title: "Turn right", "aria-label": "Turn right", onclick: (ev) => { ev.stopPropagation(); change(() => { pg.r = (pg.r + 90) % 360; }); }, text: "↻" }),
      el("button", { type: "button", title: "Duplicate", "aria-label": "Duplicate", onclick: (ev) => { ev.stopPropagation(); change(() => { pages.splice(position + 1, 0, { ...pg, key: ++seq }); }); } }, P.iconOf("copy")),
      el("button", { type: "button", title: "Delete", "aria-label": "Delete", onclick: (ev) => { ev.stopPropagation(); change(() => { pages.splice(position, 1); selected.delete(pg.key); }); } }, P.iconOf("trash")));
    const node = el("div", { class: `org-tile${selected.has(pg.key) ? " is-selected" : ""}`, draggable: "true", tabindex: "0",
                            role: "option", "aria-selected": String(selected.has(pg.key)), dataset: { key: String(pg.key) } },
      el("span", { class: "org-num", text: String(position + 1) }), sheet, label, acts);
    node.addEventListener("click", (ev) => select(pg.key, ev));
    node.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select(pg.key, ev); } });
    wireDrag(node, pg);
    return node;
  }
  function render() {
    const keep = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.key : null;
    grid.replaceChildren(...pages.map((pg, i) => tile(pg, i)));
    if (!pages.length) grid.append(el("p", { class: "pdf-empty", text: "No pages left. Undo, or add a PDF." }));
    if (keep) { const n = $(`.org-tile[data-key="${keep}"]`, grid); if (n) n.focus({ preventScroll: true }); }
    $("[data-count]", root).textContent = `${pages.length} page${pages.length === 1 ? "" : "s"}${selected.size ? ` · ${selected.size} selected` : ""}`;
    const legend = $("[data-legend]", root);
    legend.replaceChildren(...sources.map((s) => { const i = el("i"); i.style.background = s.color; return el("span", {}, i, `${sources.length > 1 ? s.letter + " · " : ""}${s.name}`); }));
    $('[data-act="undo"]', root).disabled = !undo.length;
    $('[data-act="redo"]', root).disabled = !redo.length;
  }
  function select(key, ev) {
    const order = pages.map((pg) => pg.key);
    if (ev && ev.shiftKey && anchor !== null && order.includes(anchor)) {
      const a = order.indexOf(anchor);
      const b = order.indexOf(key);
      if (!(ev.metaKey || ev.ctrlKey)) selected.clear();
      order.slice(Math.min(a, b), Math.max(a, b) + 1).forEach((k) => selected.add(k));
    } else if (ev && (ev.metaKey || ev.ctrlKey)) {
      if (selected.has(key)) selected.delete(key); else selected.add(key);
      anchor = key;
    } else {
      const only = selected.size === 1 && selected.has(key);
      selected.clear();
      if (!only) selected.add(key);
      anchor = key;
    }
    render();
  }

  // ------------------------------------------------------------------ drag and drop (moves the selection when the dragged page is in it)
  let dragKeys = [];
  function wireDrag(node, pg) {
    node.addEventListener("dragstart", (ev) => {
      dragKeys = selected.has(pg.key) ? pages.filter((x) => selected.has(x.key)).map((x) => x.key) : [pg.key];
      node.classList.add("is-dragging");
      ev.dataTransfer.effectAllowed = "move";
      ev.dataTransfer.setData("text/plain", String(pg.key));
    });
    node.addEventListener("dragend", () => { dragKeys = []; $$(".is-dragging, .drop-before, .drop-after", grid).forEach((n) => n.classList.remove("is-dragging", "drop-before", "drop-after")); });
    node.addEventListener("dragover", (ev) => {
      if (!dragKeys.length) return;
      ev.preventDefault();
      const box = node.getBoundingClientRect();
      const after = ev.clientX > box.left + box.width / 2;
      $$(".drop-before, .drop-after", grid).forEach((n) => { if (n !== node) n.classList.remove("drop-before", "drop-after"); });
      node.classList.toggle("drop-after", after);
      node.classList.toggle("drop-before", !after);
    });
    node.addEventListener("drop", (ev) => {
      ev.preventDefault();
      if (!dragKeys.length || dragKeys.includes(pg.key)) return;
      const after = node.classList.contains("drop-after");
      const moving = dragKeys.slice();
      change(() => {
        const moved = pages.filter((x) => moving.includes(x.key));
        pages = pages.filter((x) => !moving.includes(x.key));
        let at = pages.findIndex((x) => x.key === pg.key);
        if (after) at += 1;
        pages.splice(at, 0, ...moved);
      });
    });
  }

  // ------------------------------------------------------------------ actions
  const need = () => { if (!selected.size) { toast("Select pages first (click them)."); return false; } return true; };
  const acts = {
    all: () => { if (selected.size === pages.length) selected.clear(); else pages.forEach((pg) => selected.add(pg.key)); render(); },
    left: () => need() && change(() => pages.forEach((pg) => { if (selected.has(pg.key)) pg.r = (pg.r + 270) % 360; })),
    right: () => need() && change(() => pages.forEach((pg) => { if (selected.has(pg.key)) pg.r = (pg.r + 90) % 360; })),
    dup: () => need() && change(() => {
      const idx = indexes();
      const copies = idx.map((i) => ({ ...pages[i], key: ++seq }));
      pages.splice(idx[idx.length - 1] + 1, 0, ...copies);
      selected.clear();
      copies.forEach((c) => selected.add(c.key));
    }),
    del: () => need() && change(() => { pages = pages.filter((pg) => !selected.has(pg.key)); selected.clear(); }),
    back: () => need() && change(() => {
      indexes().forEach((i) => { if (i > 0 && !selected.has(pages[i - 1].key)) [pages[i - 1], pages[i]] = [pages[i], pages[i - 1]]; });
    }),
    fwd: () => need() && change(() => {
      indexes().reverse().forEach((i) => { if (i < pages.length - 1 && !selected.has(pages[i + 1].key)) [pages[i + 1], pages[i]] = [pages[i], pages[i + 1]]; });
    }),
    blank: () => change(() => {
      const idx = indexes();
      const at = idx.length ? idx[idx.length - 1] + 1 : pages.length;
      const near = pages[Math.max(0, at - 1)];
      const [w, h] = near ? pageSize(near) : [595.28, 841.89];
      const pg = { key: ++seq, blank: true, w, h };
      pages.splice(at, 0, pg);
      selected.clear();
      selected.add(pg.key);
    }),
    add: () => openPicker({ kinds: ["pdf"], multiple: true, onPick: addFiles }),
    reverse: () => change(() => {
      const idx = indexes();
      if (idx.length >= 2) {
        const picked = idx.map((i) => pages[i]).reverse();
        idx.forEach((i, k) => { pages[i] = picked[k]; });
      } else pages.reverse();
    }),
    undo: () => restore(undo, redo),
    redo: () => restore(redo, undo),
  };
  $$("[data-act]", root).forEach((b) => b.addEventListener("click", () => acts[b.dataset.act]()));
  async function addFiles(files) {
    for (const file of files) {
      if (file.kind !== "pdf" || file.locked) { toast(`${file.name} is not an open PDF.`, "error"); continue; }
      let details = file;
      try { details = await api(`/api/files/${file.id}`, undefined, "GET"); } catch (err) { toast(err.message, "error"); continue; }
      change(() => {
        const f = addSource(details);
        const idx = indexes();
        const at = idx.length ? idx[idx.length - 1] + 1 : pages.length;
        const added = Array.from({ length: details.pages }, (_, p) => ({ key: ++seq, f, p, r: 0 }));
        pages.splice(at, 0, ...added);
        selected.clear();
        added.forEach((pg) => selected.add(pg.key));
      });
      toast(`${details.pages} page(s) of ${details.name} added.`);
    }
  }
  const up = $("[data-org-upload]", root);
  up.addEventListener("change", async () => {
    const files = up.files;
    if (!files.length) return;
    try {
      const r = await upload(files, $("[data-upload-status]", root));
      r.errors.forEach((e) => toast(e, "error"));
      await addFiles(r.files);
    } catch (err) { toast(err.message, "error"); }
    up.value = "";
  });

  const zoom = $("[data-zoom]", root);
  try { const z = localStorage.getItem("phipdf-org-zoom"); if (z) zoom.value = z; } catch (_) { /* private mode */ }
  const applyZoom = () => { grid.style.setProperty("--tile", `${zoom.value}px`); try { localStorage.setItem("phipdf-org-zoom", zoom.value); } catch (_) { /* ignore */ } };
  zoom.addEventListener("input", applyZoom);
  applyZoom();

  document.addEventListener("keydown", (ev) => {
    if (ev.target.matches("input, textarea, select")) return;
    const mod = ev.metaKey || ev.ctrlKey;
    if (mod && ev.key.toLowerCase() === "z") { ev.preventDefault(); (ev.shiftKey ? acts.redo : acts.undo)(); }
    else if (mod && ev.key.toLowerCase() === "y") { ev.preventDefault(); acts.redo(); }
    else if (mod && ev.key.toLowerCase() === "a") { ev.preventDefault(); pages.forEach((pg) => selected.add(pg.key)); render(); }
    else if (mod && ev.key.toLowerCase() === "d") { ev.preventDefault(); acts.dup(); }
    else if ((ev.key === "Delete" || ev.key === "Backspace") && selected.size) { ev.preventDefault(); acts.del(); }
    else if (ev.key.toLowerCase() === "r" && !mod && selected.size) { ev.preventDefault(); (ev.shiftKey ? acts.left : acts.right)(); }
    else if (ev.key === "Escape") { selected.clear(); render(); }
  });
  window.addEventListener("beforeunload", (ev) => { if (dirty) { ev.preventDefault(); ev.returnValue = ""; } });

  // ------------------------------------------------------------------ save
  const save = $("[data-save]", root);
  save.addEventListener("click", async () => {
    if (!pages.length) { toast("There are no pages to save.", "error"); return; }
    const plan = pages.map((pg) => (pg.blank ? { blank: true, w: pg.w, h: pg.h } : { f: pg.f, p: pg.p, r: pg.r }));
    save.disabled = true;
    const label = save.lastChild.textContent;
    save.lastChild.textContent = " Saving…";
    try {
      const { run } = await api("/api/run", { tool: "organize", files: sources.map((s) => s.id), params: { plan, output_name: nameInput.value } });
      const done = await poll(run, () => {});
      const box = $("[data-result]", root);
      resultCard(box, done);
      box.scrollIntoView({ behavior: "smooth", block: "nearest" });
      if (done.status === "done") dirty = false;
    } catch (err) {
      toast(err.message, "error");
    } finally {
      save.disabled = false;
      save.lastChild.textContent = label;
    }
  });

  render();
})();
