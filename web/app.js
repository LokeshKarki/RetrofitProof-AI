/* RetrofitProof AI dashboard. Vanilla JS, no build step, works offline.
   All engineering logic lives on the server (core/); this file only edits and displays. */
"use strict";

const S = { circuit: null, result: null, roi: {}, roiDefaults: {}, alt: "instrumented",
            caseName: null, image: null, seq: 0, timer: null, evalRows: null };

const SECTIONS = [
  ["review", "Review extraction"], ["risk", "Risk flags & graph"], ["sizing", "Sizing"],
  ["alts", "Retrofit alternatives"], ["tests", "Tests & I/O"], ["roi", "ROI & export"], ["eval", "Evaluation"]];
const COMP_TYPES = ["cylinder", "valve", "service_unit", "sensor", "fitting", "other", "pressure_switch", "gauge", "regulator",
  "shutoff_valve", "shuttle_valve", "silencer", "manifold", "vacuum_ejector", "vacuum_pad", "accessory"];
const ALT_TITLES = { like_for_like: "Like-for-like", instrumented: "Instrumented upgrade", electrification: "Electrification (screening)" };

/* ---------- tiny DOM helpers ---------- */
const PROPS = new Set(["value", "checked", "selected", "disabled"]);
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (PROPS.has(k)) el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const NS = "http://www.w3.org/2000/svg";
function sv(tag, attrs, ...kids) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null) el.setAttribute(k, v);
  for (const kid of kids.flat(Infinity)) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}
const $ = (s, el = document) => el.querySelector(s);
const inr = n => n == null ? "n/a" : "₹" + Math.round(n).toLocaleString("en-IN");
function toast(msg, err) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toast.t); toast.t = setTimeout(() => t.className = "toast", err ? 6000 : 2600);
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText; try { msg = (await r.json()).error || msg; } catch (_) {}
    throw new Error(msg);
  }
  return r;
}
function statusChip(s) {
  s = s || "";
  const cls = s.startsWith("ESCALATED") || s.startsWith("Rejected") ? "bad" : s.startsWith("Provisional") ? "warn"
    : s.startsWith("Cross-reference: direct") ? "ok" : s.startsWith("Cross-reference") ? "warn"
    : s.startsWith("No match") ? "bad" : s.startsWith("Passed") ? "ok" : s.startsWith("SCREENING") ? "info" : "grey";
  return h("span", { class: "chip " + cls }, s || "-");
}
function verifChip(v) {
  return h("span", { class: "chip " + (v === "verified" ? "ok" : (v === "illustrative" || v === "-" || !v) ? "grey" : "warn") }, v || "-");
}
function evidenceText(l) {
  const x = l.xref, t = x && x.candidates && x.candidates[0];
  if (!t) return null;
  if (t.method === "series_rule") return "series pattern, never seen in a request";
  return `seen in ${t.n_requests} request${t.n_requests === 1 ? "" : "s"}, ${t.n_agree} agree` + (t.curated ? " · curated" : "");
}
const altByName = n => S.result && S.result.alternatives.find(a => a.name === n);

