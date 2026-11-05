import {
  columnChart, emptyState, hBarChart, lineChart, renderTable, sleepNightChart, sparkline, timeLineChart,
} from './charts.js';

// ---- constants ------------------------------------------------------------------

const RANGES = [
  { days: 7, label: '7日' },
  { days: 30, label: '30日' },
  { days: 90, label: '90日' },
  { days: 365, label: '1年' },
];
const COLOR = {
  readiness: 'var(--c-readiness)',
  sleep: 'var(--c-sleep)',
  activity: 'var(--c-activity)',
  main: 'var(--c-main)',
  deep: 'var(--c-deep)',
  light: 'var(--c-light)',
  rem: 'var(--c-rem)',
  awake: 'var(--c-awake)',
  stress: 'var(--c-stress)',
  recovery: 'var(--c-recovery)',
};
const STAGES = {
  4: { name: '覚醒', color: COLOR.awake },
  3: { name: 'レム', color: COLOR.rem },
  2: { name: '浅い', color: COLOR.light },
  1: { name: '深い', color: COLOR.deep },
};
const READINESS_CONTRIBUTORS = {
  previous_night: '前夜の睡眠',
  sleep_balance: '睡眠バランス',
  sleep_regularity: '睡眠の規則性',
  previous_day_activity: '前日の活動',
  activity_balance: '活動バランス',
  body_temperature: '体温',
  resting_heart_rate: '安静時心拍数',
  hrv_balance: 'HRVバランス',
  recovery_index: '回復指数',
};
const SLEEP_CONTRIBUTORS = {
  total_sleep: '合計睡眠時間',
  efficiency: '睡眠効率',
  restfulness: '安らかさ',
  rem_sleep: 'レム睡眠',
  deep_sleep: '深い睡眠',
  latency: '入眠までの時間',
  timing: 'タイミング',
};
const WORKOUTS = {
  walking: 'ウォーキング', running: 'ランニング', cycling: 'サイクリング', strengthTraining: '筋トレ',
  yoga: 'ヨガ', swimming: '水泳', hiking: 'ハイキング', elliptical: 'エリプティカル', rowing: 'ローイング',
  dancing: 'ダンス', tennis: 'テニス', golf: 'ゴルフ', pilates: 'ピラティス', other: 'その他',
};
// Labels can come back as the string "None" (seen in Oura's sandbox).
const realLabel = (v) => (typeof v === 'string' && v.trim() && v.trim() !== 'None' ? v.trim() : null);
const workoutName = (w) => {
  const kind = WORKOUTS[w.activity] || realLabel(w.activity) || 'ワークアウト';
  const label = realLabel(w.label);
  return label && label !== kind ? `${kind} (${label})` : kind;
};
const INTENSITY = { easy: '軽め', moderate: '普通', hard: 'きつめ' };
const STRESS_SUMMARY = { restored: '回復できた', normal: '普通', stressful: 'ストレス多め' };
const RESILIENCE = { limited: '限定的', adequate: 'まずまず', solid: '安定', strong: '強い', exceptional: '非常に強い' };
const HR_SOURCE = { awake: '起きている間', rest: '休息', sleep: '睡眠中', workout: 'ワークアウト', live: '測定', session: 'セッション' };
// Which API collection each card reads, for error messages.
const CARD_SOURCES = {
  scores: ['daily_readiness', 'daily_sleep', 'daily_activity'],
  night: ['sleep'], stages: ['sleep'], readiness: ['daily_readiness'], sleepParts: ['daily_sleep'],
  hrv: ['sleep'], rhr: ['sleep'], temp: ['daily_readiness'], steps: ['daily_activity'],
  stress: ['daily_stress'], hr24: ['heartrate'], workouts: ['workout'],
};
const COLLECTION_NAMES = {
  daily_readiness: 'コンディション', daily_sleep: '睡眠スコア', daily_activity: 'アクティビティ', sleep: '睡眠の詳細',
  daily_stress: 'ストレス', daily_spo2: '血中酸素', daily_resilience: 'レジリエンス',
  daily_cardiovascular_age: '血管年齢', vO2_max: 'VO2max', workout: 'ワークアウト', heartrate: '心拍数', personal_info: 'プロフィール',
};

// ---- small helpers ----------------------------------------------------------------

