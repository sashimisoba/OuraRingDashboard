// Small SVG chart helpers for the dashboard. No dependencies.
// Colors are passed as CSS values (e.g. "var(--c-main)") so themes switch in CSS.

const NS = 'http://www.w3.org/2000/svg';
const GAP = 2; // surface gap between touching marks
const RADIUS = 4; // rounded data-end

export function svgEl(tag, attrs = {}, parent) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'text') node.textContent = v;
    else node.setAttribute(k, v);
  }
  if (parent) parent.appendChild(node);
  return node;
}

function niceNum(x, round) {
  const exp = Math.floor(Math.log10(x));
  const f = x / 10 ** exp;
  let nf;
  if (round) nf = f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10;
  else nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
  return nf * 10 ** exp;
}

export function niceScale(min, max, maxTicks = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) { min = 0; max = 1; }
  if (min === max) { const pad = Math.abs(min) * 0.1 || 1; min -= pad; max += pad; }
  const range = niceNum(max - min, false);
  const step = niceNum(range / (maxTicks - 1), true);
  const lo = Math.floor(min / step + 1e-9) * step;
  const hi = Math.ceil(max / step - 1e-9) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Number(v.toFixed(10)));
  return { min: lo, max: hi, ticks, step };
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const finite = (v) => typeof v === 'number' && Number.isFinite(v);

// Rough text width at 11-12px: CJK glyphs are about twice as wide as Latin.
function textWidth(str, size = 12) {
  let w = 0;
  for (const ch of String(str)) w += ch.codePointAt(0) > 0x2e80 ? size : size * 0.6;
  return w;
}

function barPath(x, y, w, h, dir = 'up', r = RADIUS) {
  r = Math.max(0, Math.min(r, w / 2, h));
  if (h <= 0 || w <= 0) return '';
  if (r < 0.5) return `M${x},${y}h${w}v${h}h${-w}Z`;
  if (dir === 'up') {
    return `M${x},${y + h}V${y + r}A${r},${r} 0 0 1 ${x + r},${y}H${x + w - r}A${r},${r} 0 0 1 ${x + w},${y + r}V${y + h}Z`;
  }
  if (dir === 'down') {
    return `M${x},${y}H${x + w}V${y + h - r}A${r},${r} 0 0 1 ${x + w - r},${y + h}H${x + r}A${r},${r} 0 0 1 ${x},${y + h - r}Z`;
  }
  // 'right': square at the left baseline, rounded at the right data-end
  return `M${x},${y}H${x + w - r}A${r},${r} 0 0 1 ${x + w},${y + r}V${y + h - r}A${r},${r} 0 0 1 ${x + w - r},${y + h}H${x}Z`;
}

function createSvg(container, width, height, label) {
  container.replaceChildren();
  const root = svgEl('svg', {
    class: 'viz', width, height, viewBox: `0 0 ${width} ${height}`,
    role: 'img', 'aria-label': label || '',
  }, container);
  return root;
}

export function emptyState(container, message) {
  container.replaceChildren();
  const p = document.createElement('p');
  p.className = 'empty';
  p.textContent = message || 'この期間のデータがありません';
  container.appendChild(p);
}

// ---- tooltip ----------------------------------------------------------------

let tipEl = null;
function tooltipEl() {
  if (!tipEl) {
    tipEl = document.createElement('div');
    tipEl.className = 'viz-tooltip';
    tipEl.setAttribute('role', 'status');
    tipEl.hidden = true;
    document.body.appendChild(tipEl);
  }
  return tipEl;
}

