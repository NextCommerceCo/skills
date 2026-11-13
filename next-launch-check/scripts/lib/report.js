'use strict';
// Three outputs from one result:
//  - report.html (and report.pdf from it): for people — the result, what to
//    fix, what to check, the speed table and the full checklist, readable
//    and printable;
//  - report.md: for developers and AI agents — a fix list with item names,
//    URLs, evidence and likely fixes, the open checks, and how to re-run;
//  - results.json: everything, machine-readable.
const fs = require('fs');
const path = require('path');
const CL = require('./checklist');

function counted(rows) { return rows.filter((a) => !a.waived && !a.deferred); }

function automatedDisposition(rows) {
  const live = counted(rows);
  if (live.some((a) => a.status === 'fail' && a.severity === 'blocker')) return 'blocked';
  // Rows with severity info are advice; they do not turn a clean run into an exception.
  if (live.some((a) => a.status === 'fail' || ((a.status === 'warn' || a.status === 'manual_review') && a.severity !== 'info'))) return 'ready_with_exceptions';
  return 'ready';
}

function counts(rows) {
  const c = { pass: 0, fail: 0, warn: 0, manual_review: 0, skipped: 0, waived: 0, deferred: 0 };
  for (const a of rows) { if (a.waived) c.waived++; else if (a.deferred) c.deferred++; else c[a.status]++; }
  return c;
}

const RESULT = {
  ready: ['Ready', 'Every checklist item passed.'],
  ready_with_exceptions: ['Ready with exceptions', 'No blockers, but some items failed or need attention.'],
  blocked: ['Blocked', 'At least one blocker must be fixed before launch.'],
  incomplete: ['Incomplete', 'Automated checks are done. Manual items, and items to confirm from the evidence, still need a person.'],
};
const STATUS_WORD = { pass: 'Pass', fail: 'Fail', warn: 'Warning', na: 'N/A', open: 'To check', manual: 'Manual', check: 'Confirm', not_run: 'Not run' };
// What an open item is waiting for.
const KIND = (i) => (i.status !== 'open' ? i.status : i.auto === 'manual' ? 'manual' : i.auto === 'check' ? 'check' : 'not_run');
const HOW = (i) => (i.answer ? `Answered by tester${i.note ? `: ${i.note}` : ''}` : i.auto === 'manual' ? 'Manual: always checked by a person' : i.auto === 'not_run' ? 'Automated, did not run this time' : i.auto === 'check' ? 'Automated evidence: a person confirms' : 'Automated');

// Why an automated item has no result in this run.
function notRunReason(i, r) {
  const g = i.auto_globs || [];
  const orderPlaced = /placed by the script|recorded/.test((r.lanes && r.lanes.order) || '');
  if (g.length && g.every((x) => /^(order:|cos:browser-test-order|!)/.test(x))) {
    return orderPlaced ? `Not in this run's order plan. ${i.hint || ''}`.trim() : `Filled by a test order; none was placed in this run. ${i.hint || ''}`.trim();
  }
  if (g.some((x) => x.startsWith('order:'))) return orderPlaced ? (i.hint || 'Not covered by this run\'s orders.') : `Filled by a test order; none was placed in this run. ${i.hint || ''}`.trim();
  if (g.some((x) => x.startsWith('speed:'))) return 'The speed test did not run (--no-speed, or no engine answered).';
  if (g.some((x) => /^(form-negative|param-toggles|responsive|tags-observed|images|locale|currency|addons|browser-runtime)/.test(x))) return /ran/.test((r.lanes && r.lanes.browser) || '') ? 'The browser check could not run on this page; see results.json.' : 'Needs the browser checks (Playwright with Chromium), which did not run.';
  if (g.some((x) => x.startsWith('cos:'))) return 'Comes from Campaigns OS QA, which did not run.';
  return i.hint || 'The automated check did not run on this page.';
}