const $ = (sel) => document.querySelector(sel);
const pad2 = (n) => String(n).padStart(2, '0');
const WEEKDAY = ['日', '月', '火', '水', '木', '金', '土'];
const isoDay = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
const parseDay = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
const shortDay = (s) => { const d = parseDay(s); return `${d.getMonth() + 1}/${d.getDate()}`; };
const longDay = (s) => { const d = parseDay(s); return `${d.getMonth() + 1}月${d.getDate()}日(${WEEKDAY[d.getDay()]})`; };
const clock = (ms) => { const d = new Date(ms); return `${d.getHours()}:${pad2(d.getMinutes())}`; };
const finite = (v) => typeof v === 'number' && Number.isFinite(v);
const fmtInt = (v) => (finite(v) ? Math.round(v).toLocaleString('ja-JP') : '—');
const fmtHM = (sec) => {
  if (!finite(sec)) return '—';
  const min = Math.round(sec / 60);
  const h = Math.floor(min / 60);
  return h ? `${h}時間${min % 60}分` : `${min}分`;
};
const fmtTemp = (v) => {
  if (!finite(v)) return '—';
  const r = Math.round(v * 10) / 10;
  return `${r > 0 ? '+' : r < 0 ? '−' : '±'}${Math.abs(r).toFixed(1)}℃`;
};
const mean = (xs) => { const v = xs.filter(finite); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

function readJson(text, fallback) {
  try { const v = JSON.parse(text); return Array.isArray(v) ? v : fallback; } catch { return fallback; }
}

function dayList(start, end) {
  const out = [];
  for (let d = parseDay(start); isoDay(d) <= end; d.setDate(d.getDate() + 1)) out.push(isoDay(d));
  return out;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) node.append(c);
  return node;
}

// ---- state ------------------------------------------------------------------------

const state = {
  status: null,
  days: Number(store.get('oura.days')) || 30,
  payload: null,
  view: null,
  tableCards: new Set(readJson(store.get('oura.tables'), [])),
  cards: [],
};
if (!RANGES.some((r) => r.days === state.days)) state.days = 30;

// ---- data shaping -----------------------------------------------------------------

// The app-aligned hypnogram first; real data sometimes fills only one of the two.
const phasesOf = (s) => s.app_sleep_phase_5_min || s.sleep_phase_5_min || null;

function mainSleepByDay(sleeps) {
  const best = new Map();
  for (const s of sleeps || []) {
    if (s.type === 'deleted' || !s.day) continue;
    const rank = [s.type === 'long_sleep' ? 1 : 0, s.total_sleep_duration || 0];
    const cur = best.get(s.day);
    if (!cur || rank[0] > cur.rank[0] || (rank[0] === cur.rank[0] && rank[1] > cur.rank[1])) best.set(s.day, { rank, s });
  }
  return new Map([...best].map(([day, v]) => [day, v.s]));
}

function prepare(payload) {
  const d = payload.data || {};
  const byDay = (arr) => new Map((arr || []).filter((r) => r && r.day).map((r) => [r.day, r]));
  const days = dayList(payload.range.start, payload.range.end);
  return {
    days,
    readiness: byDay(d.daily_readiness),
    sleepDaily: byDay(d.daily_sleep),
    activity: byDay(d.daily_activity),
    stress: byDay(d.daily_stress),
    spo2: byDay(d.daily_spo2),
    resilience: byDay(d.daily_resilience),
    cva: byDay(d.daily_cardiovascular_age),
    vo2: byDay(d.vO2_max),
    mainSleep: mainSleepByDay(d.sleep),
    workouts: (d.workout || []).filter((w) => w.day >= payload.range.start && w.day <= payload.range.end),
    heartrate: payload.heartrate || [],
    personal: payload.personal_info || null,
    errors: payload.errors || {},
  };
}

const series = (v, map, get) => v.days.map((day) => { const r = map.get(day); const x = r ? get(r) : null; return finite(x) ? x : null; });

function latestOf(days, values) {
  for (let i = days.length - 1; i >= 0; i--) if (finite(values[i])) return { i, day: days[i], value: values[i] };
  return null;
}

function deltaVsMean(values, last) {
  if (!last) return null;
  const others = mean(values.filter((_, i) => i !== last.i));
  return finite(others) ? last.value - others : null;
}

// ---- header / status --------------------------------------------------------------

function renderStatus() {
  const box = $('#status');
  box.replaceChildren();
  const s = state.status;
  if (!s) return;
  if (s.mode === 'demo' || s.mode === 'sandbox') {
    box.append(el('span', { class: 'chip chip--demo' }, el('span', { class: 'chip-dot' }), s.mode === 'demo' ? 'デモデータ' : 'Oura Sandbox'));
  } else if (s.authenticated) {
    box.append(
      el('span', { class: 'chip' }, el('span', { class: 'chip-dot' }), 'Oura に接続中'),
      el('button', { class: 'btn btn--quiet', type: 'button', onclick: logout }, 'ログアウト'),
    );
  }
}