/* ---------- state -> server ---------- */
function cleanCircuit() {
  const strip = o => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== "" && v != null));
  const c = S.circuit;
  return {
    components: c.components.map(strip).filter(x => x.id),
    connections: c.connections.map(strip).filter(x => x.from_component && x.to_component),
    io: c.io.map(strip).filter(x => x.tag),
    requirements: strip(c.requirements || {}),
    unknowns: c.unknowns || [], assumptions: c.assumptions || []
  };
}
function scheduleAnalyze() { clearTimeout(S.timer); S.timer = setTimeout(runAnalyze, 300); }
async function runAnalyze() {
  const my = ++S.seq;
  try {
    const r = await (await api("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ circuit: cleanCircuit(), roi_inputs: S.roi }) })).json();
    if (my !== S.seq) return;                     // a newer edit superseded this response
    S.result = r; renderResults();
  } catch (e) { toast("Analysis failed: " + e.message, true); }
}

/* ---------- navigation ---------- */
function buildNav() {
  const nav = $("#steps");
  SECTIONS.forEach(([id, label], i) => nav.append(h("button", { class: "step", "data-id": id, onclick: () => show(id) },
    h("span", { class: "n" }, i + 1), label, h("span", { class: "badge", id: "badge-" + id, style: "display:none" }))));
  const start = location.hash.slice(1);
  show(SECTIONS.some(s => s[0] === start) ? start : "review");
}
function show(id) {
  document.querySelectorAll(".sec").forEach(s => s.classList.toggle("active", s.id === "s-" + id));
  document.querySelectorAll(".step").forEach(s => s.classList.toggle("active", s.dataset.id === id));
  history.replaceState(null, "", "#" + id);
  if (id === "eval" && !S.evalRows) loadEval();
}

/* ---------- editable tables ---------- */
const COMP_COLS = [
  { key: "id", label: "ID", cls: "id", w: 60 }, { key: "callout_no", label: "Item", type: "number", w: 56 }, { key: "type", label: "Type", w: 130, type: "select", options: COMP_TYPES },
  { key: "label", label: "Label", w: 150 }, { key: "manufacturer", label: "Maker", w: 80 }, { key: "part_number", label: "Legacy part no.", w: 150 },
  { key: "qty", label: "Qty", type: "number", w: 50 },
  { key: "bore_mm", label: "Bore mm", type: "number" }, { key: "stroke_mm", label: "Stroke mm", type: "number" },
  { key: "load_kg", label: "Load kg", type: "number" }, { key: "stroke_time_s", label: "Stroke s", type: "number" },
  { key: "pressure_bar", label: "Press. bar", type: "number" }, { key: "valve_function", label: "Valve function", w: 220 },
  { key: "voltage", label: "Voltage", w: 80 },
  { key: "safety_status", label: "Safety status", w: 250, type: "select", options: ["unknown", "potentially_safety_related", "engineer_confirmed_safety_related", "engineer_confirmed_not_safety_related"] },
  { key: "confidence", label: "Conf.", type: "number", step: "0.05" }];
const CONN_COLS = [
  { key: "from_component", label: "From" }, { key: "from_port", label: "Port" }, { key: "to_component", label: "To" },
  { key: "to_port", label: "Port" }, { key: "connection_type", label: "Kind", type: "select", options: ["pneumatic", "signal"] },
  { key: "confidence", label: "Conf.", type: "number", step: "0.05" }];
const IO_COLS = [
  { key: "tag", label: "Tag" }, { key: "direction", label: "Dir", type: "select", options: ["DI", "DO", "AI", "AO"] },
  { key: "linked_component", label: "Component" }, { key: "description", label: "Description", w: 170 }, { key: "plc_address", label: "PLC address" }];
const compRowCls = r => (r.safety_status || "").includes("safety_related") && !(r.safety_status || "").includes("not_") ? "safe"
  : (r.confidence != null && r.confidence !== "" && r.confidence < 0.7) ? "low" : "";

function editTable(key, cols, blank, rowCls) {
  const tbody = h("tbody");
  const draw = () => tbody.replaceChildren(...S.circuit[key].map((row, i) => {
    const tr = h("tr", { class: rowCls ? rowCls(row) : "" });
    const touch = () => { if (rowCls) tr.className = rowCls(row); scheduleAnalyze(); };
    cols.forEach(c => {
      let inp;
      if (c.type === "select") {
        inp = h("select", { onchange: e => { row[c.key] = e.target.value; touch(); } },
          c.options.map(o => h("option", { value: o }, o)));
        inp.value = row[c.key] ?? c.options[0];
      } else {
        inp = h("input", { type: c.type === "number" ? "number" : "text", step: c.step || "any", value: row[c.key] ?? "",
          onchange: e => { const v = e.target.value; row[c.key] = c.type === "number" ? (v === "" ? null : Number(v)) : v; touch(); } });
      }
      tr.append(h("td", { class: c.cls || "", style: c.w ? `min-width:${c.w}px` : null }, inp));
    });
    tr.append(h("td", null, h("button", { class: "btn ghost small", title: "Delete row", onclick: () => {
      S.circuit[key].splice(i, 1); draw(); scheduleAnalyze(); } }, "✕")));
    return tr;
  }));
  draw();
  const table = h("table", { class: "edit" }, h("thead", null, h("tr", null, cols.map(c => h("th", null, c.label)), h("th"))), tbody);
  return h("div", null, h("div", { class: "tablewrap" }, table),
    h("div", { style: "margin-top:8px" }, h("button", { class: "btn ghost small", onclick: () => {
      S.circuit[key].push(structuredClone(blank)); draw(); } }, "+ Add row")));
}

/* ---------- 1. review ---------- */
function renderReview() {
  const sec = $("#s-review"); sec.replaceChildren();
  const cases = S.cases || [];
  const sel = h("select", { onchange: e => loadCase(e.target.value) }, cases.map(c => h("option", { value: c }, c)));
  sel.value = S.caseName || "";
  const imgIn = h("input", { type: "file", accept: "image/*,.pdf,application/pdf" });
  const ioIn = h("input", { type: "file", accept: ".csv,.xlsx" });
  const btn = h("button", { class: "btn" }, "Extract with AI");
  btn.onclick = async () => {
    if (!imgIn.files[0]) return toast("Choose a diagram image or PDF first", true);
    const fd = new FormData(); fd.append("image", imgIn.files[0]); if (ioIn.files[0]) fd.append("io", ioIn.files[0]);
    btn.disabled = true; btn.replaceChildren(h("span", { class: "spin" }), "Reading diagram…");
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 180000); // 3 minutes for large drawings / model latency
    try {
      toast("Uploading diagram and starting extraction…");
      const response = await api("/api/extract", {
        method: "POST", body: fd, signal: controller.signal
      });
      const payload = await response.json();
      if (!payload.circuit) throw new Error(payload.error || "Server returned no extracted circuit.");
      S.circuit = payload.circuit;
      S.caseName = null;
      S.image = imgIn.files[0].type.startsWith("image/") ? URL.createObjectURL(imgIn.files[0]) : null;
      renderReview();
      await runAnalyze();
      toast("Extraction complete. Review low-confidence and amber rows.");
    } catch (e) {
      const message = e.name === "AbortError"
        ? "Extraction timed out after 3 minutes. Check PDF size, Gemini API key/model, and server terminal."
        : e.message;
      toast("Extraction failed: " + message, true);
      btn.disabled = false;
      btn.textContent = "Extract with AI";
    } finally {
      clearTimeout(timeoutId);
    }
  };
  const low = S.circuit.components.filter(c => c.confidence < 0.7).map(c => c.id);
  const req = S.circuit.requirements || (S.circuit.requirements = {});
  const reqField = (k, label, num) => h("label", { class: "field" }, label,
    h("input", { type: num ? "number" : "text", step: "any", value: req[k] ?? "",
      onchange: e => { const v = e.target.value; req[k] = v === "" ? null : (num ? Number(v) : v); scheduleAnalyze(); } }));

  sec.append(h("h2", null, "1 · Review extraction"),
    h("p", { class: "sub" }, "The AI reads the diagram and I/O list. A technician corrects anything it got wrong before any analysis is trusted."),
    h("div", { class: "grid g2" },
      h("div", { class: "card" }, h("h3", null, "Input"),
        h("div", { class: "row" }, h("label", { class: "field" }, "Demo case", sel)),
        h("p", { class: "small muted" }, "Demo extractions are simulated with deliberate errors so the correction step has something to fix."),
        h("div", { class: "dz" }, h("div", { class: "row" },
          h("label", { class: "field" }, "Diagram (image or PDF)", imgIn), h("label", { class: "field" }, "I/O list (CSV/XLSX)", ioIn), btn))),
      h("div", { class: "card" }, h("h3", null, "Diagram"),
        S.image ? h("img", { class: "preview", src: S.image, alt: "Legacy diagram" })
          : h("div", { class: "noimg" }, "No diagram image for this case. Upload one on the left, or add data/cases/<case>/diagram.png"))),
    low.length ? h("div", { class: "note warn" }, "Low-confidence components, verify against the drawing: ", h("b", null, low.join(", "))) : null,
    h("div", { class: "card" }, h("h3", null, "Components"), h("p", { class: "small muted" }, "Amber = confidence below 0.7. Red = potential safety function. Edits re-run the analysis automatically."),
      editTable("components", COMP_COLS, { id: "", type: "cylinder", qty: 1, safety_status: "unknown", confidence: 1 }, compRowCls)),
    h("div", { class: "grid g2" },
      h("div", { class: "card" }, h("h3", null, "Connections (port-level)"),
        editTable("connections", CONN_COLS, { from_component: "", to_component: "", connection_type: "pneumatic", confidence: 1 })),
      h("div", { class: "card" }, h("h3", null, "I/O list"),
        editTable("io", IO_COLS, { tag: "", direction: "DI" }))),
    h("div", { class: "card" }, h("h3", null, "Circuit requirements"),
      h("div", { class: "row" }, reqField("supply_pressure_bar", "Supply pressure (bar)", true), reqField("target_controller", "Target controller"),
        reqField("existing_controller", "Existing controller"), reqField("cycles_per_hour", "Cycles / hour", true))));
}

/* ---------- 2. risk + graph ---------- */
function layoutGraph(c) {
  const comps = c.components, n = comps.length, depth = Object.fromEntries(comps.map(x => [x.id, 0]));
  const ids = new Set(comps.map(x => x.id));
  const pneu = c.connections.filter(k => k.connection_type === "pneumatic" && ids.has(k.from_component) && ids.has(k.to_component));
  for (let pass = 0; pass < n; pass++) pneu.forEach(k => { if (depth[k.to_component] < depth[k.from_component] + 1 && depth[k.from_component] + 1 <= n) depth[k.to_component] = depth[k.from_component] + 1; });
  const maxD = Math.max(0, ...comps.filter(x => x.type !== "sensor").map(x => depth[x.id]));
  comps.filter(x => x.type === "sensor").forEach(s => {
    const links = c.connections.filter(k => k.connection_type === "signal" && (k.from_component === s.id || k.to_component === s.id));
    const cyl = links.map(k => k.from_component === s.id ? k.to_component : k.from_component).filter(i => ids.has(i));
    depth[s.id] = cyl.length ? Math.max(...cyl.map(i => depth[i])) + 1 : maxD + 1;
  });
  const cols = {}; comps.forEach(x => (cols[depth[x.id]] ||= []).push(x));
  const W = 168, H = 58, GX = 92, GY = 30, pos = {};
  const maxRows = Math.max(1, ...Object.values(cols).map(a => a.length));
  Object.keys(cols).sort((a, b) => a - b).forEach(d => cols[d].forEach((x, i) => {
    const off = (maxRows - cols[d].length) * (H + GY) / 2;
    pos[x.id] = { x: 20 + d * (W + GX), y: 20 + off + i * (H + GY) };
  }));
  const ncol = Object.keys(cols).length;
  return { pos, W, H, width: 40 + ncol * (W + GX) - GX, height: 40 + maxRows * (H + GY) - GY };
}
const TYPE_COL = { cylinder: "#0b6fb8", valve: "#7a4fd0", service_unit: "#2a9d8f", sensor: "#6c757d", fitting: "#6c757d", other: "#6c757d",
  shutoff_valve: "#7a4fd0", shuttle_valve: "#7a4fd0", regulator: "#2a9d8f", manifold: "#7a4fd0", pressure_switch: "#6c757d", gauge: "#6c757d",
  silencer: "#9aa7ba", accessory: "#9aa7ba", vacuum_ejector: "#c2570c", vacuum_pad: "#c2570c" };
function graphSvg(c, flags) {
  const L = layoutGraph(c), worst = {};
  flags.forEach(f => { const r = { critical: 3, warning: 2, info: 1 }; if ((r[f.severity] || 0) > (worst[f.component_id] || 0)) worst[f.component_id] = r[f.severity]; });
  const svg = sv("svg", { class: "graph", viewBox: `0 0 ${L.width} ${L.height}`, role: "img", "aria-label": "Circuit graph" });
  svg.append(sv("defs", null, sv("marker", { id: "arr", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" },
    sv("path", { d: "M0 0L10 5L0 10z", fill: "#7b8aa0" }))));
  const seen = {};
  c.connections.forEach(k => {
    const a = L.pos[k.from_component], b = L.pos[k.to_component]; if (!a || !b) return;
    const pair = [k.from_component, k.to_component].sort().join("|"), idx = (seen[pair] = (seen[pair] ?? -1) + 1);
    const back = b.x < a.x, dy = idx * 9 - 4;
    const x1 = back ? a.x : a.x + L.W, x2 = back ? b.x + L.W : b.x, y1 = a.y + L.H / 2 + dy, y2 = b.y + L.H / 2 + dy, mx = (x1 + x2) / 2;
    const sig = k.connection_type === "signal";
    svg.append(sv("path", { d: `M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}`, fill: "none", stroke: sig ? "#9aa7ba" : "#5b6f8c",
      "stroke-width": sig ? 1.4 : 2, "stroke-dasharray": sig ? "5 4" : null, "marker-end": "url(#arr)", opacity: k.confidence < 0.7 ? 0.45 : 1 }));
    const lab = sig ? "" : `${k.from_port || "?"}→${k.to_port || "?"}`;
    if (lab) svg.append(sv("text", { x: mx, y: (y1 + y2) / 2 - 5, "text-anchor": "middle", "font-size": 10.5, fill: "#44546a", stroke: "#fbfcfe", "stroke-width": 3, "paint-order": "stroke" }, lab));
  });
  c.components.forEach(x => {
    const p = L.pos[x.id], safe = (x.safety_status || "").includes("safety_related") && !(x.safety_status || "").includes("not_");
    const w = worst[x.id], stroke = safe ? "#b3261e" : w === 2 ? "#e0a030" : "#c9d3e1";
    const g = sv("g", { transform: `translate(${p.x},${p.y})` }, sv("title", null, `${x.id}: ${x.label || x.type}`));
    g.append(sv("rect", { width: L.W, height: L.H, rx: 10, fill: safe ? "#fde7e5" : "#fff", stroke, "stroke-width": safe || w === 2 ? 2.2 : 1.4 }),
      sv("rect", { width: 6, height: L.H, rx: 3, fill: TYPE_COL[x.type] || "#6c757d" }),
      sv("text", { x: 16, y: 22, "font-size": 13, "font-weight": 700, fill: "#14213d" }, x.id),
      sv("text", { x: 16, y: 38, "font-size": 11, fill: "#44546a" }, (x.label || x.type).slice(0, 24)),
      sv("text", { x: 16, y: 51, "font-size": 10, fill: "#7b8aa0" },
        (x.type === "cylinder" ? `Ø${x.bore_mm ?? "?"} × ${x.stroke_mm ?? "?"} mm` : x.type === "valve" ? (x.valve_function || "function ?").slice(0, 26) : x.type).replace("_", " ")));
    if (safe) g.append(sv("rect", { x: L.W - 52, y: 6, width: 46, height: 15, rx: 7, fill: "#b3261e" }), sv("text", { x: L.W - 29, y: 17, "text-anchor": "middle", "font-size": 9.5, "font-weight": 700, fill: "#fff" }, "SAFETY"));
    svg.append(g);
  });
  return svg;
}
function renderRisk() {
  const r = S.result, sec = $("#s-risk"); sec.replaceChildren();
  const count = s => r.flags.filter(f => f.severity === s).length;
  sec.append(h("h2", null, "2 · Risk flags & circuit graph"),
    h("p", { class: "sub" }, "Deterministic checks on the corrected circuit. Anything that may be a safety function is escalated and never substituted."),
    h("div", { class: "metrics" },
      h("div", { class: "metric" }, h("div", { class: "l" }, "Critical"), h("div", { class: "v", style: "color:var(--bad)" }, count("critical"))),
      h("div", { class: "metric" }, h("div", { class: "l" }, "Warnings"), h("div", { class: "v", style: "color:var(--warn)" }, count("warning"))),
      h("div", { class: "metric" }, h("div", { class: "l" }, "Info"), h("div", { class: "v", style: "color:var(--info)" }, count("info")))),
    h("div", { class: "card" }, h("h3", null, "Circuit graph"),
      h("div", { class: "legend" }, h("span", null, h("i", { style: "background:#0b6fb8" }), "Cylinder"), h("span", null, h("i", { style: "background:#7a4fd0" }), "Valve"),
        h("span", null, h("i", { style: "background:#2a9d8f" }), "Service unit / regulator"), h("span", null, h("i", { style: "background:#c2570c" }), "Vacuum"), h("span", null, h("i", { style: "background:#6c757d" }), "Sensor"),
        h("span", null, h("i", { style: "background:#b3261e" }), "Safety, not substituted"), h("span", null, "Dashed = signal · faded = low-confidence link")),
      graphSvg(r.circuit, r.flags)),
    h("div", { class: "card" }, h("h3", null, "Flags"),
      r.flags.length ? r.flags.map(f => h("div", { class: "flag " + f.severity }, h("div", { class: "who" }, f.component_id),
        h("div", null, h("div", { class: "code" }, f.code), f.message))) : h("p", { class: "muted" }, "No flags.")));
}

/* ---------- 3. sizing ---------- */
function renderSizing() {
  const sec = $("#s-sizing"); sec.replaceChildren();
  sec.append(h("h2", null, "3 · Sizing assessment"),
    h("p", { class: "sub" }, "Screening-level calculations with every input and assumption visible. Final sizing requires engineering verification."));
  const items = Object.values(S.result.sizing);
  if (!items.length) sec.append(h("div", { class: "card muted" }, "No cylinders to size."));
  const g = h("div", { class: "grid g2" });
  items.forEach(s => {
    const i = s.inputs, ok = s.existing_bore_ok;
    g.append(h("div", { class: "card" },
      h("div", { class: "row", style: "justify-content:space-between" }, h("h3", null, "Cylinder " + s.component_id),
        ok == null ? h("span", { class: "chip grey" }, "unresolved") : h("span", { class: "chip " + (ok ? "ok" : "bad") }, ok ? "existing bore OK" : "existing bore too small")),
      h("table", null, h("tbody", null, [
        ["Load", i.load_kg != null ? i.load_kg + " kg" : "unknown"], ["Pressure", i.pressure_bar != null ? i.pressure_bar + " bar" : "unknown"],
        ["Safety factor / efficiency", `${i.safety_factor} / ${i.efficiency}`], ["Existing bore", i.existing_bore_mm != null ? i.existing_bore_mm + " mm" : "unknown"],
        ["Theoretical bore needed", s.required_bore_mm != null ? s.required_bore_mm + " mm" : "unresolved"],
        ["Stroke / time", `${i.stroke_mm ?? "?"} mm / ${i.stroke_time_s ?? "?"} s`],
        ["Avg. air flow needed", s.required_flow_lpm != null ? s.required_flow_lpm + " l/min (free air)" : "unresolved"]
      ].map(([a, b]) => h("tr", null, h("td", { class: "muted" }, a), h("td", null, h("b", null, b)))))),
      s.notes.map(n => h("div", { class: "note warn", style: "margin-top:10px" }, n)),
      h("div", { class: "small", style: "margin-top:10px" }, h("b", null, "Assumptions"), h("ul", { class: "tight" }, s.assumptions.map(a => h("li", null, a)))),
      h("div", { class: "small", style: "margin-top:8px" }, h("b", null, "Still to verify"), h("ul", { class: "tight" }, s.verify.map(a => h("li", null, a))))));
  });
  sec.append(g);
}

/* ---------- 4. alternatives ---------- */
function altSwitch(onPick) {
  return h("div", { class: "seg" }, S.result.alternatives.map(a => h("button", { class: a.name === S.alt ? "on" : "",
    onclick: () => { S.alt = a.name; renderResults(); } }, ALT_TITLES[a.name] || a.title)));
}
async function sendFeedback(l, cand, verdict) {
  try {
    await api("/api/xref/confirm", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ competitor: l.legacy_mfr, code: l.legacy_part, festo_type_code: cand, verdict }) });
    toast(verdict === "confirm" ? "Saved: this answer will rank first next time" : "Saved: this answer is marked as wrong");
    runAnalyze();
  } catch (e) { toast(e.message, true); }
}
function festoCell(l) {
  const x = l.xref, ev = evidenceText(l), t = x && x.candidates && x.candidates[0];
  return h("td", null, h("b", null, l.part_number),
    l.festo_article_no ? h("div", { class: "small muted" }, "Art. " + l.festo_article_no) : null,
    ev ? h("div", { class: "small muted" }, ev) : null,
    x && x.conflict ? h("div", { class: "small", style: "color:var(--warn)" }, "other answers seen, expand row") : null,
    x && x.note && l.part_number === "-" ? h("div", { class: "small muted" }, x.note) : null);
}
function gapsPanel() {
  const g = S.result.gaps || [];
  if (!g.length) return null;
  return h("div", { class: "card" }, h("h3", null, `Gaps: ${g.length} item${g.length === 1 ? "" : "s"} with no Festo answer`),
    h("p", { class: "small muted" }, "Nothing is guessed. These need a datasheet, a code on the drawing, or an engineer's decision. Same-series codes are hints only."),
    h("table", null, h("thead", null, h("tr", null, ["Item(s)", "Competitor code", "Role", "Why", "Hints (not matches)"].map(x => h("th", null, x)))),
      h("tbody", null, g.map(x => h("tr", null, h("td", null, h("b", null, x.legacy_ids.join(", "))), h("td", null, x.legacy_part || "none on drawing"),
        h("td", null, x.role), h("td", { class: "muted" }, x.reason),
        h("td", { class: "small" }, (x.similar || []).map(y => h("div", null, `${y.code} → ${y.festo_type_code}`))))))));
}
function renderAlts() {
  const sec = $("#s-alts"); sec.replaceChildren();
  const a = altByName(S.alt), rows = a.bom_grouped || a.bom;
  sec.append(h("h2", null, "4 · Retrofit alternatives"),
    h("p", { class: "sub" }, "Part matches come from real earlier cross-reference requests, with evidence counts. Sizing and function checks are deterministic. Click a row to see the checks."),
    altSwitch(renderAlts), ...a.notes.map(n => h("div", { class: "note" }, n)), gapsPanel());
  const tb = h("tbody");
  rows.forEach(l => {
    const real = l.xref && l.xref.candidates && l.xref.candidates.length && l.part_number !== "-" && l.verification_status === "cross-reference";
    const top = real ? l.xref.candidates[0] : null;
    const btns = real ? h("td", { style: "white-space:nowrap" },
      h("button", { class: "btn ghost small", title: "Technician confirms this answer", onclick: e => { e.stopPropagation(); sendFeedback(l, l.part_number, "confirm"); } }, "Confirm"), " ",
      h("button", { class: "btn ghost small", title: "Technician rejects this answer", onclick: e => { e.stopPropagation(); sendFeedback(l, l.part_number, "reject"); } }, "Reject")) : h("td");
    const tr = h("tr", { class: (l.checks.length || l.xref) ? "clickable" : "" },
      h("td", null, h("b", null, l.legacy_id)), h("td", null, l.role.replaceAll("_", " ")),
      h("td", null, l.legacy_part || h("span", { class: "muted" }, "-"), l.legacy_label ? h("div", { class: "small muted" }, l.legacy_label) : null),
      festoCell(l), h("td", null, l.qty), h("td", null, statusChip(l.status)),
      h("td", { class: "small muted" }, top && top.remark ? top.remark : l.rationale), btns);
    tb.append(tr);
    const alts = l.xref && l.xref.candidates && l.xref.candidates.length > 1 ? l.xref.candidates.slice(1) : [];
    const sim = l.xref && l.xref.similar && l.xref.similar.length ? l.xref.similar : [];
    if (l.checks.length || alts.length || sim.length) {
      const ex = h("tr", { class: "expand", style: "display:none" }, h("td", { colspan: 8 },
        h("div", { class: "checks" }, l.checks.map(c => h("div", { class: "check" },
          h("span", { class: "sym " + c.status }, c.status === "passed" ? "✓" : c.status === "failed" ? "✕" : "?"), h("b", null, c.check), h("span", { class: "muted" }, c.detail))),
          alts.map(c => h("div", { class: "check" }, h("span", { class: "sym unknown" }, "?"), h("b", null, "Other answer"),
            h("span", { class: "muted" }, `${c.festo_type_code}: ${c.tier_label}, seen in ${c.n_requests} request(s)`),
            h("button", { class: "btn ghost small", style: "margin-left:8px", onclick: () => sendFeedback(l, c.festo_type_code, "confirm") }, "Use this one"))),
          sim.length ? h("div", { class: "check" }, h("span", { class: "sym unknown" }, "i"), h("b", null, "Hints, not matches"),
            h("span", { class: "muted" }, sim.map(y => `${y.code} → ${y.festo_type_code}`).join(" · "))) : null)));
      tr.addEventListener("click", () => ex.style.display = ex.style.display === "none" ? "" : "none");
      tb.append(ex);
    }
  });
  sec.append(h("div", { class: "card" }, h("div", { class: "tablewrap", style: "max-height:none" }, h("table", null,
    h("thead", null, h("tr", null, ["Item(s)", "Role", "Competitor part", "Festo type code", "Qty", "Status", "Differences / rationale", ""].map(x => h("th", null, x)))), tb)),
    h("p", { class: "small muted" }, "Cross-reference answers are what Festo engineers replied in earlier requests (or what a series pattern suggests). They are not datasheet-verified. DEMO-* parts come from the placeholder sizing catalog.")));
}