// Likely fix per family, for the developer report.
const FIX = {
  http: 'Fix the route or the deploy; check the route tag or link that points here.',
  'funnel-chain': 'Point the route tag (next-success-url, next-upsell-accept-url, next-upsell-decline-url) at the real next page.',
  sdk: 'Pin the Campaign Cart script to an exact version and use one config (one campaign API key) on every page.',
  'tracking-consistency': 'Add the missing tag snippet or align the ids so every page loads the same container and ids.',
  'tags-observed': 'Check the tag snippet loads (GTM in source but gtm.js never requested usually means a broken snippet or a blocker).',
  'legal-links': 'Add or fix the policy link; on checkout and upsell pages give it target="_blank".',
  links: 'Wire or remove the link; replace #, empty and placeholder hrefs.',
  'content-hygiene': 'Replace placeholder copy; give images alt text.',
  'seo-meta': 'Add the missing meta tags (title, description, og:image, viewport).',
  'media-weight': 'Compress or resize the file, or serve it from a CDN.',
  'storefront-domain': 'Link to your own domain, not the default *.29next.store address.',
  speed: 'Open the PageSpeed link for the full audit; usually image weight, render-blocking scripts or layout shift.',
  'browser-runtime': 'Open the page with ?debug=1 (SDK 0.5) or ?debugger=true (0.4) and fix the console errors.',
  responsive: 'Find the elements listed as sticking out; replace fixed widths with max-width: 100%.',
  'param-propagation': 'Carry the query string onto funnel links (or rely on the SDK and confirm with a test order).',
  'param-toggles': 'Wire the toggle in campaign JS: hide the element when the URL has <name>=n.',
  locale: 'Read amounts from the SDK instead of parsing formatted price text.',
  'form-negative': 'Use the SDK checkout fields so required fields and email format are validated before submit.',
  order: 'Open the order screenshot and the order request in order-record.json.',
  favicon: 'Add <link rel="icon" href="…"> pointing at a file that loads.',
  images: 'Fix or replace the image URLs listed.',
  contact: 'Use one support email and phone number on every page.',
  'pr-review': 'See the file and line listed.',
};

const FIX_BY_ID = [
  [/:pinch-zoom$/, 'Remove maximum-scale=1 and user-scalable=no from the viewport meta tag.'],
  [/:same-as-billing$/, 'Bind the checkbox to the billing form (SDK billing fields) so unticking it shows them.'],
  [/:autocomplete$/, 'Check the address autocomplete settings in the campaign config (and its Maps key).'],
  [/:invalid-(card|cvv)$/, 'Use the SDK hosted card fields; they validate the number and CVV before any order call.'],
  [/:error-language$/, "Translate the checkout's error messages, or set the SDK language for this market."],
  [/^tags-observed:.*:ga4$/, 'Add a Google Analytics tag to the GTM container (or the page), or answer n/a if GA4 is not used.'],
  [/^order:(language|shipping)/, 'Check the checkout config: language code and shipping method sent with the order.'],
  [/^order:upsell-events/, 'Fire one upsell event per accepted upsell (the SDK does this when analytics is on).'],
];
function fixFor(a) {
  const hit = FIX_BY_ID.find(([re]) => re.test(a.id));
  if (hit) return hit[1];
  if (a.family === 'speed') return a.status === 'fail' ? FIX.speed : null;
  return FIX[a.family.replace(/^cos-/, '')] || null;
}