async function logout() {
  if (!confirm('Oura からログアウトしますか? 保存されたトークンを削除します。')) return;
  await fetch('/logout', { method: 'POST' });
  location.reload();
}

function renderSetup(kind, message) {
  $('#filters').hidden = true;
  $('#main').hidden = true;
  const box = $('#setup');
  box.hidden = false;
  box.replaceChildren();
  const redirect = state.status?.redirect_uri || `${location.origin}/callback`;
  if (kind === 'login') {
    box.append(
      el('h2', { text: 'Oura アカウントに接続' }),
      el('p', { text: message || 'ダッシュボードに表示するデータを読むため、Oura の画面でアクセスを許可してください。' }),
      el('p', {}, el('a', { class: 'btn btn--primary', href: '/login', style: 'display:inline-block;text-decoration:none' }, 'Oura でログイン')),
      el('p', { class: 'card-sub', text: `うまくいかないときは、Oura に登録したアプリの Redirect URI が ${redirect} と完全に一致しているか確認してください。` }),
    );
    return;
  }
  const pre = (text) => el('pre', {}, el('code', { text }));
  box.append(
    el('h2', { text: 'はじめに: Oura API の設定' }),
    el('p', { text: state.status?.config_problem || 'Oura API の設定が見つかりません。' }),
    el('ol', {},
      el('li', {}, el('a', { href: 'https://cloud.ouraring.com/oauth/applications', target: '_blank', rel: 'noopener' }, 'Oura の API Applications'), ' で新しいアプリを登録します。'),
      el('li', {}, 'Redirect URI に ', el('code', { text: redirect }), ' を入力します。'),
      el('li', {}, '表示された Client ID と Client Secret を、このフォルダの ', el('code', { text: '.env' }), ' に書きます (', el('code', { text: '.env.example' }), ' をコピー)。', pre('OURA_CLIENT_ID=xxxxxxxx\nOURA_CLIENT_SECRET=xxxxxxxx')),
      el('li', {}, 'サーバーを再起動し、このページを開き直します。', pre('python3 server.py')),
    ),
    el('p', {}, '先に見た目を確かめたいときは ', el('code', { text: 'python3 server.py --demo' }), ' でサンプルデータを表示できます。'),
  );
}

function renderNotice(view, payload) {
  const box = $('#notice');
  box.replaceChildren();
  const items = [];
  if (payload.source === 'demo') {
    items.push(el('p', { style: 'margin:0' }, el('strong', { text: 'サンプルデータを表示しています。' }), ' 自分のデータを見るには README.md の手順で Oura API を設定し、--demo を付けずに起動してください。'));
  } else if (payload.source === 'sandbox') {
    items.push(el('p', { style: 'margin:0' }, el('strong', { text: 'Oura API の Sandbox (テスト用データ) を表示しています。' }), ' 値は Oura が用意した固定のもので、実際の睡眠や活動とは関係ありません。自分のデータを見るには --sandbox を付けずに起動してください。'));
  }
  const errs = Object.entries(view.errors);
  if (errs.length) {
    items.push(el('p', { style: 'margin:0' }, el('strong', { text: '一部のデータを取得できませんでした。' }), ' 権限 (スコープ) がない、またはリングが対応していない可能性があります。'));
    items.push(el('ul', {}, errs.map(([k, msg]) => el('li', { text: `${COLLECTION_NAMES[k] || k}: ${msg}` }))));
    if (errs.some(([, msg]) => /scope/i.test(msg))) {
      items.push(el('p', { style: 'margin:8px 0 0' }, '「scope」を含むエラーは、そのデータを読む権限が今のログインにないという意味です。右上の「ログアウト」からログインし直し、Oura の許可画面で共有をオンにしてください。それでも出るときは、Oura のアプリ登録画面でそのスコープにチェックが入っているか確認してください。'));
    }
  }
  box.hidden = !items.length;
  box.append(...items);
}

// ---- filters ----------------------------------------------------------------------

function renderFilters() {
  const seg = $('#ranges');
  seg.replaceChildren(...RANGES.map((r) => el('button', {
    type: 'button', role: 'radio', 'aria-checked': String(r.days === state.days),
    onclick: () => {
      if (state.days === r.days) return;
      state.days = r.days;
      store.set('oura.days', String(r.days));
      renderFilters();
      load();
    },
  }, r.label)));
}

function currentRange() {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - (state.days - 1));
  return { start: isoDay(start), end: isoDay(end) };
}

