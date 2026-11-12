'use strict';
const fs = require('fs');
const path = require('path');
const CL = require('./checklist');

function counted(rows) { return rows.filter((a) => !a.waived && !a.deferred); }

function automatedDisposition(rows) {
  const live = counted(rows);
  if (live.some((a) => a.status === 'fail' && a.severity === 'blocker')) return 'blocked';
  // Rows with severity info are advice; they show in the report but do not
  // turn a clean run into an exception.
  if (live.some((a) => a.status === 'fail' || ((a.status === 'warn' || a.status === 'manual_review') && a.severity !== 'info'))) return 'ready_with_exceptions';
  return 'ready';
}

function counts(rows) {
  const c = { pass: 0, fail: 0, warn: 0, manual_review: 0, skipped: 0, waived: 0, deferred: 0 };
  for (const a of rows) {
    if (a.waived) c.waived++;
    else if (a.deferred) c.deferred++;
    else c[a.status]++;
  }
  return c;
}

const WORDS = {
  ready: 'Ready — every checklist item passed.',
  ready_with_exceptions: 'Ready with exceptions — no blockers, but some items failed or need attention.',
  blocked: 'Blocked — at least one blocker must be fixed before launch.',
  incomplete: 'Incomplete — automated checks are done; some checklist items still need your answer.',
};
const MARK = { pass: 'PASS', fail: 'FAIL', warn: 'WARN', na: 'N/A', open: 'TODO' };
const ROW = { pass: 'pass', fail: 'FAIL', warn: 'warn', manual_review: 'look', skipped: 'skipped' };

function evidenceLines(e) {
  if (e === undefined || e === null) return [];
  if (typeof e === 'string') return [e];
  if (Array.isArray(e)) return e.map((x) => (typeof x === 'string' ? x : JSON.stringify(x)));
  return [JSON.stringify(e)];
}
function esc(s) { return String(s === undefined || s === null ? '' : s).replace(/\|/g, '\\|').replace(/\n/g, ' '); }

function rowLines(a, indent = '  ') {
  const L = [`${indent}- ${ROW[a.status]}${a.page ? ` [${a.page}]` : ''}: ${a.message}`];
  const ev = a.status === 'pass' ? undefined : a.evidence !== undefined ? a.evidence : typeof a.actual === 'object' ? a.actual : undefined;
  for (const e of evidenceLines(ev).slice(0, 8)) L.push(`${indent}  - ${e}`);
  if (a.screenshots) L.push(`${indent}  - screenshots: ${a.screenshots.map((s) => `[${path.basename(s)}](${s})`).join(' · ')}`);
  return L;
}