/* ---------- 5. tests + I/O ---------- */
function renderTests() {
  const sec = $("#s-tests"); sec.replaceChildren();
  const a = altByName(S.alt);
  const filter = h("input", { type: "text", placeholder: "Filter tests (component, requirement…)", style: "padding:7px 9px;border:1px solid var(--line);border-radius:8px;min-width:260px" });
  const tb = h("tbody");
  const draw = () => {
    const q = filter.value.toLowerCase();
    tb.replaceChildren(...a.tests.filter(t => !q || JSON.stringify(t).toLowerCase().includes(q)).map(t => h("tr", null,
      h("td", null, h("b", null, t.test_id)), h("td", null, t.component), h("td", null, t.requirement), h("td", null, h("b", null, t.test)),
      h("td", null, t.procedure), h("td", null, t.pass_criterion), h("td", null, t.validates_assumption), h("td", null, statusChip(t.status.startsWith("Proposed") ? "Proposed" : t.status)))));
  };
  filter.addEventListener("input", draw); draw();
  sec.append(h("h2", null, "5 · Test plan & I/O migration"),
    h("p", { class: "sub" }, "Every test names the requirement or assumption it verifies. All are proposals until an engineer approves the acceptance criteria."),
    altSwitch(renderTests),
    h("div", { class: "card" }, h("h3", null, `I/O migration (${a.io.length} points)`), h("div", { class: "tablewrap" }, h("table", null,
      h("thead", null, h("tr", null, ["Old tag", "Dir", "Component", "Old address", "New channel", "Note"].map(x => h("th", null, x)))),
      h("tbody", null, a.io.map(r => h("tr", null, h("td", null, h("b", null, r.old_tag)), h("td", null, r.direction), h("td", null, r.component || "-"),
        h("td", null, r.old_address || "-"), h("td", null, String(r.new_channel).startsWith("HOLD") ? h("span", { class: "chip bad" }, r.new_channel) : r.new_channel), h("td", { class: "muted" }, r.note))))))),
    h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:space-between" }, h("h3", null, `FAT/SAT proposals (${a.tests.length})`), filter),
      h("div", { class: "tablewrap", style: "max-height:520px" }, h("table", null,
        h("thead", null, h("tr", null, ["ID", "Component", "Requirement", "Test", "Procedure", "Pass criterion", "Validates", "Status"].map(x => h("th", null, x)))), tb))));
}