// ---- tiles ------------------------------------------------------------------------

function scoreStatus(score) {
  if (score >= 85) return { cls: 'good', icon: '✓', text: '最適' };
  if (score >= 70) return { cls: 'ok', icon: '', text: '良好' };
  return { cls: 'warn', icon: '!', text: '要注意' };
}

function deltaNode(delta, { upGood = true, format = (d) => fmtInt(Math.abs(d)), unit = '' } = {}) {
  if (!finite(delta)) return null;
  const rounded = Number(delta.toFixed(1));
  const sign = rounded > 0 ? '+' : rounded < 0 ? '−' : '±';
  const cls = upGood === null || rounded === 0 ? '' : (rounded > 0) === upGood ? 'good' : 'bad';
  const arrow = rounded > 0 ? '▲' : rounded < 0 ? '▼' : '';
  return el('div', { class: 'tile-delta' }, el('span', { class: `d ${cls}` }, `${arrow}${sign}${format(delta)}${unit}`), ' 期間平均比');
}

function scoreTile(view, { name, color, map }) {
  const values = series(view, map, (r) => r.score);
  const last = latestOf(view.days, values);
  const tile = el('div', { class: 'tile tile--score' });
  tile.append(el('div', { class: 'tile-head' }, el('span', { class: 'tile-label', text: name }), el('span', { class: 'tile-day', text: last ? longDay(last.day) : '' })));
  if (!last) {
    tile.append(el('div', { class: 'tile-value', text: '—' }), el('div', { class: 'tile-sub', text: 'この期間のデータがありません' }));
    return tile;
  }
  const st = scoreStatus(last.value);
  tile.append(
    el('div', { class: 'tile-value' }, String(last.value),
      el('span', { class: `status status--${st.cls}` }, el('span', { class: 'status-icon', 'aria-hidden': 'true', text: st.icon }), st.text)),
    deltaNode(deltaVsMean(values, last)) || el('div', { class: 'tile-delta', text: ' ' }),
  );
  const spark = el('div', { class: 'spark' });
  tile.append(spark);
  state.cards.push({ render: () => sparkline(spark, values, { color }) });
  return tile;
}

function metricTile({ label, value, unit, day, delta, sub }) {
  return el('div', { class: 'tile' },
    el('div', { class: 'tile-head' }, el('span', { class: 'tile-label', text: label }), el('span', { class: 'tile-day', text: day ? shortDay(day) : '' })),
    el('div', { class: 'tile-value' }, value, unit ? el('small', { text: unit }) : null),
    delta || null,
    sub ? el('div', { class: 'tile-sub', text: sub }) : null,
  );
}

function renderTiles(view) {
  const scores = $('#scores');
  scores.replaceChildren(
    scoreTile(view, { name: 'コンディション', color: COLOR.readiness, map: view.readiness }),
    scoreTile(view, { name: '睡眠', color: COLOR.sleep, map: view.sleepDaily }),
    scoreTile(view, { name: 'アクティビティ', color: COLOR.activity, map: view.activity }),
  );

  const tiles = [];
  const add = (values, opts) => {
    const last = latestOf(view.days, values);
    if (!last) return;
    tiles.push(metricTile({ ...opts.tile(last), day: last.day, delta: opts.delta ? deltaNode(deltaVsMean(values, last), opts.delta) : null }));
  };
  add(series(view, view.mainSleep, (s) => s.total_sleep_duration), {
    tile: (l) => ({ label: '睡眠時間', value: fmtHM(l.value) }),
    delta: { upGood: true, format: (d) => fmtHM(Math.abs(d)) },
  });
  add(series(view, view.mainSleep, (s) => s.lowest_heart_rate), {
    tile: (l) => ({ label: '安静時心拍', value: fmtInt(l.value), unit: 'bpm' }),
    delta: { upGood: false },
  });
  add(series(view, view.mainSleep, (s) => s.average_hrv), {
    tile: (l) => ({ label: 'HRV', value: fmtInt(l.value), unit: 'ms' }),
    delta: { upGood: true },
  });
  add(series(view, view.readiness, (r) => r.temperature_deviation), {
    tile: (l) => ({ label: '体温の偏差', value: fmtTemp(l.value), sub: '平常時との差' }),
  });
  add(series(view, view.activity, (r) => r.steps), {
    tile: (l) => ({ label: '歩数', value: fmtInt(l.value), unit: '歩' }),
    delta: { upGood: true },
  });
  add(series(view, view.stress, (r) => r.stress_high), {
    tile: (l) => {
      const r = view.stress.get(l.day);
      return { label: 'ストレス (高)', value: fmtHM(l.value), sub: STRESS_SUMMARY[r?.day_summary] || '' };
    },
  });
  add(series(view, view.spo2, (r) => r.spo2_percentage?.average), {
    tile: (l) => ({ label: '血中酸素 (SpO2)', value: l.value.toFixed(1), unit: '%', sub: '睡眠中の平均' }),
  });
  add(series(view, view.vo2, (r) => r.vo2_max), {
    tile: (l) => ({ label: 'VO2max', value: fmtInt(l.value), unit: 'ml/kg/分' }),
  });
  add(series(view, view.cva, (r) => r.vascular_age), {
    tile: (l) => ({ label: '血管年齢', value: fmtInt(l.value), unit: '歳', sub: finite(view.personal?.age) ? `実年齢 ${view.personal.age}歳` : '' }),
  });
  const levels = Object.keys(RESILIENCE);
  add(series(view, view.resilience, (r) => (r.level ? levels.indexOf(r.level) : null)), {
    tile: (l) => ({ label: 'レジリエンス', value: RESILIENCE[levels[l.value]] || '—' }),
  });
  $('#metrics').replaceChildren(...tiles);
}