export function showTooltip(anchor, title, rows) {
  const el = tooltipEl();
  el.replaceChildren();
  const t = document.createElement('div');
  t.className = 'viz-tooltip__title';
  t.textContent = title;
  el.appendChild(t);
  for (const r of rows) {
    const row = document.createElement('div');
    row.className = 'viz-tooltip__row';
    if (r.color) {
      const key = document.createElement('span');
      key.className = 'viz-tooltip__key';
      key.style.background = r.color;
      row.appendChild(key);
    }
    const val = document.createElement('strong');
    val.textContent = r.value;
    row.appendChild(val);
    if (r.label) {
      const lab = document.createElement('span');
      lab.className = 'viz-tooltip__label';
      lab.textContent = r.label;
      row.appendChild(lab);
    }
    el.appendChild(row);
  }
  el.hidden = false;
  const pad = 14;
  const w = el.offsetWidth;
  const h = el.offsetHeight;
  let x = anchor.x + pad;
  let y = anchor.y - h - pad;
  if (x + w > window.innerWidth - 8) x = anchor.x - w - pad;
  x = clamp(x, 8, Math.max(8, window.innerWidth - w - 8));
  if (y < 8) y = anchor.y + pad;
  el.style.left = `${x}px`;
  el.style.top = `${y}px`;
}

export function hideTooltip() {
  if (tipEl) tipEl.hidden = true;
}

// Pointer + keyboard scrubbing over an indexed x axis. The pointer only has to be
// nearest an index, never on top of a mark.
function bindIndex(root, { n, xAt, indexAt, anchorY, onShow, onClear }) {
  let current = -1;
  const anchorFor = (i) => {
    const r = root.getBoundingClientRect();
    return { x: r.left + xAt(i), y: r.top + anchorY };
  };
  const show = (i, anchor) => {
    i = clamp(i, 0, n - 1);
    current = i;
    onShow(i, anchor || anchorFor(i));
  };
  const clear = () => {
    current = -1;
    onClear();
    hideTooltip();
  };
  const fromEvent = (e) => {
    const r = root.getBoundingClientRect();
    show(indexAt(e.clientX - r.left), { x: e.clientX, y: e.clientY });
  };
  root.addEventListener('pointermove', fromEvent);
  root.addEventListener('pointerdown', fromEvent);
  root.addEventListener('pointerleave', (e) => { if (e.pointerType !== 'touch') clear(); });
  root.setAttribute('tabindex', '0');
  root.addEventListener('focus', () => show(current >= 0 ? current : n - 1));
  root.addEventListener('blur', clear);
  root.addEventListener('keydown', (e) => {
    const step = e.shiftKey ? 10 : 1;
    if (e.key === 'ArrowLeft') show(current - step);
    else if (e.key === 'ArrowRight') show(current + step);
    else if (e.key === 'Home') show(0);
    else if (e.key === 'End') show(n - 1);
    else if (e.key === 'Escape') clear();
    else return;
    e.preventDefault();
  });
}

// Close a touch-opened tooltip when tapping elsewhere.
document.addEventListener('pointerdown', (e) => {
  if (!(e.target instanceof Element) || !e.target.closest('svg.viz')) hideTooltip();
});

// Evenly spaced labels, counted back from the latest day so it is always labeled.
function xLabelIndices(n, plotW, minGap = 64) {
  if (n <= 0) return [];
  if (n === 1) return [0];
  const maxLabels = Math.max(2, Math.floor(plotW / minGap));
  const step = Math.max(1, Math.ceil((n - 1) / (maxLabels - 1)));
  const out = [];
  for (let i = n - 1; i >= 0; i -= step) out.push(i);
  return out.reverse();
}

function drawYAxis(root, scale, y, left, right, format) {
  for (const t of scale.ticks) {
    const yy = Math.round(y(t)) + 0.5;
    svgEl('line', { class: 'grid-line', x1: left, x2: right, y1: yy, y2: yy }, root);
    svgEl('text', { class: 'tick-y', x: left - 8, y: yy, 'text-anchor': 'end', 'dominant-baseline': 'middle', text: format(t, scale.ticks) }, root);
  }
}

function drawXLabels(root, indices, xAt, label, yPos, left, right) {
  for (const i of indices) {
    const x = xAt(i);
    let anchor = 'middle';
    const text = label(i);
    const half = textWidth(text, 11) / 2;
    if (x - half < left - 4) anchor = 'start';
    else if (x + half > right + 4) anchor = 'end';
    svgEl('text', { class: 'tick-x', x, y: yPos, 'text-anchor': anchor, text }, root);
  }
}