/* ---------- 6. ROI + export ---------- */
function renderRoi() {
  const sec = $("#s-roi"); sec.replaceChildren();
  const alts = S.result.alternatives, a = altByName(S.alt), r = a.roi, maxHw = Math.max(1, ...alts.map(x => x.roi.hardware_cost_inr));
  const inputs = h("div", { class: "grid g3" }, Object.keys(S.roiDefaults).map(k => h("label", { class: "field" }, k.replaceAll("_", " "),
    h("input", { type: "number", step: "any", value: S.roi[k], onchange: e => { S.roi[k] = Number(e.target.value); scheduleAnalyze(); } }))));
  const dl = h("button", { class: "btn" }, "Download Excel report");
  dl.onclick = async () => {
    dl.disabled = true;
    try {
      const res = await api("/api/export", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ circuit: cleanCircuit(), roi_inputs: S.roi, alt: S.alt }) });
      const url = URL.createObjectURL(await res.blob()), a2 = h("a", { href: url, download: `retrofit_${S.alt}.xlsx` });
      document.body.append(a2); a2.click(); a2.remove(); URL.revokeObjectURL(url); toast("Report downloaded");
    } catch (e) { toast(e.message, true); } finally { dl.disabled = false; }
  };
  const dlx = h("button", { class: "btn ghost", style: "margin-left:8px" }, "Download cross-reference sheet");
  dlx.onclick = async () => {
    dlx.disabled = true;
    try {
      const res = await api("/api/export_xref", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ circuit: cleanCircuit(), roi_inputs: S.roi }) });
      const url = URL.createObjectURL(await res.blob()), a2 = h("a", { href: url, download: "xref_request_format.xlsx" });
      document.body.append(a2); a2.click(); a2.remove(); URL.revokeObjectURL(url); toast("Cross-reference sheet downloaded");
    } catch (e) { toast(e.message, true); } finally { dlx.disabled = false; }
  };
  sec.append(h("h2", null, "6 · ROI & export"),
    h("p", { class: "sub" }, "Assumption fields are placeholders, not benchmarks. Replace them with customer figures before using any number."),
    altSwitch(renderRoi),
    h("div", { class: "metrics" },
      h("div", { class: "metric" }, h("div", { class: "l" }, "Hardware (illustrative)"), h("div", { class: "v" }, r.hardware_cost_inr > 0 ? inr(r.hardware_cost_inr) : "n/a")),
      h("div", { class: "metric" }, h("div", { class: "l" }, "Investment incl. install"), h("div", { class: "v" }, inr(r.investment_inr))),
      h("div", { class: "metric" }, h("div", { class: "l" }, "Payback"), h("div", { class: "v" }, r.payback_months ? r.payback_months.toFixed(1) + " mo" : "n/a")),
      h("div", { class: "metric" }, h("div", { class: "l" }, "Engineering hours saved / yr"), h("div", { class: "v" }, Math.round(r.eng_hours_saved_per_year)))),
    h("div", { class: "grid g2" },
      h("div", { class: "card" }, h("h3", null, "Assumptions (editable)"), inputs),
      h("div", { class: "card" }, h("h3", null, "Alternatives compared"), h("table", null, h("tbody", null, alts.map(x => h("tr", null,
        h("td", null, h("b", null, ALT_TITLES[x.name])), h("td", { style: "width:45%" }, h("div", { class: "bar" }, h("i", { style: `width:${100 * x.roi.hardware_cost_inr / maxHw}%` }))),
        h("td", null, inr(x.roi.hardware_cost_inr)), h("td", { class: "muted", style: "white-space:nowrap" }, x.roi.payback_months ? x.roi.payback_months.toFixed(1) + " mo" : "n/a"))))),
        h("p", { class: "small muted" }, "Electrification is a screening estimate only."), h("div", { style: "margin-top:12px" }, dl, dlx))));
}