// ---- cards ------------------------------------------------------------------------

function card(view, { id, title, sub, wide, legend, chart, table, isEmpty }) {
  const body = el('div', { class: 'card-body' });
  const errs = (CARD_SOURCES[id] || []).map((k) => view.errors[k]).filter(Boolean);
  const showTable = () => state.tableCards.has(id);
  const toggle = table ? el('button', {
    class: 'btn btn--quiet', type: 'button', 'aria-pressed': String(showTable()), title: '表で表示',
    onclick: () => {
      if (showTable()) state.tableCards.delete(id); else state.tableCards.add(id);
      store.set('oura.tables', JSON.stringify([...state.tableCards]));
      toggle.setAttribute('aria-pressed', String(showTable()));
      draw();
    },
  }, '表') : null;
  const node = el('section', { class: `card${wide ? ' card--wide' : ''}`, id: `card-${id}` },
    el('div', { class: 'card-head' },
      el('div', {}, el('h2', { text: title }), sub ? el('p', { class: 'card-sub', text: sub }) : null),
      el('div', { class: 'card-tools' }, toggle)),
    legend && legend.length > 1 ? el('ul', { class: 'legend' }, legend.map((l) => el('li', {},
      el('span', { class: l.kind === 'rect' ? 'key-rect' : 'key-line', style: `background:${l.color}` }), l.name))) : null,
    body);
  function draw() {
    if (isEmpty && isEmpty()) return emptyState(body, errs.length ? `取得できませんでした (${errs[0]})` : undefined);
    if (showTable() && table) return renderTable(body, table());
    return chart(body);
  }
  state.cards.push({ render: draw });
  return node;
}