function markdown(r) {
  const L = [];
  const byId = new Map(r.assertions.map((a) => [a.id, a]));
  const items = r.checklist;
  const tally = (s) => items.filter((i) => i.status === s).length;
  L.push(`# QA report: ${r.entry_url}`);
  L.push('');
  L.push(`Testing: **${r.scope || r.mode}** · run \`${r.run_id}\` · ${r.finished_at} · next-launch-check ${r.tool_version}`);
  L.push('');
  L.push(`**${WORDS[r.disposition] || r.disposition}**`);
  L.push('');
  L.push(`Checklist: ${tally('pass')} pass · ${tally('fail')} fail · ${tally('warn')} warn · ${tally('open')} to do · ${tally('na')} n/a — ${items.length} items for ${[...new Set(items.flatMap((i) => i.roles))].join(', ')}.`);
  L.push('');
  if (r.lanes) L.push(`Ran: ${Object.entries(r.lanes).map(([k, v]) => `${k} ${v}`).join(' · ')}`);
  L.push('');
  if (r.campaigns_os) {
    L.push(`Campaigns OS verdict \`${r.campaigns_os.disposition}\` (\`${r.campaigns_os.path}\`) is the authority for the families it covers; those rows are deferred here.`);
    L.push('');
  }
  L.push('## Pages');
  L.push('');
  L.push('| Page | Tested as | HTTP | Title | Found via |');
  L.push('|---|---|---|---|---|');
  for (const p of r.pages) L.push(`| [${esc(p.label)}](${p.url}) | ${p.in_scope ? `**${esc(p.role)}**` : `${esc(p.role)} (not in scope)`} | ${p.status || 'error'} | ${esc((p.title || '').slice(0, 60))} | ${esc(p.via)} |`);
  L.push('');
  const speed = r.assertions.filter((a) => a.family === 'speed' && a.actual);
  if (speed.length) {
    L.push('## Speed');
    L.push('');
    L.push('| Page | Device | Score | FCP | LCP | TBT | CLS | Speed Index | Report |');
    L.push('|---|---|---|---|---|---|---|---|---|');
    const ms = (v) => (v === null || v === undefined ? '—' : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`);
    for (const a of speed) {
      const m = a.actual;
      L.push(`| ${esc(a.page)} | ${a.id.split(':').pop()} | ${m.score}${a.status === 'pass' ? '' : ' ⚠'} | ${ms(m.fcp)} | ${ms(m.lcp)} | ${ms(m.tbt)} | ${m.cls === null ? '—' : m.cls.toFixed(2)} | ${ms(m.si)} | [PageSpeed](${a.evidence}) |`);
    }
    L.push('');
    L.push(`Engine: ${speed[0].actual.engine}. Targets: score ≥ 50, FCP ≤ 3 s, LCP ≤ 4 s, TBT ≤ 200 ms, CLS ≤ 0.1, Speed Index ≤ 4.4 s.`);
    L.push('');
  }
  L.push('## Checklist');
  L.push('');
  let sec = null;
  for (const i of items) {
    if (i.section !== sec) { sec = i.section; L.push(`### ${sec}`); L.push(''); }
    const how = i.answer ? `answered by tester${i.note ? `: ${i.note}` : ''}` : i.auto === 'manual' ? 'check by hand' : i.auto === 'not_run' ? 'check by hand (the automated check did not run)' : i.auto === 'check' ? 'look at the evidence and answer' : 'automated';
    L.push(`- **${MARK[i.status]}** \`${i.id}\` ${i.text}${i.severity === 'blocker' ? ' · blocker' : ''} — _${how}_`);
    const shown = i.rows.map((id) => byId.get(id)).filter(Boolean);
    if (i.status === 'pass') {
      for (const a of shown.slice(0, 2)) L.push(...rowLines(a));
      if (shown.length > 2) L.push(`  - …and ${shown.length - 2} more passing check(s)`);
    } else {
      const bad = shown.filter((a) => a.status !== 'pass' && a.status !== 'skipped');
      for (const a of bad.slice(0, 8)) L.push(...rowLines(a));
      if (bad.length > 8) L.push(`  - …and ${bad.length - 8} more in results.json`);
      const ok = shown.length - bad.length;
      if (ok) L.push(`  - (${ok} other check(s) for this item passed or did not apply)`);
    }
    for (const a of i.evidence_rows.map((id) => byId.get(id)).filter((a) => a && a.status !== 'pass' && a.status !== 'skipped').slice(0, 4)) L.push(...rowLines(a));
    if (i.hint && i.status === 'open') L.push(`  - ${i.hint}`);
  }
  L.push('');
  const other = CL.unmapped(items, counted(r.assertions)).filter((a) => a.status !== 'pass');
  if (other.length) {
    L.push('## Other findings');
    L.push('');
    for (const a of other) L.push(...rowLines(a, ''));
    L.push('');
  }
  const waived = r.assertions.filter((a) => a.waived);
  if (waived.length) {
    L.push('## By design (from your rules file)');
    L.push('');
    for (const a of waived) L.push(`- \`${a.id}\`: ${a.message} Reason: ${a.waived}`);
    L.push('');
  }
  L.push('## Summary to paste');
  L.push('');
  L.push('```');
  L.push(summaryText(r));
  L.push('```');
  L.push('');
  return L.join('\n');
}

function summaryText(r) {
  const items = r.checklist;
  const lines = [`QA (${r.scope || r.mode}) — ${r.entry_url}`, `Result: ${r.disposition.replace(/_/g, ' ')} (${r.finished_at.slice(0, 10)})`];
  const speed = r.assertions.filter((a) => a.family === 'speed' && a.actual);
  if (speed.length) lines.push(`Speed: ${speed.map((a) => `${a.page} ${a.id.split(':').pop()} ${a.actual.score}`).join(', ')}`);
  const failed = items.filter((i) => i.status === 'fail');
  const warned = items.filter((i) => i.status === 'warn');
  if (failed.length || warned.length) {
    lines.push('Needs fixing:');
    const byId = new Map(r.assertions.map((a) => [a.id, a]));
    for (const i of [...failed, ...warned].slice(0, 25)) {
      const first = i.rows.map((id) => byId.get(id)).find((a) => a && a.status !== 'pass');
      lines.push(`- ${i.id} ${i.text}${i.note ? ` — ${i.note}` : first ? ` — ${first.page ? `[${first.page}] ` : ''}${first.message}` : ''}`);
    }
  } else lines.push('No failed checklist items.');
  const open = items.filter((i) => i.status === 'open').length;
  if (open) lines.push(`Still to check by hand: ${open} item(s).`);
  return lines.join('\n');
}

function write(outDir, result) {
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'results.json'), JSON.stringify(result, null, 2) + '\n');
  fs.writeFileSync(path.join(outDir, 'report.md'), markdown(result));
}

module.exports = { automatedDisposition, counts, markdown, summaryText, write };