// Evidence as short readable lines: objects become "key: value · key: value".
function flat(o) {
  if (o === null || o === undefined) return '';
  if (typeof o !== 'object') return String(o);
  if (Array.isArray(o)) return o.map(flat).join(', ');
  return Object.entries(o).filter(([k, v]) => v !== null && v !== undefined && v !== '' && !/^(path|run_id|base_url)$/.test(k)).map(([k, v]) => `${k.replace(/_/g, ' ')}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ');
}
function evidenceLines(e) {
  if (e === undefined || e === null) return [];
  if (typeof e === 'string') return [e];
  if (Array.isArray(e)) return e.map((x) => (typeof x === 'string' ? x : flat(x))).filter(Boolean);
  const f = flat(e);
  return f ? [f] : [];
}
function rowEvidence(a) {
  return a.evidence !== undefined ? a.evidence : a.status !== 'pass' && typeof a.actual === 'object' ? a.actual : undefined;
}
function numbered(items) {
  const bySec = new Map();
  return items.map((i) => { const n = (bySec.get(i.section) || 0) + 1; bySec.set(i.section, n); return { ...i, n }; });
}
// One line per test order, from the order rows.
function orderSummary(r) {
  return r.assertions.filter((a) => /^order:created:/.test(a.id)).map((a) => {
    const label = a.id.replace(/^order:created:/, '');
    const rel = (k) => r.assertions.find((x) => x.id === `order:${k}:${label}`);
    const up = rel('upsells');
    const rc = rel('receipt');
    const bits = [a.message.replace(/\s+Stopped:.*$/, '')];
    if (up) bits.push(up.status === 'pass' ? `upsells ok (${up.message})` : `upsells: ${up.message}`);
    if (rc) bits.push(rc.status === 'pass' ? 'receipt reached' : 'receipt NOT reached');
    const stopped = /Stopped: (.*)$/.exec(a.message);
    if (stopped) bits.push(`stopped early: ${stopped[1]}`);
    return { label, ok: a.status === 'pass', text: bits.join(' · '), shot: (a.screenshots || [])[0] || null };
  });
}
function speedRows(r) { return r.assertions.filter((a) => a.family === 'speed' && a.actual); }
const ms = (v) => (v === null || v === undefined ? '—' : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`);

// ---------- developer / AI markdown ----------
function markdown(r) {
  const byId = new Map(r.assertions.map((a) => [a.id, a]));
  const items = r.checklist;
  const L = [];
  L.push(`# QA findings: ${r.entry_url}`);
  L.push('');
  L.push(`- Result: **${r.disposition}** (automated rows: ${r.disposition_automated})`);
  L.push(`- Tested as: \`${r.scope || r.mode}\` · run \`${r.run_id}\` · ${r.finished_at} · next-launch-check ${r.tool_version}`);
  L.push(`- Lanes: ${Object.entries(r.lanes || {}).map(([k, v]) => `${k}: ${v}`).join('; ')}`);
  L.push(`- Pages: ${r.pages.filter((p) => p.in_scope).map((p) => `${p.role || '?'} ${p.final_url || p.url} (${p.status || 'error'})`).join(' · ')}`);
  L.push(`- Full data: \`results.json\` (rows in \`assertions\`, checklist in \`checklist\`). Human report: \`report.html\`.`);
  L.push('');
  const orders = orderSummary(r);
  if (orders.length) {
    L.push(`## Test orders (${orders.length})`);
    L.push('');
    for (const o of orders) L.push(`- ${o.ok ? 'PLACED' : 'NOT PLACED'} · ${o.label}: ${o.text}${o.shot ? ` (screenshot: ${o.shot})` : ''}`);
    L.push('');
  }
  const bad = items.filter((i) => i.status === 'fail' || i.status === 'warn').sort((a, b) => (a.severity === 'blocker' ? -1 : 0) - (b.severity === 'blocker' ? -1 : 0) || (a.status === 'fail' ? -1 : 1) - (b.status === 'fail' ? -1 : 1));
  L.push(`## Fix list (${bad.length})`);
  L.push('');
  if (!bad.length) L.push('Nothing failed.');
  for (const i of bad) {
    L.push(`### \`${i.id}\` — ${i.text}`);
    L.push(`${i.status === 'fail' ? 'FAIL' : 'WARN'}${i.severity === 'blocker' ? ' · blocker' : ''} · ${i.section}${i.note ? ` · tester: ${i.note}` : ''}`);
    L.push('');
    const rows = i.rows.map((id) => byId.get(id)).filter((a) => a && a.status !== 'pass' && a.status !== 'skipped');
    for (const a of rows.slice(0, 10)) {
      L.push(`- \`${a.id}\`${a.url ? ` — ${a.url}` : a.page ? ` — ${a.page}` : ''}`);
      L.push(`  - ${a.message}`);
      for (const e of evidenceLines(rowEvidence(a)).slice(0, 6)) L.push(`  - evidence: ${e.slice(0, 300)}`);
      if (a.screenshots) L.push(`  - screenshots: ${a.screenshots.join(', ')}`);
      const fix = fixFor(a);
      if (fix) L.push(`  - likely fix: ${fix}`);
    }
    if (rows.length > 10) L.push(`- …${rows.length - 10} more rows in results.json`);
    L.push('');
  }
  const open = items.filter((i) => i.status === 'open');
  const groups = [
    ['check', 'Confirm from the evidence', 'The script ran these and collected evidence; a person looks and answers.'],
    ['not_run', 'Automated, but not run this time', 'Each line says why and how to run it.'],
    ['manual', 'Manual checks (always done by a person)', 'No script can judge these.'],
  ];
  L.push(`## Open items (${open.length})`);
  L.push('');
  L.push('Answer with `node <skill>/scripts/launch-check.js finalize <this folder> --answers answers.json`, where answers.json maps item name to `pass`, `fail`, `warn` or `na` (or `{ "answer": "fail", "note": "…" }`).');
  L.push('');
  for (const [kind, title, line] of groups) {
    const list = open.filter((i) => KIND(i) === kind);
    if (!list.length) continue;
    L.push(`### ${title} (${list.length})`);
    L.push('');
    L.push(line);
    L.push('');
    for (const i of list) {
      const ev = [...i.rows, ...i.evidence_rows].map((id) => byId.get(id)).filter((a) => a && a.status !== 'pass' && a.status !== 'skipped');
      const why = kind === 'not_run' ? ` — ${notRunReason(i, r)}` : ev.length ? ` — evidence: ${ev.slice(0, 2).map((a) => a.message).join(' / ').slice(0, 260)}${ev.some((a) => a.screenshots) ? ` (screenshots: ${ev.flatMap((a) => a.screenshots || []).slice(0, 2).join(', ')})` : ''}` : '';
      L.push(`- \`${i.id}\` (${i.section}) ${i.text}${i.severity === 'blocker' ? ' · blocker' : ''}${why}`);
    }
    L.push('');
  }
  const passed = items.filter((i) => i.status === 'pass' || i.status === 'na');
  L.push(`## Passed or not applicable (${passed.length})`);
  L.push('');
  L.push(passed.map((i) => `\`${i.id}\``).join(' · ') || '—');
  L.push('');
  const other = CL.unmapped(items, counted(r.assertions)).filter((a) => a.status === 'fail' || a.status === 'warn');
  if (other.length) {
    L.push(`## Other findings (${other.length})`);
    L.push('');
    for (const a of other) L.push(`- \`${a.id}\` ${a.page ? `[${a.page}] ` : ''}${a.message}`);
    L.push('');
  }
  L.push('## Re-run');
  L.push('');
  L.push('```bash');
  L.push(`node <skill>/scripts/launch-check.js check "${r.entry_url}" --scope ${r.scope || 'funnel'}${r.options && r.options.rules ? ` --rules "${r.options.rules}"` : ''}`);
  L.push('```');
  L.push('');
  return L.join('\n');
}

// ---------- human HTML ----------
const esc = (s) => String(s === undefined || s === null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function html(r) {
  const byId = new Map(r.assertions.map((a) => [a.id, a]));
  const items = numbered(r.checklist);
  const tally = (s) => items.filter((i) => i.status === s).length;
  const [word, line] = RESULT[r.disposition] || [r.disposition, ''];
  const tone = { ready: 'ok', ready_with_exceptions: 'warn', blocked: 'bad', incomplete: 'info' }[r.disposition] || 'info';
  const pill = (s) => `<span class="pill ${s}">${STATUS_WORD[s] || s}</span>`;
  const orders = orderSummary(r);
  const shots = (list) => (list && list.length ? `<div class="shots">${list.map((s) => `<a href="${esc(s)}" target="_blank"><img src="${esc(s)}" alt="${esc(path.basename(s))}" loading="lazy"></a>`).join('')}</div>` : '');
  const detail = (i) => {
    const rows = i.rows.map((id) => byId.get(id)).filter(Boolean);
    const show = i.status === 'pass' ? rows.slice(0, 2) : rows.filter((a) => a.status !== 'pass' && a.status !== 'skipped').slice(0, 6);
    const ev = i.evidence_rows.map((id) => byId.get(id)).filter((a) => a && a.status !== 'pass' && a.status !== 'skipped').slice(0, 3);
    const parts = [...show, ...ev].map((a) => {
      const evid = evidenceLines(rowEvidence(a)).slice(0, 4).map((e) => `<li class="ev">${esc(e.slice(0, 240))}</li>`).join('');
      return `<li><span class="where">${esc(a.page ? a.page.replace(/^cos:/, '') : '')}</span> ${esc(a.message)}${evid ? `<ul>${evid}</ul>` : ''}${shots(a.screenshots)}</li>`;
    }).join('');
    const hint = i.status === 'open' && KIND(i) === 'not_run' ? `<p class="hint">${esc(notRunReason(i, r))}</p>` : i.hint && i.status === 'open' ? `<p class="hint">${esc(i.hint)}</p>` : '';
    return parts || hint ? `<ul class="rows">${parts}</ul>${hint}` : '';
  };
  const itemHtml = (i, withSection) => `<div class="item s-${i.status}">
      <div class="head">${pill(KIND(i))}<span class="num">${withSection ? `${esc(i.section)} · ` : ''}${i.n}.</span><span class="title">${esc(i.text)}</span>${i.severity === 'blocker' && i.status !== 'pass' ? '<span class="blocker">Blocker</span>' : ''}<span class="how">${esc(HOW(i))}</span></div>
      ${detail(i)}
    </div>`;
  const fix = items.filter((i) => i.status === 'fail' || i.status === 'warn').sort((a, b) => (b.severity === 'blocker') - (a.severity === 'blocker') || (a.status === 'fail' ? -1 : 1));
  const open = items.filter((i) => i.status === 'open');
  const sections = [...new Set(items.map((i) => i.section))];
  const speed = speedRows(r);
  const pages = r.pages.filter((p) => p.in_scope || p.role);
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>QA report — ${esc(r.entry_url)}</title>
<style>
:root{--ink:#14161a;--muted:#5d6470;--line:#e3e6ea;--bg:#f6f7f9;--ok:#127a3c;--ok-bg:#e6f4ea;--warn:#8a5a00;--warn-bg:#fdf3dc;--bad:#b3261e;--bad-bg:#fbe7e6;--info:#2f4f8f;--info-bg:#e8eefb;--na:#5d6470;--na-bg:#eef0f3}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--ink);background:var(--bg)}
main{max-width:980px;margin:0 auto;padding:32px 24px 64px}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:36px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}h3{font-size:15px;margin:22px 0 8px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.meta{color:var(--muted);font-size:13px;word-break:break-all}.banner{margin:20px 0;padding:18px 20px;border-radius:10px;background:#fff;border:1px solid var(--line);border-left:6px solid}
.banner.ok{border-left-color:var(--ok)}.banner.warn{border-left-color:#e0a100}.banner.bad{border-left-color:var(--bad)}.banner.info{border-left-color:var(--info)}.banner b{font-size:20px;display:block}
.cards{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:14px 0}.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px;text-align:center}.card b{display:block;font-size:24px}.card span{font-size:12px;color:var(--muted)}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:14px}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}th{background:#fafbfc;font-weight:600;font-size:13px;color:var(--muted)}td.num{font-variant-numeric:tabular-nums}
.item{background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:8px 0;break-inside:avoid}.head{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}.title{font-weight:600;flex:1;min-width:220px}.num{color:var(--muted);font-size:13px}.how{color:var(--muted);font-size:12px}
.pill{display:inline-block;min-width:70px;text-align:center;font-size:12px;font-weight:700;padding:2px 8px;border-radius:999px}.pill.pass{color:var(--ok);background:var(--ok-bg)}.pill.fail{color:var(--bad);background:var(--bad-bg)}.pill.warn{color:var(--warn);background:var(--warn-bg)}.pill.open,.pill.check{color:var(--info);background:var(--info-bg)}.pill.manual{color:#5b3a99;background:#efe9fb}.pill.not_run{color:var(--na);background:var(--na-bg)}.pill.na{color:var(--na);background:var(--na-bg)}
.blocker{font-size:11px;font-weight:700;color:#fff;background:var(--bad);padding:1px 7px;border-radius:4px}
.rows{margin:8px 0 2px;padding-left:18px;font-size:14px;color:#2b3038}.rows li{margin:4px 0}.rows ul{padding-left:16px}.ev{color:var(--muted);font-size:12.5px;word-break:break-all}.where{color:var(--muted);font-size:12px;font-family:ui-monospace,Menlo,monospace}
.shots{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0}.shots img{height:110px;max-width:220px;object-fit:cover;object-position:top;border:1px solid var(--line);border-radius:6px}
.hint{font-size:13px;color:var(--muted);margin:6px 0 0}pre{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px;white-space:pre-wrap;font-size:13px}
details>summary{cursor:pointer;color:var(--muted);font-size:14px;margin:8px 0}footer{margin-top:40px;color:var(--muted);font-size:12px}
@media (max-width:700px){.cards{grid-template-columns:repeat(2,1fr)}}
@media print{.full .shots{display:none}.shots img{height:90px}body{background:#fff}main{padding:0;max-width:none}.item,.banner,.card,table{border-color:#ccc}details{display:block}details>summary{display:none}details[open]>*,details>*{display:block}a{color:inherit;text-decoration:none}h2{break-after:avoid}}
</style></head><body><main>
<h1>QA report</h1>
<div class="meta">${esc(r.entry_url)}<br>Tested as <b>${esc(r.scope || r.mode)}</b> · ${esc(new Date(r.finished_at).toUTCString())} · run ${esc(r.run_id)}</div>
<div class="banner ${tone}"><b>${esc(word)}</b>${esc(line)}</div>
<div class="cards"><div class="card"><b>${tally('pass')}</b><span>Passed</span></div><div class="card"><b>${tally('fail')}</b><span>Failed</span></div><div class="card"><b>${tally('warn')}</b><span>Warnings</span></div><div class="card"><b>${open.filter((i) => KIND(i) === 'manual').length}</b><span>Manual</span></div><div class="card"><b>${open.filter((i) => KIND(i) !== 'manual').length}</b><span>Confirm / not run</span></div><div class="card"><b>${tally('na')}</b><span>N/A</span></div></div>
<div class="meta">What ran: ${esc(Object.entries(r.lanes || {}).map(([k, v]) => `${k.replace(/_/g, ' ')}: ${v}`).join(' · '))}</div>

${orders.length ? `<h2>Test orders (${orders.length})</h2><table><tr><th>Order</th><th>Result</th><th>What happened</th></tr>${orders.map((o) => `<tr><td>${esc(o.label)}</td><td>${pill(o.ok ? 'pass' : 'fail').replace(/>[^<]+</, `>${o.ok ? 'Placed' : 'Not placed'}<`)}</td><td>${esc(o.text)}${o.shot ? ` · <a href="${esc(o.shot)}">screenshot</a>` : ''}</td></tr>`).join('')}</table>` : ''}

${fix.length ? `<h2>What to fix (${fix.length})</h2>${fix.map((i) => itemHtml(i, true)).join('')}` : '<h2>What to fix</h2><p>Nothing failed.</p>'}


${[['check', 'Confirm from the evidence', 'The script ran these and collected the evidence below. Look and answer.'], ['not_run', 'Automated, but not run this time', 'Each item says why and how to run it.'], ['manual', 'Manual checks', 'Always done by a person; no script can judge these. Your assistant goes through them with you.']].map(([k, t, line]) => { const list = open.filter((i) => KIND(i) === k); return list.length ? `<h2>${t} (${list.length})</h2><p class="meta">${line}</p>${list.map((i) => itemHtml(i, true)).join('')}` : ''; }).join('\n')}

${speed.length ? `<h2>Speed</h2><table><tr><th>Page</th><th>Device</th><th>Score</th><th>First paint</th><th>Largest paint</th><th>Blocking</th><th>Layout shift</th><th>Speed index</th></tr>${speed.map((a) => { const m = a.actual; return `<tr><td>${esc(a.page)}</td><td>${esc(a.id.split(':').pop())}</td><td class="num">${pill(a.status === 'pass' ? 'pass' : 'fail').replace(/>[^<]+</, `>${m.score}<`)}</td><td class="num">${ms(m.fcp)}</td><td class="num">${ms(m.lcp)}</td><td class="num">${ms(m.tbt)}</td><td class="num">${m.cls === null ? '—' : m.cls.toFixed(2)}</td><td class="num">${ms(m.si)}</td></tr>`; }).join('')}</table><p class="meta">A page fails only when Lighthouse rates it poor: score under 50, largest paint over 4 s, blocking over 600 ms or layout shift over 0.25. Good is a score of 90+, largest paint under 2.5 s, blocking under 200 ms, layout shift under 0.1. Measured with ${esc(speed[0].actual.engine)}.</p>` : ''}

<h2>Pages tested</h2>
<table><tr><th>Page</th><th>Type</th><th>Status</th><th>Title</th></tr>${pages.map((p) => `<tr><td><a href="${esc(p.url)}">${esc(p.label)}</a></td><td>${esc(p.role || p.type)}${p.in_scope ? '' : ' <span class="meta">(followed, not tested)</span>'}</td><td>${p.status || 'error'}</td><td>${esc((p.title || '').slice(0, 70))}</td></tr>`).join('')}</table>

<h2>Full checklist</h2><div class="full">
${sections.map((sec) => `<h3>${esc(sec)}</h3>${items.filter((i) => i.section === sec).map((i) => itemHtml(i, false)).join('')}`).join('')}</div>

<h2>Summary to paste</h2>
<pre>${esc(summaryText(r))}</pre>
<footer>next-launch-check ${esc(r.tool_version)} · developer notes in report.md · all data in results.json</footer>
</main></body></html>
`;
}

function summaryText(r) {
  const items = r.checklist;
  const lines = [`QA (${r.scope || r.mode}) — ${r.entry_url}`, `Result: ${(RESULT[r.disposition] || [r.disposition])[0]} (${r.finished_at.slice(0, 10)})`];
  const speed = speedRows(r);
  if (speed.length) lines.push(`Speed: ${speed.map((a) => `${a.page} ${a.id.split(':').pop()} ${a.actual.score}`).join(', ')}`);
  const bad = items.filter((i) => i.status === 'fail').concat(items.filter((i) => i.status === 'warn'));
  if (bad.length) {
    lines.push('Needs fixing:');
    const byId = new Map(r.assertions.map((a) => [a.id, a]));
    for (const i of bad.slice(0, 25)) {
      const first = i.rows.map((id) => byId.get(id)).find((a) => a && a.status !== 'pass' && a.status !== 'skipped');
      lines.push(`- ${i.text}${i.note ? ` — ${i.note}` : first ? ` — ${first.page ? `[${first.page.replace(/^cos:/, '')}] ` : ''}${first.message}` : ''}`);
    }
  } else lines.push('No failed checklist items.');
  const orders = orderSummary(r);
  if (orders.length) lines.push(`Test orders: ${orders.map((o) => `${o.label} ${o.ok ? 'placed' : 'NOT placed'}`).join(', ')}`);
  const open = items.filter((i) => i.status === 'open');
  if (open.length) lines.push(`Open: ${open.filter((i) => KIND(i) === 'manual').length} manual, ${open.filter((i) => KIND(i) === 'check').length} to confirm, ${open.filter((i) => KIND(i) === 'not_run').length} not run.`);
  return lines.join('\n');
}

function write(outDir, result) {
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'results.json'), JSON.stringify(result, null, 2) + '\n');
  fs.writeFileSync(path.join(outDir, 'report.md'), markdown(result));
  fs.writeFileSync(path.join(outDir, 'report.html'), html(result));
}

// report.pdf from report.html, when Playwright is installed.
async function pdf(outDir) {
  const PW = require('../browser/playwright').load();
  if (!PW) return null;
  let browser;
  try {
    browser = await PW.pw.chromium.launch();
    const page = await browser.newPage();
    await page.goto('file://' + path.join(outDir, 'report.html'), { waitUntil: 'load' });
    const file = path.join(outDir, 'report.pdf');
    await page.pdf({ path: file, format: 'A4', printBackground: true, margin: { top: '14mm', bottom: '14mm', left: '12mm', right: '12mm' } });
    return file;
  } catch (_) { return null; } finally { if (browser) await browser.close().catch(() => {}); }
}

module.exports = { KIND, notRunReason, orderSummary, automatedDisposition, counts, markdown, html, summaryText, write, pdf };