function renderCards(view) {
  const { days } = view;
  const xLabel = (i) => shortDay(days[i]);
  const title = (i) => longDay(days[i]);
  const cards = [];
  const anyIn = (...arrs) => arrs.some((a) => a.some(finite));

  // Scores
  const sR = series(view, view.readiness, (r) => r.score);
  const sS = series(view, view.sleepDaily, (r) => r.score);
  const sA = series(view, view.activity, (r) => r.score);
  const scoreSeries = [
    { name: 'コンディション', color: COLOR.readiness, values: sR },
    { name: '睡眠', color: COLOR.sleep, values: sS },
    { name: 'アクティビティ', color: COLOR.activity, values: sA },
  ];
  cards.push(card(view, {
    id: 'scores', title: 'スコアの推移', sub: '0〜100。85以上が最適', wide: true,
    legend: scoreSeries.map((s) => ({ ...s, kind: 'line' })),
    isEmpty: () => !anyIn(sR, sS, sA),
    chart: (c) => lineChart(c, { labels: days, xLabel, title, series: scoreSeries, height: 240, yPad: 3, yClamp: [0, 100], ariaLabel: 'スコアの推移' }),
    table: () => ({
      columns: [{ label: '日付' }, { label: 'コンディション', num: true }, { label: '睡眠', num: true }, { label: 'アクティビティ', num: true }],
      rows: days.map((d, i) => [longDay(d), fmtInt(sR[i]), fmtInt(sS[i]), fmtInt(sA[i])]).reverse(),
    }),
  }));

  // Last night
  const nights = [...view.mainSleep.values()].filter(phasesOf).sort((a, b) => (a.bedtime_end < b.bedtime_end ? -1 : 1));
  const night = nights[nights.length - 1];
  const phases = night ? phasesOf(night) : '';
  const nightStart = night ? Date.parse(night.bedtime_start) : 0;
  const hr = night?.heart_rate?.items ? { start: Date.parse(night.heart_rate.timestamp), interval: night.heart_rate.interval, items: night.heart_rate.items } : null;
  cards.push(card(view, {
    id: 'night', wide: true,
    title: night ? `最新の睡眠: ${longDay(night.day)}` : '最新の睡眠',
    sub: night ? `${clock(nightStart)} 〜 ${clock(Date.parse(night.bedtime_end))} · 合計 ${fmtHM(night.total_sleep_duration)} · 効率 ${fmtInt(night.efficiency)}% · 最低心拍 ${fmtInt(night.lowest_heart_rate)} bpm` : '',
    legend: Object.values(STAGES).reverse().map((s) => ({ ...s, kind: 'rect' })),
    isEmpty: () => !night,
    chart: (c) => sleepNightChart(c, {
      start: nightStart, epochSeconds: 300, phases, stages: STAGES, order: ['4', '3', '2', '1'],
      hr, hrColor: COLOR.main, timeLabel: clock, ariaLabel: '睡眠ステージと心拍数',
    }),
    table: () => ({
      columns: [{ label: '時刻' }, { label: 'ステージ' }, { label: '心拍数 (bpm)', num: true }],
      rows: [...phases].map((code, i) => {
        const t = nightStart + i * 300000;
        const k = hr ? Math.round((t + 150000 - hr.start) / (hr.interval * 1000)) : -1;
        return [clock(t), STAGES[code]?.name || code, hr && finite(hr.items[k]) ? fmtInt(hr.items[k]) : '—'];
      }),
    }),
  }));

  // Sleep stages per night
  const stageKeys = [
    { key: 'deep_sleep_duration', ...STAGES[1] },
    { key: 'light_sleep_duration', ...STAGES[2] },
    { key: 'rem_sleep_duration', ...STAGES[3] },
    { key: 'awake_time', ...STAGES[4] },
  ];
  const stageSeries = stageKeys.map((s) => ({ name: s.name, color: s.color, values: series(view, view.mainSleep, (r) => (finite(r[s.key]) ? r[s.key] / 3600 : null)), format: (h) => fmtHM(h * 3600) }));
  const totalSleep = series(view, view.mainSleep, (r) => r.total_sleep_duration);
  cards.push(card(view, {
    id: 'stages', title: '睡眠ステージ', sub: '1晩ごとの時間 (時間)',
    legend: stageSeries.map((s) => ({ ...s, kind: 'rect' })),
    isEmpty: () => !anyIn(...stageSeries.map((s) => s.values)),
    chart: (c) => columnChart(c, {
      labels: days, xLabel, title, stacks: stageSeries, height: 240, yFormat: (v) => `${v}`,
      totalRow: (i) => (finite(totalSleep[i]) ? { value: fmtHM(totalSleep[i]), label: '合計睡眠' } : null), ariaLabel: '睡眠ステージ',
    }),
    table: () => ({
      columns: [{ label: '日付' }, ...stageSeries.map((s) => ({ label: s.name, num: true })), { label: '合計睡眠', num: true }],
      rows: days.map((d, i) => [longDay(d), ...stageSeries.map((s) => (finite(s.values[i]) ? fmtHM(s.values[i] * 3600) : '—')), fmtHM(totalSleep[i])]).reverse(),
    }),
  }));

  // Contributors
  const contributorCard = (id, cardTitle, map, labels) => {
    const values = series(view, map, (r) => (r.contributors ? 1 : null));
    const last = latestOf(days, values);
    const rec = last ? map.get(last.day) : null;
    const items = Object.entries(labels).map(([k, label]) => ({ label, value: rec?.contributors?.[k] }));
    return card(view, {
      id, title: cardTitle, sub: rec ? `${longDay(rec.day)} · 各項目 0〜100` : '',
      isEmpty: () => !rec,
      chart: (c) => hBarChart(c, { items, color: COLOR.main, flagBelow: 70, flagText: '要注意', ariaLabel: cardTitle }),
      table: () => ({ columns: [{ label: '項目' }, { label: 'スコア', num: true }], rows: items.map((it) => [it.label, fmtInt(it.value)]) }),
    });
  };
  cards.push(contributorCard('readiness', 'コンディションの内訳', view.readiness, READINESS_CONTRIBUTORS));

  // HRV / resting HR (two charts - never one chart with two y axes)
  const hrv = series(view, view.mainSleep, (s) => s.average_hrv);
  const rhr = series(view, view.mainSleep, (s) => s.lowest_heart_rate);
  const singleLine = (id, cardTitle, sub, values, unit, color = COLOR.main) => card(view, {
    id, title: cardTitle, sub,
    isEmpty: () => !anyIn(values),
    chart: (c) => lineChart(c, { labels: days, xLabel, title, series: [{ name: cardTitle, color, values, format: (v) => `${fmtInt(v)} ${unit}` }], yPad: 2, height: 200, yFormat: String, ariaLabel: cardTitle }),
    table: () => ({ columns: [{ label: '日付' }, { label: `${cardTitle} (${unit})`, num: true }], rows: days.map((d, i) => [longDay(d), fmtInt(values[i])]).reverse() }),
  });
  cards.push(singleLine('hrv', 'HRV', '睡眠中の平均 (ms)', hrv, 'ms'));
  cards.push(singleLine('rhr', '安静時心拍', '睡眠中の最低心拍 (bpm)', rhr, 'bpm'));

  cards.push(contributorCard('sleepParts', '睡眠スコアの内訳', view.sleepDaily, SLEEP_CONTRIBUTORS));

  const temp = series(view, view.readiness, (r) => r.temperature_deviation);
  cards.push(card(view, {
    id: 'temp', title: '体温の偏差', sub: '平常時との差 (℃)。0 が平常',
    isEmpty: () => !anyIn(temp),
    chart: (c) => lineChart(c, { labels: days, xLabel, title, series: [{ name: '体温の偏差', color: COLOR.main, values: temp, format: fmtTemp }], baseline: 0, yPad: 0.1, height: 200, yFormat: (v) => (v > 0 ? `+${v}` : `${v}`), ariaLabel: '体温の偏差' }),
    table: () => ({ columns: [{ label: '日付' }, { label: '偏差', num: true }], rows: days.map((d, i) => [longDay(d), fmtTemp(temp[i])]).reverse() }),
  }));

  const steps = series(view, view.activity, (r) => r.steps);
  cards.push(card(view, {
    id: 'steps', title: '歩数', sub: '1日の合計',
    isEmpty: () => !anyIn(steps),
    chart: (c) => columnChart(c, { labels: days, xLabel, title, stacks: [{ name: '歩数', color: COLOR.main, values: steps, format: fmtInt }], labelLast: true, height: 200, yFormat: (v, ticks) => (v && ticks && Math.max(...ticks) >= 10000 ? `${v / 1000}K` : fmtInt(v)), ariaLabel: '歩数' }),
    table: () => ({ columns: [{ label: '日付' }, { label: '歩数', num: true }], rows: days.map((d, i) => [longDay(d), fmtInt(steps[i])]).reverse() }),
  }));

  const stressHigh = series(view, view.stress, (r) => (finite(r.stress_high) ? r.stress_high / 60 : null));
  const recoveryHigh = series(view, view.stress, (r) => (finite(r.recovery_high) ? r.recovery_high / 60 : null));
  const fmtMin = (m) => fmtHM(m * 60);
  cards.push(card(view, {
    id: 'stress', title: 'ストレスと回復', sub: '日中の「ストレス高」と「回復」の時間 (分)',
    legend: [{ name: 'ストレス高', color: COLOR.stress, kind: 'rect' }, { name: '回復', color: COLOR.recovery, kind: 'rect' }],
    isEmpty: () => !anyIn(stressHigh, recoveryHigh),
    chart: (c) => columnChart(c, {
      labels: days, xLabel, title, height: 220, yFormat: (v) => fmtInt(v),
      stacks: [{ name: 'ストレス高', color: COLOR.stress, values: stressHigh, format: fmtMin }],
      down: { name: '回復', color: COLOR.recovery, values: recoveryHigh, format: fmtMin },
      totalRow: (i) => { const r = view.stress.get(days[i]); return r?.day_summary ? { value: STRESS_SUMMARY[r.day_summary] || r.day_summary, label: '' } : null; },
      ariaLabel: 'ストレスと回復',
    }),
    table: () => ({
      columns: [{ label: '日付' }, { label: 'ストレス高', num: true }, { label: '回復', num: true }, { label: 'まとめ' }],
      rows: days.map((d, i) => [longDay(d), fmtMin(stressHigh[i]), fmtMin(recoveryHigh[i]), STRESS_SUMMARY[view.stress.get(d)?.day_summary] || '—']).reverse(),
    }),
  }));

  const hrPoints = view.heartrate.map((p) => ({ t: Date.parse(p.timestamp), v: p.bpm, note: HR_SOURCE[p.source] || p.source || '' }));
  const now = Date.now();
  cards.push(card(view, {
    id: 'hr24', title: '直近24時間の心拍数', sub: 'bpm', wide: true,
    isEmpty: () => hrPoints.length < 2,
    chart: (c) => timeLineChart(c, {
      points: hrPoints, color: COLOR.main, format: (v) => `${fmtInt(v)} bpm`, gapMs: 20 * 60 * 1000, height: 200,
      domain: [now - 24 * 3600 * 1000, now],
      timeLabel: (ms, long) => { const d = new Date(ms); return long ? `${d.getMonth() + 1}/${d.getDate()} ${clock(ms)}` : clock(ms); },
      ariaLabel: '直近24時間の心拍数',
    }),
    table: () => ({
      columns: [{ label: '時刻' }, { label: '心拍数 (bpm)', num: true }, { label: '状態' }],
      rows: [...hrPoints].sort((a, b) => b.t - a.t).map((p) => { const d = new Date(p.t); return [`${d.getMonth() + 1}/${d.getDate()} ${clock(p.t)}`, fmtInt(p.v), p.note]; }),
    }),
  }));

  const workouts = [...view.workouts].sort((a, b) => (a.start_datetime < b.start_datetime ? 1 : -1));
  const workoutTable = () => ({
    columns: [{ label: '日付' }, { label: '種類' }, { label: '時間', num: true }, { label: '強度' }, { label: 'カロリー', num: true }, { label: '距離', num: true }],
    rows: workouts.map((w) => {
      const start = Date.parse(w.start_datetime);
      const sec = (Date.parse(w.end_datetime) - start) / 1000;
      return [
        `${longDay(w.day)} ${clock(start)}`,
        workoutName(w),
        fmtHM(sec),
        INTENSITY[w.intensity] || w.intensity || '—',
        finite(w.calories) ? `${fmtInt(w.calories)} kcal` : '—',
        finite(w.distance) && w.distance > 0 ? (w.distance < 1000 ? `${fmtInt(w.distance)} m` : `${(w.distance / 1000).toFixed(1)} km`) : '—',
      ];
    }),
  });
  cards.push(card(view, {
    id: 'workouts', title: 'ワークアウト', sub: workouts.length ? `${workouts.length} 件` : '', wide: true,
    isEmpty: () => !workouts.length,
    chart: (c) => renderTable(c, workoutTable()),
  }));

  $('#grid').replaceChildren(...cards);
}