/* ---------- 7. evaluation ---------- */
async function loadEval() {
  try { S.evalRows = await (await api("/api/eval")).json(); renderEval(); } catch (e) { toast(e.message, true); }
  try { S.holdout = await (await api("/api/xref/eval")).json(); renderEval(); } catch (e) { toast("Hold-out evaluation: " + e.message, true); }
}
function holdoutCard() {
  const H = S.holdout;
  if (!H) return h("div", { class: "card muted" }, "Computing the hold-out evaluation on the real request data…");
  const p = v => v == null ? "n/a" : Math.round(v * 100) + "%";
  const row = (a, b, c) => h("tr", null, h("td", null, a), h("td", null, h("b", null, b)), h("td", { class: "muted small" }, c || ""));
  return h("div", { class: "card" }, h("h3", null, "Cross-reference accuracy on held-out real requests"),
    h("p", { class: "small muted" }, `Built from ${H.train_rows.toLocaleString()} rows, tested on ${H.test_rows.toLocaleString()} rows from requests the matcher never saw (${H.split}).`),
    h("table", null, h("tbody", null,
      row("Codes with a Festo answer in the test set", H.match_rows.toLocaleString(), "rows where an engineer gave a Festo type code"),
      row("Share of those whose code was seen before", p(H.seen_code_share), "the long tail: most codes are one-offs"),
      row("Coverage, all codes", p(H.coverage), "got any answer"),
      row("Top-1 correct, all codes", p(H.top1_of_all), "answer equals the engineer's reply"),
      row("Top-1 correct, when it answered", p(H.top1_when_answered), "precision on the answers it gave"),
      row("Coverage on codes seen before", p(H.coverage_seen_codes)),
      row("Top-1 / top-3 on codes seen before", `${p(H.top1_seen_codes)} / ${p(H.top3_seen_codes)}`),
      row("Coverage on codes never seen", p(H.coverage_unseen_codes), "only series rules can answer these"),
      row("Correctly said \"no match\"", p(H.correct_abstain_rate), "where the engineer also found no equivalent"),
      row("Repeated codes with conflicting answers", `${H.repeated_codes_with_conflicting_answers} / ${H.repeated_codes_in_train}`, "ceiling on top-1: Festo itself answered differently across requests"))),
    h("p", { class: "small muted", style: "margin-top:10px" }, "Series rules were written after looking at this data, so their share is optimistic. The truth is one engineer's reply in one request."));
}
function renderEval() {
  const sec = $("#s-eval"); sec.replaceChildren();
  sec.append(h("h2", null, "7 · Evaluation"), h("p", { class: "sub" }, "Two different things: real hold-out accuracy of the cross-reference, and a pipeline smoke test."),
    holdoutCard(),
    h("h3", null, "Pipeline smoke test (synthetic cases)"),
    h("div", { class: "note warn" }, "These cases and baselines were written by the tool's author, and the case 3 expected codes are also loaded into the knowledge base. The scores prove the pipeline runs end to end; they are NOT accuracy evidence. Use the hold-out numbers above."));
  sec.append(h("div", { class: "note" }, "Tool runtime covers the deterministic engine only. It excludes AI extraction latency and the technician's review time, so do not present it as end-to-end time-to-quote. Time the full flow yourself (upload → corrected → exported) and compare that with a timed manual attempt."));
  if (!S.evalRows) return sec.append(h("div", { class: "card muted" }, "Loading…"));
  const pct = v => typeof v === "number" ? (v <= 1 ? Math.round(v * 100) + "%" : v) : v;
  const rows = [["Extraction", null], ["Component precision", "component_precision"], ["Component recall", "component_recall"],
    ["Connection precision", "connection_precision"], ["Connection recall", "connection_recall"],
    ["Pipeline on raw AI extraction", null], ["BOM match precision", "raw_extraction.match_precision"], ["BOM match recall", "raw_extraction.match_recall"],
    ["Safety items escalated", "raw_extraction.safety_escalated"], ["Gap detection recall", "raw_extraction.gap_detection_recall"],
    ["Pipeline after technician correction", null], ["BOM match precision", "after_correction.match_precision"], ["BOM match recall", "after_correction.match_recall"],
    ["Safety items escalated", "after_correction.safety_escalated"], ["Gap detection recall", "after_correction.gap_detection_recall"],
    ["Time (not comparable, see note)", null], ["Engine runtime only (s)", "after_correction.runtime_s"], ["Manual baseline (min, placeholder)", "manual_baseline_min"]];
  sec.append(h("div", { class: "card" }, h("table", null, h("thead", null, h("tr", null, h("th", null, "Metric"), S.evalRows.map(r => h("th", null, r.case)))),
    h("tbody", null, rows.map(([lab, key]) => key === null
      ? h("tr", null, h("td", { colspan: S.evalRows.length + 1, style: "background:#f1f4f9;font-weight:700" }, lab))
      : h("tr", null, h("td", null, lab), S.evalRows.map(r => h("td", null, key.endsWith("runtime_s") || key === "manual_baseline_min" ? r[key] : pct(r[key])))))))));
}

