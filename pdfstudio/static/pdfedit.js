// PHI PDF Studio · Edit PDF: change the text already on the page ("Edit text": click a paragraph and type), and add
// text, pictures, signatures, boxes, circles, lines, arrows, free drawing, highlights, whiteout and redaction; saved
// as a new PDF through the "edit" tool (tools.apply_edits; page text changes in edit_text.py).
// Every item is kept in PDF points of the page as seen; the screen shows it at `scale` pixels per point.
(() => {
  "use strict";
  const P = window.PDFT;
  const root = document.querySelector('[data-pdf-page="edit"]');
  if (!P || !root) return;
  const { $, $$, el, api, toast, poll, resultCard, readJSON, dataUrl } = P;

  const file = readJSON("ed-file", null);
  if (!file) return;
  const gallery = readJSON("ed-images", []);
  const sizes = (file.sizes && file.sizes.length ? file.sizes : [[595.28, 841.89, 0]]).map((s) => [Number(s[0]), Number(s[1])]);
  const canvas = $("[data-canvas]", root);
  const rail = $("[data-rail]", root);
  const props = $("[data-props]", root);

  const FONT_CSS = {
    sans: 'Helvetica, Arial, "Noto Sans Bengali", "Kohinoor Bangla", sans-serif',
    serif: '"Times New Roman", Times, "Noto Serif Bengali", "Kohinoor Bangla", serif',
    mono: 'ui-monospace, Menlo, Consolas, monospace',
  };
  const FONTS = readJSON("ed-fonts", {});
  const fontCss = (key, family) => {
    if (FONT_CSS[key]) return FONT_CSS[key];
    const f = FONTS[key];
    return f ? `"${f.css}", ${FONT_CSS[f.generic] || FONT_CSS.sans}` : FONT_CSS[family] || FONT_CSS.sans;
  };
  const shape = (color, extra = {}) => ({ color, fill: "", width: 2, opacity: 100, dash: "solid", ...extra });
  const BASE_DEFAULTS = {
    text: { color: "#111827", size: 14, font: "sans", bold: false, italic: false, underline: false, strike: false, align: "left", opacity: 100, fill: "" },
    date: { color: "#111827", size: 12, font: "sans", bold: false, italic: false, underline: false, strike: false, align: "left", opacity: 100, fill: "" },
    note: { color: "#facc15" },
    stamp: { color: "#16a34a", width: 2.5, opacity: 90, text: "APPROVED" },
    rect: shape("#dc2626"), roundrect: shape("#7c3aed"), ellipse: shape("#dc2626"), triangle: shape("#2563eb"),
    diamond: shape("#ea580c"), star: shape("#d97706", { fill: "#fde047" }),
    line: shape("#dc2626"), arrow: shape("#dc2626"), arrow2: shape("#2563eb"),
    pen: { color: "#2563eb", width: 2, opacity: 100 },
    pencil: { color: "#374151", width: 0.8, opacity: 85 },
    marker: { color: "#22c55e", width: 12, opacity: 35 },
    whitener: { color: "#ffffff", width: 10, matchPaper: true },
    highlight: { color: "#fde047", opacity: 40 },
    underline: { color: "#dc2626", width: 1.5, opacity: 100 },
    strike: { color: "#dc2626", width: 1.5, opacity: 100 },
    check: { color: "#16a34a", width: 2.5, opacity: 100 },
    cross: { color: "#dc2626", width: 2.5, opacity: 100 },
    dot: { color: "#111827", opacity: 100 },
    whiteout: { fill: "#ffffff", matchPaper: true },
    redact: { fill: "#000000" },
    link: { url: "" },
    image: { opacity: 100 },
    textedit: {},
  };
  const defaults = JSON.parse(JSON.stringify(BASE_DEFAULTS));
  try { Object.entries(JSON.parse(localStorage.getItem("phipdf-edit-defaults") || "{}")).forEach(([k, v]) => { if (defaults[k]) Object.assign(defaults[k], v); }); } catch (_) { /* none saved */ }
  const saveDefaults = () => { try { localStorage.setItem("phipdf-edit-defaults", JSON.stringify(defaults)); } catch (_) { /* private mode */ } };
  const SHAPE_PROPS = ["color", "fill", "width", "opacity", "dash"];
  const PROPS_OF = {
    select: [], sign: [], edittext: ["ocr"], textedit: ["color", "font", "size", "style", "align"],
    text: ["color", "font", "size", "style", "align", "opacity", "fill"], date: ["color", "font", "size", "style", "align", "opacity", "fill"],
    note: ["color"], stamp: ["stamp", "color", "width", "opacity"],
    rect: SHAPE_PROPS, roundrect: SHAPE_PROPS, ellipse: SHAPE_PROPS, triangle: SHAPE_PROPS, diamond: SHAPE_PROPS, star: SHAPE_PROPS,
    line: ["color", "width", "opacity", "dash"], arrow: ["color", "width", "opacity", "dash"], arrow2: ["color", "width", "opacity", "dash"],
    pen: ["color", "width", "opacity"], pencil: ["color", "width", "opacity"], marker: ["color", "width", "opacity"],
    whitener: ["color", "width", "paper"], highlight: ["color", "opacity"],
    underline: ["color", "width", "opacity"], strike: ["color", "width", "opacity"],
    check: ["color", "width", "opacity"], cross: ["color", "width", "opacity"], dot: ["color", "opacity"],
    whiteout: ["fill", "paper"], redact: ["fill"], link: ["url"], image: ["opacity", "images"],
  };
  const HELP = {
    select: "Click an item to select it · drag to move · drag the corner to resize · double-click text to change it · arrow keys nudge · Ctrl+C / Ctrl+V copy.",
    edittext: "Click a word on the page: it is selected, so just type to replace it. Only the words you change are replaced; the rest stays exactly as it was. Empty a paragraph to remove it. A scanned page or a photo has no text: press “Read text on this page (OCR)” first.",
    text: "Click on the page where the text goes, then type (বাংলা works). Pick any font in the bar.",
    date: "Click where today's date goes; you can change it like any text.",
    note: "Click to stick a note: it becomes a comment (a note icon that opens) in the PDF.",
    stamp: "Click or drag to put a stamp; choose or type its words in the bar above.",
    image: "Choose a picture: it goes on the page you are looking at; then drag it into place.",
    rect: "Drag on the page to draw a box.", roundrect: "Drag on the page to draw a box with round corners.",
    ellipse: "Drag on the page to draw a circle or oval.", triangle: "Drag on the page to draw a triangle.",
    diamond: "Drag on the page to draw a diamond.", star: "Drag on the page to draw a star.",
    line: "Drag from one end to the other.", arrow: "Drag from the tail to the point.", arrow2: "Drag from one point to the other.",
    pen: "Draw freely with the mouse, a finger or a pen.", pencil: "A thin, soft pencil line: draw freely.",
    marker: "A wide see-through marker: draw over anything.",
    whitener: "A correction pen: paint over what should go (it takes the paper's colour when “Match the paper colour” is on).",
    highlight: "Drag over the text to highlight it.", underline: "Drag along the text to underline it.",
    strike: "Drag along the text to strike it out.", check: "Click to put a tick (drag for a bigger one).",
    cross: "Click to put a cross (drag for a bigger one).", dot: "Click to put a dot.",
    whiteout: "Drag over what should be covered (with the paper's colour when “Match the paper colour” is on). It is still in the file underneath; use Redact to remove it.",
    redact: "Drag over what must disappear: text and pictures under the box are really removed when you save.",
    link: "Drag a box over what should be clickable, then type the web address in the bar above.",
  };
  const STROKES = new Set(["pen", "pencil", "marker", "whitener"]);
  const LINES = new Set(["line", "arrow", "arrow2"]);
  const POLYGONS = {
    triangle: [[0.5, 0], [1, 1], [0, 1]],
    diamond: [[0.5, 0], [1, 0.5], [0.5, 1], [0, 0.5]],
    star: (() => {
      const pts = Array.from({ length: 10 }, (_, i) => {
        const r = i % 2 ? 0.4 : 1, a = ((-90 + 36 * i) * Math.PI) / 180;
        return [0.5 + 0.5 * r * Math.cos(a), 0.5 + 0.5 * r * Math.sin(a)];
      });
      const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
      const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
      return pts.map(([x, y]) => [(x - x0) / (x1 - x0), (y - y0) / (y1 - y0)]);
    })(),
  };
  const KEEP_TOOL = new Set(["pen", "pencil", "marker", "whitener", "highlight", "underline", "strike", "whiteout", "redact", "check", "cross", "dot"]);
  const SHAPES = new Set(["rect", "roundrect", "ellipse", "triangle", "diamond", "star", "highlight", "underline", "strike", "whiteout", "redact", "link", "stamp", "check", "cross", "dot"]);
  const CLICK_SIZE = { rect: [140, 70], roundrect: [140, 70], ellipse: [120, 80], triangle: [90, 80], diamond: [80, 80], star: [80, 76], highlight: [160, 16],
    underline: [160, 14], strike: [160, 14], whiteout: [140, 30], redact: [140, 20], link: [160, 20], stamp: [150, 44], check: [18, 18], cross: [18, 18], dot: [7, 7] };
  const TEXTISH = new Set(["text", "textedit", "note"]);
  const NAMES = { text: "text", image: "picture", rect: "box", roundrect: "rounded box", ellipse: "circle", arrow2: "double arrow", pen: "drawing",
    pencil: "pencil line", marker: "marker line", whitener: "whitener stroke", strike: "strike-out", check: "tick", note: "sticky note" };

  let tool = "select";
  let objects = [];
  const images = new Map();           // key -> {src, data?, file?, w, h}
  let selectedId = null;
  let editingId = null;
  let seq = 0;
  let zoom = 1;
  let base = 1;
  let scale = 1;
  let current = 0;
  let saved = true;
  let clipboard = null;
  const undoStack = [];
  const redoStack = [];

  // ------------------------------------------------------------------ undo
  const snap = () => JSON.stringify(objects);
  const remember = () => { undoStack.push(snap()); if (undoStack.length > 200) undoStack.shift(); redoStack.length = 0; saved = false; };
  const restore = (from, to) => {
    if (!from.length) return;
    to.push(snap());
    objects = JSON.parse(from.pop());
    if (!objects.some((o) => o.id === selectedId)) selectedId = null;
    editingId = null;
    saved = false;
    renderAll();
  };

  // ------------------------------------------------------------------ pages
  const pages = sizes.map(([w, h], i) => {
    const img = el("img", { alt: `Page ${i + 1}`, draggable: "false" });
    const layer = el("div", { class: "ed-layer tool-select", dataset: { page: String(i) } });
    const wrap = el("div", { class: "ed-page", dataset: { page: String(i) } }, el("span", { class: "ed-page-no", text: `${i + 1}` }), img, layer);
    canvas.append(wrap);
    const thumb = el("button", { type: "button", "aria-label": `Page ${i + 1}` },
      el("img", { src: `/files/${file.id}/page/${i + 1}.png?w=160`, alt: "", loading: "lazy" }), `${i + 1}`);
    thumb.addEventListener("click", () => wrap.scrollIntoView({ behavior: "smooth", block: "start" }));
    rail.append(thumb);
    wireLayer(layer, i);
    return { w, h, img, layer, wrap, thumb, loaded: 0, near: false };
  });
  const loadImage = (pg, i) => {
    const want = Math.round(pg.w * scale * (window.devicePixelRatio || 1));
    if (pg.loaded >= want * 0.9) return;
    pg.loaded = want;
    pg.img.src = `/files/${file.id}/page/${i + 1}.png?w=${want}`;
  };
  const seen = new Map();
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      const i = Number(e.target.dataset.page);
      pages[i].near = e.isIntersecting;
      seen.set(i, e.intersectionRatio);
      if (e.isIntersecting) { loadImage(pages[i], i); if (tool === "edittext" && !textCache.has(i)) loadBlocks(i); }
    });
    let best = current;
    let ratio = -1;
    seen.forEach((r, i) => { if (r > ratio) { ratio = r; best = i; } });
    if (best !== current) { current = best; markRail(); }
  }, { rootMargin: "600px 0px", threshold: [0, 0.25, 0.5, 0.75, 1] });
  pages.forEach((pg) => observer.observe(pg.wrap));
  const markRail = () => pages.forEach((pg, i) => {
    pg.thumb.classList.toggle("is-active", i === current);
    pg.thumb.classList.toggle("has-edits", objects.some((o) => o.page === i));
  });

  function layout() {
    base = Math.max(0.3, Math.min(2.4, (canvas.clientWidth - 40) / Math.max(...sizes.map((s) => s[0]))));
    scale = base * zoom;
    pages.forEach((pg, i) => {
      pg.wrap.style.width = `${pg.w * scale}px`;
      pg.wrap.style.height = `${pg.h * scale}px`;
      if (pg.near) loadImage(pg, i);
    });
    $("[data-zoom-label]", root).textContent = `${Math.round(zoom * 100)}%`;
    renderAll();
  }

  // ------------------------------------------------------------------ objects on screen
  const byId = (id) => objects.find((o) => o.id === id);
  const px = (v) => `${(v * scale).toFixed(2)}px`;
  function bbox(o) {
    if (LINES.has(o.type)) {
      const pad = Math.max(4, (o.width || 1) * (o.type === "line" ? 1.5 : 4));
      return { x: Math.min(o.x1, o.x2) - pad, y: Math.min(o.y1, o.y2) - pad, w: Math.abs(o.x2 - o.x1) + 2 * pad, h: Math.abs(o.y2 - o.y1) + 2 * pad };
    }
    if (STROKES.has(o.type)) {
      const xs = o.points.map((p) => p[0]);
      const ys = o.points.map((p) => p[1]);
      const pad = Math.max(3, o.width || 1);
      return { x: Math.min(...xs) - pad, y: Math.min(...ys) - pad, w: Math.max(...xs) - Math.min(...xs) + 2 * pad, h: Math.max(...ys) - Math.min(...ys) + 2 * pad };
    }
    return { x: o.x, y: o.y, w: o.w, h: o.h };
  }
  const SVG = "http://www.w3.org/2000/svg";
  const svgEl = (tag, attrs) => { const n = document.createElementNS(SVG, tag); Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v)); return n; };
  const dashArray = (o, w) => (o.dash === "dash" ? `${w * 3} ${w * 2}` : o.dash === "dot" ? `0.01 ${w * 2}` : "");
  function strokeSvg(o, b) {
    const svg = svgEl("svg", { viewBox: `0 0 ${b.w * scale} ${b.h * scale}` });
    const w = Math.max(0.6, (o.width || 1) * scale);
    const color = o.color || "#000";
    const op = (o.opacity === undefined ? 100 : o.opacity) / 100;
    const look = { stroke: color, "stroke-width": w, "stroke-linecap": "round", "stroke-linejoin": "round", "stroke-opacity": op };
    if (o.dash && o.dash !== "solid") look["stroke-dasharray"] = dashArray(o, w);
    const at = (u, v) => `${u * b.w * scale},${v * b.h * scale}`;
    if (STROKES.has(o.type)) {
      svg.append(svgEl("polyline", { points: o.points.map(([x, y]) => `${(x - b.x) * scale},${(y - b.y) * scale}`).join(" "), fill: "none", ...look }));
      return svg;
    }
    if (POLYGONS[o.type]) {
      const inset = w / 2;
      const pts = POLYGONS[o.type].map(([u, v]) => `${inset + u * (b.w * scale - w)},${inset + v * (b.h * scale - w)}`).join(" ");
      svg.append(svgEl("polygon", { points: pts, fill: o.fill || "none", "fill-opacity": op, ...look, "stroke-width": o.width > 0 ? w : 0 }));
      return svg;
    }
    if (o.type === "underline" || o.type === "strike") {
      const y = o.type === "underline" ? b.h * scale - w / 2 : (b.h * scale) / 2;
      svg.append(svgEl("line", { x1: 0, y1: y, x2: b.w * scale, y2: y, ...look, "stroke-linecap": "butt" }));
      return svg;
    }
    if (o.type === "check") {
      svg.append(svgEl("polyline", { points: [at(0.08, 0.55), at(0.38, 0.88), at(0.94, 0.12)].join(" "), fill: "none", ...look }));
      return svg;
    }
    if (o.type === "cross") {
      svg.append(svgEl("path", { d: `M${at(0.12, 0.12)}L${at(0.88, 0.88)}M${at(0.88, 0.12)}L${at(0.12, 0.88)}`, fill: "none", ...look }));
      return svg;
    }
    const x1 = (o.x1 - b.x) * scale, y1 = (o.y1 - b.y) * scale, x2 = (o.x2 - b.x) * scale, y2 = (o.y2 - b.y) * scale;
    svg.append(svgEl("line", { x1, y1, x2, y2, ...look }));
    const head = (tx, ty, hx, hy) => {
      const a = Math.atan2(hy - ty, hx - tx);
      const len = Math.max(6, (o.width || 1) * 4) * scale;
      const pts = [[hx - len * Math.cos(a - 0.45), hy - len * Math.sin(a - 0.45)], [hx, hy], [hx - len * Math.cos(a + 0.45), hy - len * Math.sin(a + 0.45)]];
      svg.append(svgEl("polygon", { points: pts.map((p) => p.join(",")).join(" "), fill: color, stroke: color, "stroke-width": w, "fill-opacity": op, "stroke-opacity": op, "stroke-linejoin": "round" }));
    };
    if (o.type === "arrow" || o.type === "arrow2") head(x1, y1, x2, y2);
    if (o.type === "arrow2") head(x2, y2, x1, y1);
    return svg;
  }
  const SVG_DRAWN = new Set([...STROKES, ...LINES, ...Object.keys(POLYGONS), "underline", "strike", "check", "cross"]);
  function place(node, o) {
    const b = bbox(o);
    node.style.left = px(b.x);
    node.style.top = px(b.y);
    node.style.width = px(b.w);
    node.style.height = px(b.h);
    if (SVG_DRAWN.has(o.type)) {
      $$("svg", node).forEach((s) => s.remove());
      node.prepend(strokeSvg(o, b));
    }
    if (o.type === "stamp") node.style.fontSize = px(Math.max(4, (o.h - 2 * (o.width || 2) - 2) * 0.62));
    if (o.type === "roundrect") node.style.borderRadius = px(Math.min(o.w, o.h) * 0.18);
  }
  function nodeOf(o) {
    const node = el("div", { class: "ed-obj", dataset: { id: String(o.id) } });
    const op = (o.opacity === undefined ? 100 : o.opacity) / 100;
    switch (o.type) {
      case "textedit":
        node.classList.add("ed-text", "ed-textedit");
        if (syncRuns(o) && new Set(o.runs.map((r) => r.c)).size > 1) {
          node.replaceChildren(...o.runs.map((r) => { const span = el("span", { text: r.t }); span.style.color = r.c; return span; }));
        } else node.textContent = o.text;
        node.style.fontFamily = fontCss(o.font === "original" ? o.family : o.font, o.family);
        node.style.fontSize = px(o.size);
        node.style.lineHeight = String(lineHeight(o));
        node.style.color = o.color;
        node.style.fontWeight = o.bold ? "700" : "400";
        node.style.fontStyle = o.italic ? "italic" : "normal";
        node.style.textAlign = o.align === "justify" ? "left" : o.align;
        node.style.background = o.bg || "#ffffff";
        node.style.minHeight = px(o.h);
        node.style.minWidth = px(o.w);
        if (!o.text.trim()) node.dataset.empty = "removed";
        break;
      case "text":
        node.classList.add("ed-text");
        node.textContent = o.text;
        node.style.fontFamily = fontCss(o.font, "sans");
        node.style.fontSize = px(o.size);
        node.style.color = o.color;
        node.style.fontWeight = o.bold ? "700" : "400";
        node.style.fontStyle = o.italic ? "italic" : "normal";
        node.style.textDecoration = [o.underline ? "underline" : "", o.strike ? "line-through" : ""].join(" ").trim() || "none";
        node.style.textAlign = o.align;
        node.style.opacity = op;
        if (o.fill) node.style.background = o.fill;
        break;
      case "image": {
        const img = images.get(o.img);
        node.append(el("img", { class: "ed-img", src: img ? img.src : "", alt: "" }));
        node.style.opacity = op;
        break;
      }
      case "rect":
      case "roundrect":
      case "ellipse":
        node.style.border = o.width > 0 ? `${px(o.width)} ${o.dash === "dash" ? "dashed" : o.dash === "dot" ? "dotted" : "solid"} ${o.color}` : "0";
        node.style.background = o.fill || "transparent";
        node.style.opacity = op;
        if (o.type === "ellipse") node.style.borderRadius = "50%";
        break;
      case "note":
        node.classList.add("ed-text", "ed-note");
        node.textContent = o.text;
        node.style.background = o.color || "#facc15";
        break;
      case "stamp":
        node.classList.add("ed-stamp");
        node.textContent = o.text || "APPROVED";
        node.style.color = o.color;
        node.style.border = `${px(o.width || 2)} solid ${o.color}`;
        node.style.borderRadius = px(Math.min(o.w, o.h) * 0.18);
        node.style.opacity = op;
        break;
      case "link":
        node.classList.add("ed-link");
        node.textContent = o.url || "type the web address above";
        node.title = o.url || "";
        break;
      case "dot":
        node.style.background = o.color;
        node.style.borderRadius = "50%";
        node.style.opacity = op;
        break;
      case "marker":
        node.classList.add("ed-highlight");
        break;
      case "highlight":
        node.classList.add("ed-highlight");
        node.style.background = o.color;
        node.style.opacity = Math.min(op, 0.45);
        break;
      case "whiteout":
        node.style.background = o.fill || "#ffffff";
        node.style.boxShadow = "0 0 0 1px rgba(100, 116, 139, .35)";
        break;
      case "redact":
        node.classList.add("ed-redact");
        node.style.background = o.fill || "#000000";
        node.textContent = "REDACT";
        break;
      default:
        break;
    }
    place(node, o);
    if (o.id === selectedId) {
      node.classList.add("is-selected");
      if (LINES.has(o.type)) {
        const b = bbox(o);
        [["p1", o.x1, o.y1], ["p2", o.x2, o.y2]].forEach(([which, x, y]) => {
          const h = el("span", { class: `ed-handle h-${which}`, dataset: { handle: which } });
          h.style.left = px(x - b.x - 6 / scale);
          h.style.top = px(y - b.y - 6 / scale);
          node.append(h);
        });
      } else if (!STROKES.has(o.type) && o.type !== "textedit") node.append(el("span", { class: "ed-handle h-se", dataset: { handle: "se" } }));
    }
    if (o.id === editingId) startEditing(node, o, false);
    node.addEventListener("pointerdown", (ev) => objectDown(ev, o, node));
    node.addEventListener("dblclick", () => { if (TEXTISH.has(o.type)) { selectedId = o.id; editingId = o.id; renderPage(o.page); } });
    return node;
  }
  function renderPage(i) {
    const layer = pages[i].layer;
    layer.replaceChildren(...objects.filter((o) => o.page === i).map(nodeOf));
    if (tool === "edittext") showBlocks(i);
  }

  // ------------------------------------------------------------------ the page's own text (Edit text)
  const textCache = new Map();          // page -> blocks (null while loading)
  const scanned = new Set();            // pages that are pictures with no text (worth OCR)
  const ocrBusy = new Set();
  const ocrDone = new Set();
  let blockSeq = 0;                     // OCR blocks get new ids, so they never match an older edit
  function loadBlocks(i) {
    if (textCache.has(i)) return;
    textCache.set(i, null);
    api(`/api/files/${file.id}/text/${i + 1}`, undefined, "GET")
      .then((data) => {
        textCache.set(i, data.blocks || []);
        if (data.scanned) scanned.add(i);
        if (tool === "edittext") renderPage(i);
      })
      .catch(() => textCache.set(i, []));
  }
  const ocrLang = $("[data-ocr-lang]", root);
  try { const last = localStorage.getItem("phipdf-edit-ocr"); if (ocrLang && last && $$("option", ocrLang).some((o) => o.value === last)) ocrLang.value = last; } catch (_) { /* none */ }
  if (ocrLang) ocrLang.addEventListener("change", () => { try { localStorage.setItem("phipdf-edit-ocr", ocrLang.value); } catch (_) { /* private mode */ } });
  async function readPage(i) {
    if (!ocrLang) { toast("OCR needs Tesseract and a language: see Settings.", "error"); return false; }
    if (ocrBusy.has(i)) return false;
    ocrBusy.add(i);
    renderPage(i);
    try {
      const data = await api(`/api/files/${file.id}/ocr/${i + 1}`, { language: ocrLang.value });
      const blocks = (data.blocks || []).map((b) => ({ ...b, id: `ocr${++blockSeq}` }));
      textCache.set(i, blocks);
      ocrDone.add(i);
      scanned.delete(i);
      toast(blocks.length ? `Page ${i + 1}: ${blocks.length} paragraph${blocks.length === 1 ? "" : "s"} found. Click one to change it.` : `No text was found on page ${i + 1}: check the OCR language.`);
      return true;
    } catch (err) {
      toast(err.message, "error");
      return false;
    } finally {
      ocrBusy.delete(i);
      if (tool !== "edittext") setTool("edittext"); else renderAll();
    }
  }
  async function wholePage(i) {
    if (!ocrDone.has(i) && !(await readPage(i))) return;
    const blocks = (textCache.get(i) || []).filter((b) => !objects.some((o) => o.type === "textedit" && o.page === i && o.block === b.id));
    if (!blocks.length) { toast("There is no text on this page to turn into editable text."); return; }
    if (!window.confirm(`Turn the ${blocks.length} paragraph${blocks.length === 1 ? "" : "s"} found on page ${i + 1} into real text? The letters in the picture are covered with the paper colour and the text is written on top, so it can be searched, copied and changed. Check what OCR read before you save.`)) return;
    if (editingId !== null) finishEditing();
    remember();
    blocks.forEach((b) => objects.push({ ...blockObject(b, i), restyled: true, forced: true }));
    renderAll();
  }
  $$("[data-ocr]", root).forEach((b) => b.addEventListener("click", () => (b.dataset.ocr === "all" ? wholePage(current) : readPage(current))));
  function showBlocks(i) {
    const blocks = textCache.get(i);
    if (blocks === undefined) { if (pages[i].near) loadBlocks(i); return; }
    if (!blocks) return;
    const layer = pages[i].layer;
    if (ocrBusy.has(i) || (scanned.has(i) && !ocrDone.has(i))) {
      const busy = ocrBusy.has(i);
      const banner = el("div", { class: "ed-scan" },
        el("strong", { text: busy ? "Reading the text on this page…" : "This page is a picture (a scan or a photo)" }),
        el("span", { text: busy ? "OCR takes a few seconds." : "Its letters are pixels, so there is nothing to click yet. Read them with OCR to change them." }));
      if (!busy) {
        const go = el("button", { type: "button", class: "btn btn-primary btn-sm", text: "Read text with OCR" });
        go.addEventListener("pointerdown", (ev) => ev.stopPropagation());
        go.addEventListener("click", () => readPage(i));
        banner.append(go);
      }
      layer.append(banner);
    }
    blocks.forEach((b) => {
      if (objects.some((o) => o.type === "textedit" && o.page === i && o.block === b.id)) return;
      const [x0, y0, x1, y1] = b.bbox;
      const node = el("div", { class: `ed-block${b.unreadable ? " is-unreadable" : ""}${b.ocr ? " is-ocr" : ""}`,
        title: b.unreadable ? "This text cannot be read back from the PDF: retype it" : b.ocr ? `Read by OCR: “${b.text.slice(0, 80)}” · click to change it` : "Click to edit this text" });
      node.style.left = px(x0 - 2); node.style.top = px(y0 - 2);
      node.style.width = px(x1 - x0 + 4); node.style.height = px(y1 - y0 + 4);
      node.addEventListener("pointerdown", (ev) => { ev.stopPropagation(); ev.preventDefault(); editBlock(b, i, toPt(ev, layer, i)); });
      layer.append(node);
    });
  }
  // the paper's colour in a box of the page (the lighter pixels there), for whiteout and the whitener brush
  function paperColor(i, x, y, w, h) {
    try {
      const img = pages[i].img;
      if (!img.complete || !img.naturalWidth) return "#ffffff";
      const k = img.naturalWidth / pages[i].w;
      const pad = 3;
      const sx = Math.max(0, (x - pad) * k), sy = Math.max(0, (y - pad) * k);
      const sw = Math.max(1, Math.min(img.naturalWidth - sx, (w + 2 * pad) * k)), sh = Math.max(1, Math.min(img.naturalHeight - sy, (h + 2 * pad) * k));
      const c = document.createElement("canvas");
      c.width = 24; c.height = 24;
      const ctx2 = c.getContext("2d");
      ctx2.drawImage(img, sx, sy, sw, sh, 0, 0, 24, 24);
      const d = ctx2.getImageData(0, 0, 24, 24).data;
      const px4 = [];
      for (let n = 0; n < d.length; n += 4) px4.push([d[n], d[n + 1], d[n + 2], d[n] * 0.299 + d[n + 1] * 0.587 + d[n + 2] * 0.114]);
      px4.sort((a, b) => b[3] - a[3]);
      const top = px4.slice(0, Math.max(1, Math.round(px4.length * 0.3)));
      const pick = top[Math.floor(top.length / 2)];
      return `#${pick.slice(0, 3).map((v) => v.toString(16).padStart(2, "0")).join("")}`;
    } catch (_) { return "#ffffff"; }
  }
  function background(i, x, y) {
    // the page colour just outside the paragraph, so the box hides the old text on screen like the save will
    try {
      const img = pages[i].img;
      if (!img.complete || !img.naturalWidth) return "#ffffff";
      const k = img.naturalWidth / pages[i].w;
      const c = document.createElement("canvas");
      c.width = 1; c.height = 1;
      const ctx = c.getContext("2d");
      ctx.drawImage(img, Math.max(0, (x - 3) * k), Math.max(0, (y - 3) * k), 1, 1, 0, 0, 1, 1);
      const [r, g, bl] = ctx.getImageData(0, 0, 1, 1).data;
      return `rgb(${r}, ${g}, ${bl})`;
    } catch (_) { return "#ffffff"; }
  }
  function blockObject(b, i) {
    const [x0, y0, x1, y1] = b.bbox;
    return {
      id: ++seq, page: i, type: "textedit", block: b.id, x: x0, y: y0, w: x1 - x0, h: y1 - y0, text: b.text, orig: b.text,
      size: b.size, old_size: b.size, color: b.color, font: b.fontkey || "original", fontname: b.font, family: b.family, bold: b.bold,
      italic: b.italic, align: b.align, lines: b.lines, bg: b.bg || background(i, x0, y0), unreadable: b.unreadable, ocr: !!b.ocr,
      start: { size: b.size, color: b.color, bold: b.bold, italic: b.italic, align: b.align, font: b.fontkey || "original" },
      runs: b.runs ? b.runs.map((r) => ({ ...r })) : null,
    };
  }
  // the word under a click, as [start, end] in the paragraph's text (from the page's own word boxes)
  function wordAt(b, pt) {
    let best = null, bestD = Infinity, offset = 0;
    (b.lines || []).forEach((ln) => {
      let from = 0;
      (ln.words || []).forEach((w) => {
        const at = ln.text.indexOf(w[4], from);
        if (at < 0) return;
        from = at + w[4].length;
        const dx = Math.max(w[0] - pt.x, 0, pt.x - w[2]);
        const dy = Math.max(w[1] - pt.y, 0, pt.y - w[3]);
        if (dx + 3 * dy < bestD) { bestD = dx + 3 * dy; best = [offset + at, offset + at + w[4].length]; }
      });
      offset += ln.text.length + 1;
    });
    return best;
  }
  function editBlock(b, i, pt) {
    if (editingId !== null) finishEditing();
    remember();
    const o = blockObject(b, i);
    const word = pt && !b.unreadable ? wordAt(b, pt) : null;
    if (word) caretWanted = { word };
    objects.push(o);
    selectedId = o.id;
    editingId = o.id;
    renderAll();
    if (b.unreadable) toast("This text is stored in a way that cannot be read back (common with Bengali PDFs): select it all and retype it.");
  }
  const lineHeight = (o) => (o.lines && o.lines.length > 1 ? Math.max(1, Math.min(2.5, (o.lines[1].origin[1] - o.lines[0].origin[1]) / (o.old_size || o.size))) : 1.2);
  function renderAll() {
    pages.forEach((_, i) => renderPage(i));
    markRail();
    showProps();
    const n = objects.length;
    $("[data-ed-count]", root).textContent = n ? `${n} change${n === 1 ? "" : "s"}${saved ? " · saved" : ""}` : "No changes yet";
    $('[data-ed="undo"]', root).disabled = !undoStack.length;
    $('[data-ed="redo"]', root).disabled = !redoStack.length;
    $('[data-ed="delete"]', root).disabled = selectedId === null;
    $('[data-ed="dup"]', root).disabled = selectedId === null;
    $('[data-ed="front"]', root).disabled = selectedId === null;
    $('[data-ed="back"]', root).disabled = selectedId === null;
    $('[data-ed="paste"]', root).disabled = !clipboard;
  }
  function select(id) {
    if (editingId !== null && editingId !== id) finishEditing();
    selectedId = id;
    renderAll();
  }

  // ------------------------------------------------------------------ text editing
  let caretWanted = null;               // where typing starts in the next text opened: {word: [start, end]} or {x, y}
  function textSpot(node, start, end) {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    let pos = 0, a = null, t;
    while ((t = walker.nextNode())) {
      const len = t.nodeValue.length;
      if (!a && start <= pos + len) a = [t, start - pos];
      if (a && end <= pos + len) return [a, [t, end - pos]];
      pos += len;
    }
    return null;
  }
  function placeCaret(node, range, want) {
    if (want && want.word) {
      const spot = textSpot(node, want.word[0], want.word[1]);
      if (spot) { range.setStart(...spot[0]); range.setEnd(...spot[1]); return; }
    } else if (want) {
      const at = document.caretRangeFromPoint ? document.caretRangeFromPoint(want.x, want.y)
        : document.caretPositionFromPoint ? document.caretPositionFromPoint(want.x, want.y) : null;
      const where = at && (at.startContainer || at.offsetNode);
      if (where && node.contains(where)) { range.setStart(where, at.startOffset ?? at.offset); range.collapse(true); return; }
    }
    range.selectNodeContents(node);
    range.collapse(false);
  }
  function startEditing(node, o, focus = true) {
    node.contentEditable = "true";
    node.spellcheck = false;
    const grow = () => {
      o.text = node.innerText.replace(/\n$/, "");
      const h = node.scrollHeight / scale;
      if (h > o.h + 0.5) { o.h = h; node.style.height = px(o.h); }
    };
    node.addEventListener("input", grow);
    node.addEventListener("paste", (ev) => {
      ev.preventDefault();
      const text = (ev.clipboardData || window.clipboardData).getData("text/plain");
      document.execCommand("insertText", false, text);
    });
    node.addEventListener("blur", () => { if (editingId === o.id) finishEditing(); });
    node.addEventListener("keydown", (ev) => {
      ev.stopPropagation();
      if (ev.key === "Escape") { ev.preventDefault(); node.blur(); }
    });
    if (focus || document.activeElement !== node) {
      const want = caretWanted;
      caretWanted = null;
      requestAnimationFrame(() => {
        node.focus();
        const range = document.createRange();
        placeCaret(node, range, want);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      });
    }
  }
  // ---- a paragraph's colours, run by run ([{t, c}]): kept through changes by matching old and new letters
  const restyledOf = (o) => {
    const s0 = o.start || {};
    return !!o.forced || o.size !== s0.size || o.color !== s0.color || o.bold !== s0.bold || o.italic !== s0.italic || o.align !== s0.align || o.font !== (s0.font || "original");
  };
  const runsText = (runs) => runs.map((r) => r.t).join("");
  function recolor(runs, text) {
    const old = runsText(runs);
    const colors = [];
    runs.forEach((r) => { for (let k = 0; k < r.t.length; k++) colors.push(r.c); });
    const n = old.length, m = text.length;
    const out = new Array(m).fill(null);
    if (n && m && n * m <= 4e6) {                    // the longest common run of letters keeps its colours
      const w = m + 1;
      const dp = new Uint16Array((n + 1) * w);
      for (let i = 1; i <= n; i++) for (let j = 1; j <= m; j++) {
        dp[i * w + j] = old[i - 1] === text[j - 1] ? dp[(i - 1) * w + j - 1] + 1 : Math.max(dp[(i - 1) * w + j], dp[i * w + j - 1]);
      }
      for (let i = n, j = m; i > 0 && j > 0;) {
        if (old[i - 1] === text[j - 1]) { out[j - 1] = colors[i - 1]; i--; j--; }
        else if (dp[(i - 1) * w + j] >= dp[i * w + j - 1]) i--;
        else j--;
      }
    } else if (n) for (let j = 0; j < m; j++) out[j] = colors[Math.min(n - 1, Math.floor((j * n) / m))];
    for (let j = 0; j < m; j++) if (out[j] === null) out[j] = j ? out[j - 1] : null;   // typed letters: the colour before
    const next = out.find((c) => c !== null) || (runs[0] && runs[0].c) || "#000000";
    const result = [];
    for (let j = 0; j < m; j++) {
      const c = out[j] === null ? next : out[j];
      if (result.length && result[result.length - 1].c === c) result[result.length - 1].t += text[j];
      else result.push({ t: text[j], c });
    }
    return result;
  }
  const syncRuns = (o) => { if (o.runs && runsText(o.runs) !== o.text) o.runs = recolor(o.runs, o.text); return o.runs; };

  function finishEditing() {
    const o = byId(editingId);
    editingId = null;
    if (o && o.type === "textedit") {
      o.restyled = restyledOf(o);
      if (o.text === o.orig && !o.restyled) objects = objects.filter((x) => x.id !== o.id);
    } else if (o && !String(o.text || "").trim()) objects = objects.filter((x) => x.id !== o.id);
    if (o && selectedId === o.id && !byId(o.id)) selectedId = null;
    renderAll();
  }

  // ------------------------------------------------------------------ pointer: drawing on a page
  function toPt(ev, layer, i) {
    const r = layer.getBoundingClientRect();
    const x = Math.max(0, Math.min(pages[i].w, (ev.clientX - r.left) / scale));
    const y = Math.max(0, Math.min(pages[i].h, (ev.clientY - r.top) / scale));
    return { x, y };
  }
  function wireLayer(layer, i) {
    layer.addEventListener("pointerdown", (ev) => {
      // a drawing tool draws on top of other items too (with Select, a click on an item picks it instead)
      const onItem = ev.target !== layer && ev.target.closest(".ed-obj");
      const drawing = tool !== "select" && tool !== "edittext";
      if (ev.button !== 0 || (ev.target !== layer && !(drawing && onItem && !onItem.isContentEditable))) return;
      if (editingId !== null) { finishEditing(); if (tool === "select") return; }
      current = i;
      const start = toPt(ev, layer, i);
      if (tool === "select" || tool === "edittext") { select(null); return; }
      if (tool === "image") { toast("Choose a picture in the bar above first."); return; }
      if (tool === "sign") { openSign(); return; }
      if (tool === "text" || tool === "date") {
        remember();
        const d = defaults[tool];
        const today = new Date().toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" });
        const o = { id: ++seq, page: i, type: "text", x: start.x, y: Math.max(0, start.y - d.size * 0.7), w: Math.min(tool === "date" ? 160 : 260, pages[i].w - start.x - 4), h: d.size * 1.35, ...d, text: tool === "date" ? today : "" };
        o.w = Math.max(40, o.w);
        objects.push(o);
        selectedId = o.id;
        editingId = tool === "date" ? null : o.id;
        setTool("select");
        renderAll();
        return;
      }
      if (tool === "note") {
        remember();
        const o = { id: ++seq, page: i, type: "note", x: Math.min(start.x, pages[i].w - 170), y: start.y, w: 170, h: 70, text: "", ...defaults.note };
        objects.push(o);
        selectedId = o.id;
        editingId = o.id;
        setTool("select");
        renderAll();
        return;
      }
      ev.preventDefault();
      layer.setPointerCapture(ev.pointerId);
      const draft = el("div", { class: "ed-draft" });
      layer.append(draft);
      const points = [[start.x, start.y]];
      let svgHost = null;
      const stroking = STROKES.has(tool);
      let strokeLook = { ...defaults[tool] };
      if (tool === "whitener" && defaults.whitener.matchPaper) strokeLook.color = paperColor(i, start.x - 6, start.y - 6, 12, 12);
      if (stroking || LINES.has(tool)) { svgHost = el("div", { class: `ed-obj${tool === "marker" ? " ed-highlight" : ""}` }); layer.append(svgHost); draft.remove(); }
      const move = (e) => {
        const p = toPt(e, layer, i);
        if (stroking) {
          const last = points[points.length - 1];
          if (Math.hypot(p.x - last[0], p.y - last[1]) * scale > 1.5) points.push([p.x, p.y]);
          const o = { type: tool, points, ...strokeLook };
          if (points.length > 1) place(svgHost, o);
        } else if (LINES.has(tool)) {
          place(svgHost, { type: tool, x1: start.x, y1: start.y, x2: p.x, y2: p.y, ...defaults[tool] });
        } else {
          draft.style.left = px(Math.min(start.x, p.x));
          draft.style.top = px(Math.min(start.y, p.y));
          draft.style.width = px(Math.abs(p.x - start.x));
          draft.style.height = px(Math.abs(p.y - start.y));
        }
      };
      const up = (e) => {
        layer.removeEventListener("pointermove", move);
        layer.removeEventListener("pointerup", up);
        layer.removeEventListener("pointercancel", up);
        draft.remove();
        if (svgHost) svgHost.remove();
        const p = toPt(e, layer, i);
        let o = null;
        if (stroking) {
          if (points.length < 2) {
            if (tool !== "pencil" && tool !== "pen") return;
            points.push([start.x + 0.3, start.y + 0.3]);       // a click: a dot
          }
          o = { type: tool, points: points.map(([x, y]) => [Math.round(x * 100) / 100, Math.round(y * 100) / 100]), ...strokeLook };
          delete o.matchPaper;
        } else if (LINES.has(tool)) {
          let end = p;
          if (Math.hypot(p.x - start.x, p.y - start.y) < 4) end = { x: Math.min(pages[i].w, start.x + 120), y: start.y };
          o = { type: tool, x1: start.x, y1: start.y, x2: end.x, y2: end.y, ...defaults[tool] };
        } else if (SHAPES.has(tool)) {
          let x = Math.min(start.x, p.x), y = Math.min(start.y, p.y), w = Math.abs(p.x - start.x), h = Math.abs(p.y - start.y);
          if (w < 4 && h < 4) {
            [w, h] = CLICK_SIZE[tool] || [100, 60];
            const centred = tool === "check" || tool === "cross" || tool === "dot";
            x = Math.max(0, Math.min(centred ? start.x - w / 2 : start.x, pages[i].w - w));
            y = Math.max(0, Math.min(centred ? start.y - h / 2 : start.y, pages[i].h - h));
          }
          w = Math.max(w, 4); h = Math.max(h, 4);         // a flat drag still leaves something to see and grab
          o = { type: tool, x, y, w, h, ...defaults[tool] };
          if (tool === "whiteout" && defaults.whiteout.matchPaper) o.fill = paperColor(i, x, y, w, h);
          delete o.matchPaper;
        }
        if (!o) return;
        remember();
        o.id = ++seq;
        o.page = i;
        objects.push(o);
        selectedId = KEEP_TOOL.has(tool) ? null : o.id;
        if (!KEEP_TOOL.has(tool)) setTool("select");
        renderAll();
        if (o.type === "link") requestAnimationFrame(() => { const u = $('[data-prop="url"]', props); if (u) u.focus(); });
      };
      layer.addEventListener("pointermove", move);
      layer.addEventListener("pointerup", up);
      layer.addEventListener("pointercancel", up);
    });
  }

  // ------------------------------------------------------------------ pointer: moving and resizing items
  function objectDown(ev, o, node) {
    if (o.type === "textedit" && ev.button === 0 && o.id !== editingId && (tool === "select" || tool === "edittext")) {
      ev.stopPropagation();                            // the page's own text: a click goes straight to typing
      ev.preventDefault();
      if (editingId !== null) finishEditing();
      caretWanted = { x: ev.clientX, y: ev.clientY };
      selectedId = o.id;
      editingId = o.id;
      renderAll();
      return;
    }
    if (tool !== "select" || ev.button !== 0) return;
    if (o.id === editingId) return;                    // clicking inside the text being typed
    ev.stopPropagation();
    ev.preventDefault();
    if (editingId !== null) finishEditing();
    if (selectedId !== o.id) { selectedId = o.id; renderAll(); node = $(`.ed-obj[data-id="${o.id}"]`, pages[o.page].layer); }
    const layer = pages[o.page].layer;
    const handle = ev.target.dataset ? ev.target.dataset.handle : null;
    const start = toPt(ev, layer, o.page);
    const before = snap();
    const orig = JSON.parse(JSON.stringify(o));
    let moved = false;
    layer.setPointerCapture(ev.pointerId);
    const move = (e) => {
      const p = toPt(e, layer, o.page);
      const dx = p.x - start.x, dy = p.y - start.y;
      if (!moved && Math.hypot(dx, dy) * scale < 2) return;
      moved = true;
      if (handle === "se") {
        let w = Math.max(6, orig.w + dx), h = Math.max(6, orig.h + dy);
        if (o.type === "image" && !e.shiftKey) h = w * (orig.h / orig.w);
        o.w = w; o.h = h;
      } else if (handle === "p1") { o.x1 = orig.x1 + dx; o.y1 = orig.y1 + dy; }
      else if (handle === "p2") { o.x2 = orig.x2 + dx; o.y2 = orig.y2 + dy; }
      else if (LINES.has(o.type)) { o.x1 = orig.x1 + dx; o.x2 = orig.x2 + dx; o.y1 = orig.y1 + dy; o.y2 = orig.y2 + dy; }
      else if (STROKES.has(o.type)) o.points = orig.points.map(([x, y]) => [x + dx, y + dy]);
      else { o.x = orig.x + dx; o.y = orig.y + dy; }
      place(node, o);
      if (o.type === "text" || o.type === "note") node.style.height = px(o.h);
    };
    const up = () => {
      layer.removeEventListener("pointermove", move);
      layer.removeEventListener("pointerup", up);
      layer.removeEventListener("pointercancel", up);
      if (moved) { undoStack.push(before); redoStack.length = 0; saved = false; }
      renderAll();
    };
    layer.addEventListener("pointermove", move);
    layer.addEventListener("pointerup", up);
    layer.addEventListener("pointercancel", up);
  }

  // ------------------------------------------------------------------ tools and the properties bar
  function setTool(name) {
    tool = name;
    root.classList.toggle("is-edittext", name === "edittext");
    $$("[data-tool]", root).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.tool === name)));
    pages.forEach((pg) => { pg.layer.className = `ed-layer tool-${name}`; });
    if (name !== "select" && selectedId !== null && editingId === null) selectedId = null;
    if (name === "sign") openSign();
    renderAll();
  }
  $$("[data-tool]", root).forEach((b) => b.addEventListener("click", () => setTool(b.dataset.tool)));

  function showProps() {
    const o = byId(selectedId);
    const kind = o ? o.type : tool;
    const show = PROPS_OF[kind] || [];
    const values = o || defaults[kind] || {};
    $$("[data-p]", props).forEach((n) => { n.hidden = !show.includes(n.dataset.p); });
    $("[data-help]", props).textContent = o ? (o.type === "textedit" ? (o.ocr ? "Changing text read by OCR: check every word. On saving, its place is covered with the paper colour and your text is written there." : "Changing the page's own text: only the words you change are replaced, in the same font, size and colour (the font of the PDF when it has those letters). Delete undoes this change; empty the text to remove it from the page.")
      : `Selected: ${NAMES[o.type] || o.type}. Delete removes it · Ctrl+C copies · Ctrl+] / Ctrl+[ bring to front / send back.`) : (HELP[kind] || "");
    const original = $('[data-prop="font"] option[value="original"]', props);
    if (original) original.hidden = kind !== "textedit";
    const set = (name, value) => { const input = $(`[data-prop="${name}"]`, props); if (input && document.activeElement !== input) input.value = value; };
    set("color", values.color || "#000000");
    set("fillMode", values.fill ? "color" : "none");
    set("fill", values.fill || (kind === "redact" ? "#000000" : "#ffffff"));
    $('[data-prop="fill"]', props).hidden = !values.fill && kind !== "whiteout" && kind !== "redact";
    $('[data-prop="fillMode"]', props).hidden = kind === "whiteout" || kind === "redact";
    set("width", values.width ?? 2);
    set("opacity", values.opacity ?? 100);
    set("font", values.font || (kind === "textedit" ? "original" : "sans"));
    set("size", values.size || 14);
    set("dash", values.dash || "solid");
    set("url", values.url || "");
    if (kind === "stamp") set("text", values.text || "");
    const paper = $('[data-prop="matchPaper"]', props);
    if (paper) paper.checked = !!(defaults[kind] || {}).matchPaper;
    $$("[data-flag]", props).forEach((b) => {
      b.setAttribute("aria-pressed", String(!!values[b.dataset.flag]));
      b.hidden = kind === "textedit" && (b.dataset.flag === "underline" || b.dataset.flag === "strike");
    });
    $$("[data-align]", props).forEach((b) => b.setAttribute("aria-pressed", String((values.align || "left") === b.dataset.align)));
    $("[data-color-label]", props).textContent = ["text", "date", "textedit"].includes(kind) ? "Text" : ["highlight", "note", "dot", "marker", "whitener", "stamp", "check", "cross"].includes(kind) ? "Colour" : "Line";
    props.hidden = !show.length && !$("[data-help]", props).textContent;
  }
  let propSnap = false;
  function setProp(name, value) {
    const o = byId(selectedId);
    const kind = o ? o.type : tool;
    if (defaults[kind]) { defaults[kind][name] = value; saveDefaults(); }
    if (!o) return;
    if (!propSnap) { remember(); propSnap = true; }
    o[name] = value;
    if (o.type === "textedit" && name === "color") o.runs = null;
    if (o.type === "textedit") {
      o.restyled = restyledOf(o);
    }
    if (name === "matchPaper" && value) {                // take the paper colour again, where the item is
      if (o.type === "whiteout") o.fill = paperColor(o.page, o.x, o.y, o.w, o.h);
      if (o.type === "whitener" && o.points.length) o.color = paperColor(o.page, o.points[0][0] - 6, o.points[0][1] - 6, 12, 12);
      delete o.matchPaper;
    }
    if (o.type === "text" && name === "size") {
      const node = $(`.ed-obj[data-id="${o.id}"]`, pages[o.page].layer);
      if (node) { node.style.fontSize = px(value); o.h = Math.max(o.h, node.scrollHeight / scale); }
    }
    renderPage(o.page);
    const n = objects.length;
    $("[data-ed-count]", root).textContent = `${n} change${n === 1 ? "" : "s"}`;
  }
  props.addEventListener("change", () => { propSnap = false; });
  $$("[data-prop]", props).forEach((input) => input.addEventListener("input", () => {
    const name = input.dataset.prop;
    if (name === "fillMode") {
      const fillInput = $('[data-prop="fill"]', props);
      fillInput.hidden = input.value === "none";
      setProp("fill", input.value === "none" ? "" : fillInput.value);
    } else if (name === "fill") setProp("fill", input.value);
    else if (input.type === "checkbox") setProp(name, input.checked);
    else if (["width", "opacity", "size"].includes(name)) { const v = Number(input.value); if (!Number.isNaN(v) && v >= 0) setProp(name, v); }
    else setProp(name, input.value);
  }));
  $$("[data-flag]", props).forEach((b) => b.addEventListener("click", () => {
    const on = b.getAttribute("aria-pressed") !== "true";
    b.setAttribute("aria-pressed", String(on));
    propSnap = false;
    setProp(b.dataset.flag, on);
    propSnap = false;
  }));
  $$("[data-align]", props).forEach((b) => b.addEventListener("click", () => {
    $$("[data-align]", props).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    propSnap = false;
    setProp("align", b.dataset.align);
    propSnap = false;
  }));

  // ------------------------------------------------------------------ pictures
  const MAX_EDGE = 2400;
  function addPicture(entry) {
    const key = `img${++seq}`;
    images.set(key, entry);
    const pg = pages[current];
    let w = Math.min(pg.w * 0.4, entry.w * 0.75);
    let h = w * (entry.h / Math.max(entry.w, 1));
    if (h > pg.h * 0.6) { h = pg.h * 0.6; w = h * (entry.w / Math.max(entry.h, 1)); }
    const viewTop = Math.max(0, (canvas.getBoundingClientRect().top < 0 ? -pg.wrap.getBoundingClientRect().top / scale : 0));
    const o = { id: ++seq, page: current, type: "image", img: key, x: (pg.w - w) / 2, y: Math.min(pg.h - h, viewTop + 60), w, h, opacity: defaults.image.opacity };
    remember();
    objects.push(o);
    selectedId = o.id;
    setTool("select");
    pg.wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  const loadImg = (src) => new Promise((resolve, reject) => { const i = new Image(); i.onload = () => resolve(i); i.onerror = reject; i.src = src; });
  async function shrink(src, type) {
    const img = await loadImg(src);
    const k = Math.min(1, MAX_EDGE / Math.max(img.naturalWidth, img.naturalHeight));
    if (k >= 1 && /png|jpe?g/.test(type)) return { src, w: img.naturalWidth, h: img.naturalHeight };
    const c = document.createElement("canvas");
    c.width = Math.round(img.naturalWidth * k);
    c.height = Math.round(img.naturalHeight * k);
    c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
    const out = /jpe?g/.test(type) ? c.toDataURL("image/jpeg", 0.9) : c.toDataURL("image/png");
    return { src: out, w: c.width, h: c.height };
  }
  const picInput = $("[data-ed-image]", root);
  picInput.addEventListener("change", async () => {
    const f = picInput.files[0];
    picInput.value = "";
    if (!f) return;
    if (f.size > 30 * 1024 * 1024) { toast("That picture is over 30 MB.", "error"); return; }
    try {
      const shrunk = await shrink(await dataUrl(f), f.type);
      addPicture({ src: shrunk.src, data: shrunk.src, w: shrunk.w, h: shrunk.h });
    } catch (_) { toast("That picture could not be read.", "error"); }
  });
  const galleryBox = $("[data-ed-gallery]", root);
  gallery.slice(0, 24).forEach((g) => {
    const b = el("button", { type: "button", title: g.name }, el("img", { src: `/files/${g.id}/page/1.png?w=160`, alt: g.name, loading: "lazy" }));
    b.addEventListener("click", () => addPicture({ src: `/files/${g.id}/page/1.png?w=1000`, file: g.id, w: g.width || 400, h: g.height || 300 }));
    galleryBox.append(b);
  });

  // signature pad
  const signDialog = $("#ed-sign");
  const pad = $("[data-sign-pad]", signDialog);
  const ctx = pad.getContext("2d");
  let inked = false;
  function openSign() {
    ctx.clearRect(0, 0, pad.width, pad.height);
    inked = false;
    signDialog.showModal();
  }
  pad.addEventListener("pointerdown", (ev) => {
    pad.setPointerCapture(ev.pointerId);
    const r = pad.getBoundingClientRect();
    const k = pad.width / r.width;
    ctx.strokeStyle = $("[data-sign-color]", signDialog).value;
    ctx.lineWidth = Number($("[data-sign-width]", signDialog).value) * 2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo((ev.clientX - r.left) * k, (ev.clientY - r.top) * k);
    const move = (e) => { ctx.lineTo((e.clientX - r.left) * k, (e.clientY - r.top) * k); ctx.stroke(); inked = true; };
    const up = () => { pad.removeEventListener("pointermove", move); pad.removeEventListener("pointerup", up); };
    pad.addEventListener("pointermove", move);
    pad.addEventListener("pointerup", up);
  });
  $("[data-sign-clear]", signDialog).addEventListener("click", () => { ctx.clearRect(0, 0, pad.width, pad.height); inked = false; });
  $$("[data-sign-close]", signDialog).forEach((b) => b.addEventListener("click", () => { signDialog.close(); setTool("select"); }));
  $("[data-sign-use]", signDialog).addEventListener("click", () => {
    if (!inked) { toast("Draw the signature first."); return; }
    const data = ctx.getImageData(0, 0, pad.width, pad.height).data;
    let x0 = pad.width, y0 = pad.height, x1 = 0, y1 = 0;
    for (let y = 0; y < pad.height; y += 2) for (let x = 0; x < pad.width; x += 2) {
      if (data[(y * pad.width + x) * 4 + 3] > 10) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
    }
    const m = 8;
    x0 = Math.max(0, x0 - m); y0 = Math.max(0, y0 - m); x1 = Math.min(pad.width, x1 + m); y1 = Math.min(pad.height, y1 + m);
    const c = document.createElement("canvas");
    c.width = x1 - x0; c.height = y1 - y0;
    c.getContext("2d").drawImage(pad, x0, y0, c.width, c.height, 0, 0, c.width, c.height);
    const src = c.toDataURL("image/png");
    signDialog.close();
    addPicture({ src, data: src, w: c.width / 2, h: c.height / 2 });
  });

  // ------------------------------------------------------------------ actions, keys, zoom
  const del = () => {
    if (selectedId === null) return;
    remember();
    objects = objects.filter((o) => o.id !== selectedId);
    selectedId = null;
    editingId = null;
    renderAll();
  };
  const shift = (o, dx, dy) => {
    if (LINES.has(o.type)) { o.x1 += dx; o.x2 += dx; o.y1 += dy; o.y2 += dy; }
    else if (STROKES.has(o.type)) o.points = o.points.map(([x, y]) => [x + dx, y + dy]);
    else { o.x += dx; o.y += dy; }
  };
  const dup = () => {
    const o = byId(selectedId);
    if (!o || o.type === "textedit") return;
    remember();
    const c = JSON.parse(JSON.stringify(o));
    c.id = ++seq;
    shift(c, 12, 12);
    objects.push(c);
    selectedId = c.id;
    renderAll();
  };
  const copy = () => { const o = byId(selectedId); if (o && o.type !== "textedit") { clipboard = JSON.parse(JSON.stringify(o)); toast("Copied: paste it with Ctrl+V on any page."); } };
  const paste = () => {
    if (!clipboard) { toast("Select an item and press Ctrl+C first."); return; }
    remember();
    const c = JSON.parse(JSON.stringify(clipboard));
    c.id = ++seq;
    if (c.page === current) shift(c, 12, 12);
    c.page = current;
    clipboard = JSON.parse(JSON.stringify(c));
    objects.push(c);
    selectedId = c.id;
    renderAll();
  };
  const restack = (front) => {
    const o = byId(selectedId);
    if (!o) return;
    remember();
    objects = objects.filter((x) => x !== o);
    if (front) objects.push(o); else objects.unshift(o);
    renderAll();
  };
  const nudge = (dx, dy) => {
    const o = byId(selectedId);
    if (!o) return;
    remember();
    shift(o, dx, dy);
    renderPage(o.page);
  };
  const ZOOMS = [0.5, 0.67, 0.8, 1, 1.25, 1.5, 2, 2.5, 3];
  const zoomTo = (z) => { zoom = z; layout(); };
  const actions = {
    undo: () => restore(undoStack, redoStack), redo: () => restore(redoStack, undoStack), delete: del, dup, paste,
    front: () => restack(true), back: () => restack(false),
    "zoom-in": () => zoomTo(ZOOMS.find((z) => z > zoom + 0.01) || zoom),
    "zoom-out": () => zoomTo([...ZOOMS].reverse().find((z) => z < zoom - 0.01) || zoom),
    fit: () => zoomTo(1), save,
  };
  $$("[data-ed]", root).forEach((b) => b.addEventListener("click", () => actions[b.dataset.ed]()));
  const KEYS = { v: "select", e: "edittext", t: "text", d: "date", n: "note", m: "stamp", i: "image", s: "sign", r: "rect", o: "ellipse", l: "line", a: "arrow",
    p: "pen", g: "pencil", k: "marker", b: "whitener", h: "highlight", u: "underline", c: "check", z: "cross", w: "whiteout", x: "redact" };
  document.addEventListener("keydown", (ev) => {
    if (editingId !== null || ev.target.isContentEditable || ev.target.matches("input, textarea, select")) return;
    if ($("dialog[open]")) return;
    const mod = ev.metaKey || ev.ctrlKey;
    const key = ev.key.toLowerCase();
    if (mod && key === "z") { ev.preventDefault(); (ev.shiftKey ? actions.redo : actions.undo)(); }
    else if (mod && key === "y") { ev.preventDefault(); actions.redo(); }
    else if (mod && key === "d") { ev.preventDefault(); dup(); }
    else if (mod && key === "c" && selectedId !== null) { ev.preventDefault(); copy(); }
    else if (mod && key === "v" && clipboard) { ev.preventDefault(); paste(); }
    else if (mod && key === "]") { ev.preventDefault(); restack(true); }
    else if (mod && key === "[") { ev.preventDefault(); restack(false); }
    else if (mod && key === "s") { ev.preventDefault(); save(); }
    else if ((ev.key === "Delete" || ev.key === "Backspace") && selectedId !== null) { ev.preventDefault(); del(); }
    else if (ev.key === "Escape") { selectedId = null; setTool("select"); }
    else if (ev.key.startsWith("Arrow") && selectedId !== null) {
      ev.preventDefault();
      const step = ev.shiftKey ? 10 : 1;
      nudge(ev.key === "ArrowLeft" ? -step : ev.key === "ArrowRight" ? step : 0, ev.key === "ArrowUp" ? -step : ev.key === "ArrowDown" ? step : 0);
    } else if (!mod && !ev.altKey && KEYS[key]) setTool(KEYS[key]);
    else if (!mod && (ev.key === "+" || ev.key === "=")) actions["zoom-in"]();
    else if (!mod && ev.key === "-") actions["zoom-out"]();
  });
  let resizeTimer = 0;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(layout, 150); });
  window.addEventListener("beforeunload", (ev) => { if (!saved && objects.length) { ev.preventDefault(); ev.returnValue = ""; } });

  // ------------------------------------------------------------------ save
  function ops() {
    return objects.map((o) => {
      const base = { page: o.page, type: o.type, opacity: (o.opacity === undefined ? 100 : o.opacity) / 100 };
      switch (o.type) {
        case "textedit":
          return { page: o.page, type: "textedit", x: o.x, y: o.y, w: o.w, h: o.h, text: o.text, orig: o.orig, size: o.size,
                   old_size: o.old_size, color: o.color, font: o.font, fontname: o.fontname, bold: o.bold, italic: o.italic,
                   align: o.align, restyled: !!o.restyled, ocr: !!o.ocr, bg: /^#[0-9a-f]{6}$/i.test(o.bg || "") ? o.bg : "", runs: syncRuns(o) || null,
                   lines: o.lines.map((l) => ({ x: l.bbox[0], y: l.bbox[1], w: l.bbox[2] - l.bbox[0], h: l.bbox[3] - l.bbox[1], ox: l.origin[0], oy: l.origin[1], size: l.size,
                                                ...(l.words ? { words: l.words } : {}) })) };
        case "text":
          return { ...base, x: o.x, y: o.y, w: o.w, h: o.h, text: o.text, size: o.size, color: o.color, font: o.font === "original" ? "sans" : o.font, bold: o.bold,
                   italic: o.italic, underline: o.underline, strike: !!o.strike, align: o.align, fill: o.fill || "" };
        case "image": {
          const img = images.get(o.img) || {};
          return { ...base, x: o.x, y: o.y, w: o.w, h: o.h, ...(img.file ? { file: img.file } : { data: img.data }) };
        }
        case "line":
        case "arrow":
        case "arrow2":
          return { ...base, x1: o.x1, y1: o.y1, x2: o.x2, y2: o.y2, color: o.color, width: o.width, dash: o.dash || "solid" };
        case "pen":
        case "pencil":
        case "marker":
        case "whitener":
          return { ...base, points: o.points, color: o.color, width: o.width };
        default:
          return { ...base, x: o.x, y: o.y, w: o.w, h: o.h, color: o.color || "", fill: o.fill || "", width: o.width || 0, dash: o.dash || "solid",
                   ...(o.type === "note" || o.type === "stamp" ? { text: o.text || "" } : {}), ...(o.type === "link" ? { url: o.url || "" } : {}) };
      }
    });
  }
  const savedDialog = $("#ed-saved");
  $$("[data-saved-close]", savedDialog).forEach((b) => b.addEventListener("click", () => savedDialog.close()));
  async function save() {
    if (editingId !== null) finishEditing();
    if (!objects.length) { toast("Add something to a page first."); return; }
    const bare = objects.find((o) => o.type === "link" && !String(o.url || "").trim());
    if (bare) { selectedId = bare.id; renderAll(); toast("Type the web address of the link in the bar above (or delete the link).", "error"); return; }
    const btn = $('[data-ed="save"]', root);
    btn.disabled = true;
    const label = btn.lastChild.textContent;
    btn.lastChild.textContent = " Saving…";
    try {
      const name = file.name.replace(/\.pdf$/i, "") + " (edited).pdf";
      const { run } = await api("/api/run", { tool: "edit", files: [file.id], params: { ops: ops(), output_name: name } });
      const done = await poll(run, () => {});
      const box = $("[data-saved-result]", savedDialog);
      resultCard(box, done);
      const out = (done.outputs || [])[0];
      const open = $("[data-saved-open]", savedDialog);
      open.hidden = !out;
      if (out) open.href = `/edit/${out.id}`;
      if (done.status === "done") saved = true;
      savedDialog.showModal();
      renderAll();
    } catch (err) {
      toast(err.message, "error");
    } finally {
      btn.disabled = false;
      btn.lastChild.textContent = label;
    }
  }

  layout();
  setTool(new URLSearchParams(location.search).get("tool") === "draw" ? "select" : "edittext");
})();