// ---- render & load ----------------------------------------------------------------

function renderAll() {
  const view = state.view;
  state.cards = [];
  renderNotice(view, state.payload);
  renderTiles(view);
  renderCards(view);
  drawAll();
  const fetched = state.payload.fetched_at ? new Date(state.payload.fetched_at) : null;
  $('#updated').textContent = fetched ? `${clock(fetched.getTime())} に取得` : '';
}

function drawAll() {
  for (const c of state.cards) c.render();
}

let resizeTimer = 0;
let lastWidth = 0;
new ResizeObserver(() => {
  const w = $('#main').clientWidth;
  if (!state.view || w === lastWidth) return;
  lastWidth = w;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(drawAll, 80);
}).observe($('#main'));

async function load(refresh = false) {
  const main = $('#main');
  const btn = $('#refresh');
  main.classList.add('is-loading');
  btn.disabled = true;
  try {
    const { start, end } = currentRange();
    const res = await fetch(`/api/dashboard?start=${start}&end=${end}${refresh ? '&refresh=1' : ''}`);
    const body = await res.json().catch(() => ({}));
    if (res.status === 401) {
      renderSetup(state.status?.configured ? 'login' : 'config', body.message);
      return;
    }
    if (!res.ok) throw new Error(body.message || `HTTP ${res.status}`);
    state.payload = body;
    state.view = prepare(body);
    lastWidth = main.clientWidth;
    renderAll();
  } catch (e) {
    const box = $('#notice');
    box.hidden = false;
    box.replaceChildren(el('p', { style: 'margin:0' }, el('strong', { text: 'データを読み込めませんでした。' }), ` ${e.message}`));
  } finally {
    main.classList.remove('is-loading');
    btn.disabled = false;
  }
}

async function init() {
  try {
    state.status = await (await fetch('/api/status')).json();
  } catch {
    state.status = null;
  }
  renderStatus();
  if (state.status && state.status.mode === 'live' && !state.status.configured) return renderSetup('config');
  if (state.status && state.status.mode === 'live' && !state.status.authenticated) return renderSetup('login');
  renderFilters();
  $('#refresh').addEventListener('click', () => load(true));
  await load();
}

init();