// ---- line chart (shared x = days) -----------------------------------------------

/**
 * spec: { labels: string[], xLabel(i), title(i), series: [{name, color, values, format}],
 *         height, yPad, yClamp: [lo, hi], yFormat, baseline (number), ariaLabel }
 */
export function lineChart(container, spec) {
  const n = spec.labels.length;
  const all = spec.series.flatMap((s) => s.values).filter(finite);
  if (!n || !all.length) return emptyState(container, spec.emptyMessage);
  const width = Math.max(container.clientWidth, 260);
  const height = spec.height || 220;
  const yFormat = spec.yFormat || ((v) => String(v));
  const pad = spec.yPad ?? 0;
  let lo = Math.min(...all) - pad;
  let hi = Math.max(...all) + pad;
  if (spec.baseline !== undefined) { lo = Math.min(lo, spec.baseline); hi = Math.max(hi, spec.baseline); }
  if (spec.yClamp) { lo = Math.max(lo, spec.yClamp[0]); hi = Math.min(hi, spec.yClamp[1]); }
  const scale = niceScale(lo, hi, spec.ticks || 5);
  const m = { top: 12, right: 14, bottom: 26, left: 40 };
  const plotH = height - m.top - m.bottom;
  const y = (v) => m.top + plotH - ((v - scale.min) / (scale.max - scale.min)) * plotH;

  // Direct end labels: value at the end of each line, dropped if any two collide.
  const ends = spec.series.map((s) => {
    for (let i = s.values.length - 1; i >= 0; i--) if (finite(s.values[i])) return { i, v: s.values[i], s };
    return null;
  });
  let direct = spec.directLabels !== false && spec.series.length <= 4 && ends.every(Boolean);
  if (direct) {
    const ys = ends.map((e) => y(e.v)).sort((a, b) => a - b);
    for (let k = 1; k < ys.length; k++) if (ys[k] - ys[k - 1] < 14) direct = false;
  }
  if (direct) {
    const widest = Math.max(...ends.map((e) => textWidth((e.s.format || yFormat)(e.v), 12)));
    m.right = Math.ceil(widest + 18);
  }
  const plotW = width - m.left - m.right;
  const x = (i) => (n === 1 ? m.left + plotW / 2 : m.left + (i * plotW) / (n - 1));

  const root = createSvg(container, width, height, spec.ariaLabel);
  drawYAxis(root, scale, y, m.left, width - m.right, yFormat);
  if (spec.baseline !== undefined) {
    const yy = Math.round(y(spec.baseline)) + 0.5;
    svgEl('line', { class: 'axis-line', x1: m.left, x2: width - m.right, y1: yy, y2: yy }, root);
  }
  drawXLabels(root, xLabelIndices(n, plotW), x, spec.xLabel, height - 6, m.left, width - m.right);

  const showAllDots = n <= 14;
  for (const s of spec.series) {
    let d = '';
    let pen = false;
    s.values.forEach((v, i) => {
      if (!finite(v)) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    svgEl('path', { class: 'series', d, style: `stroke:${s.color}` }, root);
    s.values.forEach((v, i) => {
      if (!finite(v)) return;
      const isolated = !finite(s.values[i - 1]) && !finite(s.values[i + 1]);
      const isEnd = ends.find((e) => e && e.s === s)?.i === i;
      if (showAllDots || isolated || isEnd) svgEl('circle', { class: 'dot', cx: x(i), cy: y(v), r: 4, style: `fill:${s.color}` }, root);
    });
  }
  if (direct) {
    for (const e of ends) {
      svgEl('text', { class: 'end-label', x: x(e.i) + 10, y: y(e.v), 'dominant-baseline': 'middle', text: (e.s.format || yFormat)(e.v) }, root);
    }
  }

  const hover = svgEl('g', { 'pointer-events': 'none' }, root);
  bindIndex(root, {
    n,
    xAt: x,
    indexAt: (lx) => (n === 1 ? 0 : Math.round(((lx - m.left) / plotW) * (n - 1))),
    anchorY: m.top + 10,
    onShow(i, anchor) {
      hover.replaceChildren();
      const xx = Math.round(x(i)) + 0.5;
      svgEl('line', { class: 'crosshair', x1: xx, x2: xx, y1: m.top, y2: m.top + plotH }, hover);
      const rows = [];
      for (const s of spec.series) {
        const v = s.values[i];
        if (finite(v)) svgEl('circle', { class: 'dot', cx: x(i), cy: y(v), r: 4.5, style: `fill:${s.color}` }, hover);
        rows.push({ color: s.color, value: finite(v) ? (s.format || yFormat)(v) : '—', label: s.name });
      }
      showTooltip(anchor, spec.title ? spec.title(i) : spec.labels[i], rows);
    },
    onClear() { hover.replaceChildren(); },
  });
  return root;
}

// ---- columns: single, stacked, or diverging -----------------------------------

/**
 * spec: { labels, xLabel(i), title(i), stacks: [{name, color, values, format}],
 *         down: {name, color, values, format} (optional, drawn below zero),
 *         height, yFormat, totalRow(i) -> {label, value} (optional), labelLast }
 */
export function columnChart(container, spec) {
  const n = spec.labels.length;
  const totals = spec.labels.map((_, i) => spec.stacks.reduce((sum, s) => sum + (finite(s.values[i]) ? s.values[i] : 0), 0));
  const downs = spec.down ? spec.down.values.map((v) => (finite(v) ? v : 0)) : null;
  const hasData = spec.stacks.some((s) => s.values.some(finite)) || (spec.down && spec.down.values.some(finite));
  if (!n || !hasData) return emptyState(container, spec.emptyMessage);

  const width = Math.max(container.clientWidth, 260);
  const height = spec.height || 220;
  const yFormat = spec.yFormat || ((v) => String(v));
  const scale = niceScale(downs ? -Math.max(...downs, 0) : 0, Math.max(...totals, 0) || 1, spec.ticks || 5);
  if (!downs) scale.min = 0;
  const m = { top: spec.labelLast ? 20 : 12, right: 12, bottom: 26, left: 40 };
  const plotW = width - m.left - m.right;
  const plotH = height - m.top - m.bottom;
  const y = (v) => m.top + plotH - ((v - scale.min) / (scale.max - scale.min)) * plotH;
  const band = plotW / n;
  const barW = Math.max(1, Math.min(24, band * 0.62, band - GAP));
  const cx = (i) => m.left + band * (i + 0.5);

  const root = createSvg(container, width, height, spec.ariaLabel);
  drawYAxis(root, { ...scale, ticks: scale.ticks.filter((t) => t >= scale.min) }, y, m.left, width - m.right, (t, ticks) => yFormat(Math.abs(t), ticks));
  const zero = Math.round(y(0)) + 0.5;
  svgEl('line', { class: 'axis-line', x1: m.left, x2: width - m.right, y1: zero, y2: zero }, root);
  drawXLabels(root, xLabelIndices(n, plotW), cx, spec.xLabel, height - 6, m.left, width - m.right);

  const bars = [];
  for (let i = 0; i < n; i++) {
    const g = svgEl('g', { class: 'bar' }, root);
    bars.push(g);
    const x0 = cx(i) - barW / 2;
    const segs = spec.stacks.map((s) => (finite(s.values[i]) && s.values[i] > 0 ? s.values[i] : 0));
    const topIdx = segs.reduce((last, v, k) => (v > 0 ? k : last), -1);
    let cum = 0;
    segs.forEach((v, k) => {
      if (v <= 0) return;
      const yBottom = y(cum) - (cum > 0 ? GAP : downs ? GAP / 2 : 0);
      const yTop = y(cum + v);
      cum += v;
      const h = yBottom - yTop;
      if (h < 0.5) return;
      svgEl('path', { d: barPath(x0, yTop, barW, h, k === topIdx ? 'up' : 'none', k === topIdx ? RADIUS : 0), style: `fill:${spec.stacks[k].color}` }, g);
    });
    if (downs && downs[i] > 0) {
      const yTop = y(0) + GAP / 2;
      const h = y(-downs[i]) - yTop;
      if (h >= 0.5) svgEl('path', { d: barPath(x0, yTop, barW, h, 'down'), style: `fill:${spec.down.color}` }, g);
    }
  }

  if (spec.labelLast) {
    for (let i = n - 1; i >= 0; i--) {
      if (totals[i] > 0) {
        const fmt = spec.stacks.length === 1 && spec.stacks[0].format ? spec.stacks[0].format : yFormat;
        const label = fmt(totals[i]);
        const xx = clamp(cx(i), m.left + textWidth(label, 11) / 2, width - m.right - textWidth(label, 11) / 2 + 8);
        svgEl('text', { class: 'value-label', x: xx, y: y(totals[i]) - 6, 'text-anchor': 'middle', text: label }, root);
        break;
      }
    }
  }

  bindIndex(root, {
    n,
    xAt: cx,
    indexAt: (lx) => Math.floor((lx - m.left) / band),
    anchorY: m.top + 10,
    onShow(i, anchor) {
      root.classList.add('has-hover');
      bars.forEach((g, k) => g.classList.toggle('is-hover', k === i));
      const rows = [];
      if (spec.totalRow) {
        const t = spec.totalRow(i);
        if (t) rows.push(t);
      }
      for (const s of [...spec.stacks].reverse()) {
        const v = s.values[i];
        rows.push({ color: s.color, value: finite(v) ? (s.format || yFormat)(v) : '—', label: s.name });
      }
      if (spec.down) {
        const v = spec.down.values[i];
        rows.push({ color: spec.down.color, value: finite(v) ? (spec.down.format || yFormat)(v) : '—', label: spec.down.name });
      }
      showTooltip(anchor, spec.title ? spec.title(i) : spec.labels[i], rows);
    },
    onClear() {
      root.classList.remove('has-hover');
      bars.forEach((g) => g.classList.remove('is-hover'));
    },
  });
  return root;
}

// ---- horizontal bars (score contributors, 0-100) -------------------------------

/** spec: { items: [{label, value}], max, color, format, flagBelow, flagText } */
export function hBarChart(container, spec) {
  const items = spec.items.filter((it) => finite(it.value));
  if (!items.length) return emptyState(container, spec.emptyMessage);
  const width = Math.max(container.clientWidth, 260);
  const rowH = 28;
  const barH = 10;
  const height = items.length * rowH + 4;
  const labelW = Math.min(132, Math.max(...items.map((it) => textWidth(it.label, 12))) + 12);
  const valueW = spec.flagBelow ? 84 : 40;
  const max = spec.max || 100;
  const x = (v) => labelW + (clamp(v, 0, max) / max) * (width - labelW - valueW);

  const root = createSvg(container, width, height, spec.ariaLabel);
  svgEl('line', { class: 'axis-line', x1: labelW + 0.5, x2: labelW + 0.5, y1: 0, y2: height }, root);
  items.forEach((it, k) => {
    const cy = k * rowH + rowH / 2 + 2;
    svgEl('text', { class: 'row-label', x: 0, y: cy, 'dominant-baseline': 'middle', text: it.label }, root);
    const w = x(it.value) - labelW - 1;
    if (w > 0.5) svgEl('path', { d: barPath(labelW + 1, cy - barH / 2, w, barH, 'right'), style: `fill:${spec.color}` }, root);
    const label = svgEl('text', { class: 'value-label', x: labelW + 1 + Math.max(w, 0) + 8, y: cy, 'dominant-baseline': 'middle', text: (spec.format || String)(it.value) }, root);
    if (spec.flagBelow && it.value < spec.flagBelow) {
      svgEl('tspan', { class: 'muted-label', dx: 6, text: spec.flagText || '要注意' }, label);
    }
  });
  return root;
}

// ---- one night: hypnogram + heart rate -----------------------------------------

/**
 * spec: { start (ms), epochSeconds, phases: "4422...", stages: {code: {name, color}}, order: [codes top->bottom],
 *         hr: {start (ms), interval (s), items: [..]} | null, timeLabel(ms) }
 */
export function sleepNightChart(container, spec) {
  const phases = spec.phases || '';
  if (!phases.length) return emptyState(container, spec.emptyMessage);
  const width = Math.max(container.clientWidth, 260);
  const hrItems = spec.hr?.items || [];
  const hrValid = hrItems.filter(finite);
  const hasHr = hrValid.length > 1;
  const m = { top: 4, right: 12, left: 44, bottom: 26 };
  const rowH = 22;
  const hypnoH = spec.order.length * rowH;
  const hrGap = 22;
  const hrH = hasHr ? 120 : 0;
  const height = m.top + hypnoH + (hasHr ? hrGap + hrH : 0) + m.bottom;
  const plotW = width - m.left - m.right;
  const epochMs = spec.epochSeconds * 1000;
  const t0 = spec.start;
  const t1 = t0 + phases.length * epochMs;
  const x = (t) => m.left + ((t - t0) / (t1 - t0)) * plotW;
  const rowOf = (code) => spec.order.indexOf(code);

  const root = createSvg(container, width, height, spec.ariaLabel);
  spec.order.forEach((code, r) => {
    const cy = m.top + r * rowH + rowH / 2;
    svgEl('line', { class: 'stage-guide', x1: m.left, x2: width - m.right, y1: Math.round(cy) + 0.5, y2: Math.round(cy) + 0.5 }, root);
    svgEl('text', { class: 'row-label', x: m.left - 8, y: cy, 'text-anchor': 'end', 'dominant-baseline': 'middle', text: spec.stages[code].name }, root);
  });
  // Runs of the same stage become one block on that stage's row.
  let runStart = 0;
  for (let i = 1; i <= phases.length; i++) {
    if (i < phases.length && phases[i] === phases[runStart]) continue;
    const code = phases[runStart];
    const r = rowOf(code);
    if (r >= 0) {
      const xa = x(t0 + runStart * epochMs);
      const xb = x(t0 + i * epochMs);
      svgEl('rect', {
        x: xa, y: m.top + r * rowH + 4, width: Math.max(xb - xa, 1), height: rowH - 8, rx: Math.min(2, (xb - xa) / 2),
        style: `fill:${spec.stages[code].color}`,
      }, root);
    }
    runStart = i;
  }

  let hrScale = null;
  let yHr = null;
  const hrTop = m.top + hypnoH + hrGap;
  if (hasHr) {
    hrScale = niceScale(Math.min(...hrValid) - 2, Math.max(...hrValid) + 2, 4);
    yHr = (v) => hrTop + hrH - ((v - hrScale.min) / (hrScale.max - hrScale.min)) * hrH;
    drawYAxis(root, hrScale, yHr, m.left, width - m.right, (v) => String(v));
    svgEl('text', { class: 'muted-label', x: m.left, y: hrTop - 8, text: '心拍数 (bpm)' }, root);
    const hrMs = spec.hr.interval * 1000;
    let d = '';
    let pen = false;
    hrItems.forEach((v, k) => {
      if (!finite(v)) { pen = false; return; }
      const tt = spec.hr.start + k * hrMs;
      if (tt < t0 || tt > t1) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${x(tt).toFixed(1)},${yHr(v).toFixed(1)}`;
      pen = true;
    });
    svgEl('path', { class: 'series', d, style: `stroke:${spec.hrColor}` }, root);
  }

  // Hour ticks along the bottom.
  const hourMs = 3600 * 1000;
  const hours = (t1 - t0) / hourMs;
  const every = plotW / hours < 44 ? 2 : 1;
  const first = new Date(t0);
  first.setMinutes(0, 0, 0);
  let tick = first.getTime() + hourMs;
  const bottom = height - m.bottom;
  svgEl('line', { class: 'axis-line', x1: m.left, x2: width - m.right, y1: bottom + 0.5, y2: bottom + 0.5 }, root);
  while (tick < t1) {
    if (new Date(tick).getHours() % every === 0) {
      svgEl('text', { class: 'tick-x', x: x(tick), y: height - 6, 'text-anchor': 'middle', text: spec.timeLabel(tick) }, root);
    }
    tick += hourMs;
  }

  const hover = svgEl('g', { 'pointer-events': 'none' }, root);
  const n = phases.length;
  const xAt = (i) => x(t0 + (i + 0.5) * epochMs);
  bindIndex(root, {
    n,
    xAt,
    indexAt: (lx) => Math.floor(((lx - m.left) / plotW) * n),
    anchorY: m.top + 10,
    onShow(i, anchor) {
      hover.replaceChildren();
      const xx = Math.round(xAt(i)) + 0.5;
      svgEl('line', { class: 'crosshair', x1: xx, x2: xx, y1: m.top, y2: bottom }, hover);
      const stage = spec.stages[phases[i]];
      const rows = [{ color: stage?.color, value: stage?.name || '—', label: '' }];
      const t = t0 + i * epochMs;
      if (hasHr) {
        const k = Math.round((t + epochMs / 2 - spec.hr.start) / (spec.hr.interval * 1000));
        const v = hrItems[k];
        if (finite(v)) {
          svgEl('circle', { class: 'dot', cx: xAt(i), cy: yHr(v), r: 4.5, style: `fill:${spec.hrColor}` }, hover);
          rows.push({ color: spec.hrColor, value: `${Math.round(v)} bpm`, label: '心拍数' });
        }
      }
      showTooltip(anchor, `${spec.timeLabel(t)} 〜 ${spec.timeLabel(t + epochMs)}`, rows);
    },
    onClear() { hover.replaceChildren(); },
  });
  return root;
}

// ---- time series (e.g. last 24 h heart rate) -----------------------------------

/** spec: { points: [{t, v, note}], color, format, gapMs, height, timeLabel(ms), title(point) } */
export function timeLineChart(container, spec) {
  const pts = spec.points.filter((p) => finite(p.v)).sort((a, b) => a.t - b.t);
  if (pts.length < 2) return emptyState(container, spec.emptyMessage);
  const width = Math.max(container.clientWidth, 260);
  const height = spec.height || 200;
  const m = { top: 12, right: 14, bottom: 26, left: 40 };
  const plotW = width - m.left - m.right;
  const plotH = height - m.top - m.bottom;
  const t0 = spec.domain ? spec.domain[0] : pts[0].t;
  const t1 = spec.domain ? spec.domain[1] : pts[pts.length - 1].t;
  const vals = pts.map((p) => p.v);
  const scale = niceScale(Math.min(...vals), Math.max(...vals), 5);
  const x = (t) => m.left + ((t - t0) / (t1 - t0)) * plotW;
  const y = (v) => m.top + plotH - ((v - scale.min) / (scale.max - scale.min)) * plotH;

  const root = createSvg(container, width, height, spec.ariaLabel);
  drawYAxis(root, scale, y, m.left, width - m.right, String);
  const hourMs = 3600 * 1000;
  const every = plotW / ((t1 - t0) / hourMs) < 14 ? 6 : 3;
  const first = new Date(t0);
  first.setMinutes(0, 0, 0);
  for (let t = first.getTime() + hourMs; t < t1; t += hourMs) {
    if (new Date(t).getHours() % every === 0) {
      svgEl('text', { class: 'tick-x', x: x(t), y: height - 6, 'text-anchor': 'middle', text: spec.timeLabel(t) }, root);
    }
  }
  let d = '';
  pts.forEach((p, k) => {
    const pen = k > 0 && p.t - pts[k - 1].t <= spec.gapMs;
    d += `${pen ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`;
  });
  svgEl('path', { class: 'series', d, style: `stroke:${spec.color}` }, root);
  const last = pts[pts.length - 1];
  svgEl('circle', { class: 'dot', cx: x(last.t), cy: y(last.v), r: 4, style: `fill:${spec.color}` }, root);

  const hover = svgEl('g', { 'pointer-events': 'none' }, root);
  const nearest = (lx) => {
    const t = t0 + ((lx - m.left) / plotW) * (t1 - t0);
    let lo = 0;
    let hi = pts.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (pts[mid].t < t) lo = mid; else hi = mid;
    }
    return Math.abs(pts[lo].t - t) <= Math.abs(pts[hi].t - t) ? lo : hi;
  };
  bindIndex(root, {
    n: pts.length,
    xAt: (i) => x(pts[i].t),
    indexAt: nearest,
    anchorY: m.top + 10,
    onShow(i, anchor) {
      hover.replaceChildren();
      const p = pts[i];
      const xx = Math.round(x(p.t)) + 0.5;
      svgEl('line', { class: 'crosshair', x1: xx, x2: xx, y1: m.top, y2: m.top + plotH }, hover);
      svgEl('circle', { class: 'dot', cx: x(p.t), cy: y(p.v), r: 4.5, style: `fill:${spec.color}` }, hover);
      showTooltip(anchor, spec.timeLabel(p.t, true), [{ color: spec.color, value: spec.format(p.v), label: p.note || '' }]);
    },
    onClear() { hover.replaceChildren(); },
  });
  return root;
}

// ---- sparkline for stat tiles ---------------------------------------------------

export function sparkline(container, values, { color, height = 36 } = {}) {
  container.replaceChildren();
  const pts = values.map((v, i) => [i, v]).filter(([, v]) => finite(v));
  if (pts.length < 2) return null;
  const width = Math.max(container.clientWidth, 80);
  const vs = pts.map(([, v]) => v);
  const lo = Math.min(...vs);
  const hi = Math.max(...vs);
  const span = hi - lo || 1;
  const n = values.length;
  const x = (i) => 5 + (i / Math.max(n - 1, 1)) * (width - 10);
  const y = (v) => 5 + (1 - (v - lo) / span) * (height - 10);
  const root = svgEl('svg', { width, height, viewBox: `0 0 ${width} ${height}`, 'aria-hidden': 'true' }, container);
  const d = pts.map(([i, v], k) => `${k ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
  svgEl('path', { d, fill: 'none', style: 'stroke:var(--c-deemph);stroke-width:2;stroke-linejoin:round;stroke-linecap:round' }, root);
  const [li, lv] = pts[pts.length - 1];
  svgEl('circle', { cx: x(li), cy: y(lv), r: 4, style: `fill:${color};stroke:var(--surface);stroke-width:2` }, root);
  return root;
}

// ---- table view -------------------------------------------------------------------

/** columns: [{label, num}], rows: [[cell, ...]] (cells are strings) */
export function renderTable(container, { columns, rows }) {
  container.replaceChildren();
  if (!rows.length) return emptyState(container);
  const wrap = document.createElement('div');
  wrap.className = 'table-wrap';
  const table = document.createElement('table');
  table.className = 'viz-table';
  const thead = table.createTHead().insertRow();
  for (const c of columns) {
    const th = document.createElement('th');
    th.textContent = c.label;
    if (c.num) th.className = 'num';
    thead.appendChild(th);
  }
  const tbody = table.createTBody();
  for (const r of rows) {
    const tr = tbody.insertRow();
    r.forEach((cell, k) => {
      const td = tr.insertCell();
      td.textContent = cell ?? '—';
      if (columns[k].num) td.className = 'num';
    });
  }
  wrap.appendChild(table);
  container.appendChild(wrap);
  return table;
}