/* ---------- aggregate rendering ---------- */
function renderResults() {
  const r = S.result, a = altByName(S.alt);
  const cov = r.coverage || { pct: null, matched: 0, in_scope: 0, direct: 0 };
  const crit = r.flags.filter(f => f.severity === "critical").length, warn = r.flags.filter(f => f.severity === "warning").length;
  $("#kpis").replaceChildren(
    h("div", { class: "kpi" + (crit ? " crit" : "") }, h("div", { class: "v" }, crit), h("div", { class: "l" }, "Critical flags")),
    h("div", { class: "kpi" }, h("div", { class: "v" }, warn), h("div", { class: "l" }, "Warnings")),
    h("div", { class: "kpi" }, h("div", { class: "v" }, (a.bom_grouped || a.bom).length), h("div", { class: "l" }, "BOM rows")),
    h("div", { class: "kpi" }, h("div", { class: "v" }, cov.pct == null ? "n/a" : Math.round(cov.pct * 100) + "%"), h("div", { class: "l" }, `Matched (${cov.matched}/${cov.in_scope})`)),
    h("div", { class: "kpi" }, h("div", { class: "v" }, cov.direct), h("div", { class: "l" }, "Direct equivalents")),
    h("div", { class: "kpi" }, h("div", { class: "v" }, Math.max(1, Math.round(r.runtime_s * 1000)) + " ms"), h("div", { class: "l" }, "Analysis time")));
  const b = $("#badge-risk"); b.style.display = crit ? "" : "none"; b.textContent = crit;
  renderRisk(); renderSizing(); renderAlts(); renderTests(); renderRoi();
}

/* ---------- boot ---------- */
async function loadCase(name) {
  const d = await (await api("/api/cases/" + encodeURIComponent(name))).json();
  S.circuit = d.circuit; S.caseName = name; S.image = d.image; renderReview(); await runAnalyze();
}
async function boot() {
  try {
    const meta = await (await api("/api/meta")).json();
    $("#banner").textContent = meta.banner; S.roiDefaults = meta.roi_defaults; S.roi = { ...meta.roi_defaults };
    S.cases = await (await api("/api/cases")).json();
    buildNav();
    if (S.cases.length) await loadCase(S.cases[0]); else toast("No demo cases found. Run python tools/make_cases.py", true);
  } catch (e) { toast("Cannot reach the API: " + e.message, true); }
}
boot();
