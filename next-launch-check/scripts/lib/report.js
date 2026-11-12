'use strict';
const fs = require('fs');
const path = require('path');

const ORDER = { fail: 0, warn: 1, manual_review: 2, skipped: 3, pass: 4 };

function counted(assertions) {
  return assertions.filter((a) => !a.waived && !a.deferred);
}

function automatedDisposition(assertions) {
  const live = counted(assertions);
  if (live.some((a) => a.status === 'fail' && a.severity === 'blocker')) return 'blocked';
  // Rows with severity info are advice; they show in the report but do not
  // turn a clean run into an exception.
  if (live.some((a) => a.status === 'fail' || ((a.status === 'warn' || a.status === 'manual_review') && a.severity !== 'info'))) return 'ready_with_exceptions';
  return 'ready';
}

function finalDisposition(automated, manual) {
  const open = manual.filter((m) => !m.answer);
  if (manual.some((m) => m.answer === 'fail' && m.severity === 'blocker')) return 'blocked';
  if (automated === 'blocked') return 'blocked';
  if (open.length) return 'incomplete';
  if (manual.some((m) => m.answer === 'fail')) return 'ready_with_exceptions';
  return automated;
}

function counts(assertions) {
  const c = { pass: 0, fail: 0, warn: 0, manual_review: 0, skipped: 0, waived: 0, deferred: 0 };
  for (const a of assertions) {
    if (a.waived) c.waived++;
    else if (a.deferred) c.deferred++;
    else c[a.status]++;
  }
  return c;
}

const ICON = { pass: 'PASS', fail: 'FAIL', warn: 'WARN', manual_review: 'CHECK', skipped: 'SKIP' };
const WORDS = {
  ready: 'Ready: every automated check passed.',
  ready_with_exceptions: 'Ready with exceptions: no blockers, but there are warnings or items to check by hand.',
  blocked: 'Blocked: at least one blocker must be fixed before launch.',
  incomplete: 'Incomplete: automated checks are done; the manual checklist still has open items.',
};

function esc(s) { return String(s === undefined || s === null ? '' : s).replace(/\|/g, '\\|').replace(/\n/g, ' '); }
function evidenceLines(e) {
  if (e === undefined || e === null) return [];
  if (typeof e === 'string') return [e];
  if (Array.isArray(e)) return e.map((x) => (typeof x === 'string' ? x : JSON.stringify(x)));
  return [JSON.stringify(e)];
}

function markdown(r) {
  const L = [];
  L.push(`# Launch check: ${r.entry_url}`);
  L.push('');
  L.push(`Mode \`${r.mode}\` · run \`${r.run_id}\` · ${r.finished_at} · next-launch-check ${r.tool_version}`);
  L.push('');
  L.push(`**${WORDS[r.disposition] || r.disposition}**`);
  L.push('');
  const c = r.counts;
  L.push(`Automated: ${c.pass} pass · ${c.fail} fail · ${c.warn} warn · ${c.manual_review} to check · ${c.skipped} skipped${c.waived ? ` · ${c.waived} by design` : ''}${c.deferred ? ` · ${c.deferred} deferred to campaigns-os` : ''}. Manual checklist: ${r.manual.filter((m) => m.answer).length}/${r.manual.length} answered.`);
  L.push('');
  if (r.lanes) {
    L.push(`Lanes: ${Object.entries(r.lanes).map(([k, v]) => `${k} ${v}`).join(' · ')}`);
    L.push('');
  }
  if (r.campaigns_os) {
    L.push(`Campaigns OS verdict: \`${r.campaigns_os.disposition}\` from \`${r.campaigns_os.path}\`. It is the authority for the families it covers; those rows here are marked deferred.`);
    L.push('');
  }
  L.push('## Pages');
  L.push('');
  L.push('| Page | Type | HTTP | Title | Found via |');
  L.push('|---|---|---|---|---|');
  for (const p of r.pages) L.push(`| [${esc(p.label)}](${p.url}) | ${esc(p.type)} | ${p.status || 'error'} | ${esc((p.title || '').slice(0, 60))} | ${esc(p.via)} |`);
  L.push('');
  const live = r.assertions.filter((a) => !a.waived && !a.deferred);
  const section = (title, list) => {
    if (!list.length) return;
    L.push(`## ${title}`);
    L.push('');
    for (const a of list) {
      L.push(`- **${ICON[a.status]}** \`${a.id}\`${a.severity === 'blocker' ? ' (blocker)' : ''}: ${a.message}`);
      const ev = a.evidence !== undefined ? a.evidence : a.status !== 'pass' && a.actual !== undefined && typeof a.actual === 'object' ? a.actual : undefined;
      for (const e of evidenceLines(ev).slice(0, 12)) L.push(`  - ${e}`);
      if (a.screenshots) for (const s of a.screenshots) L.push(`  - screenshot: [${path.basename(s)}](${s})`);
    }
    L.push('');
  };
  section('Blockers', live.filter((a) => a.status === 'fail' && a.severity === 'blocker'));
  section('Failures', live.filter((a) => a.status === 'fail' && a.severity !== 'blocker'));
  section('Warnings', live.filter((a) => a.status === 'warn'));
  section('Check by hand (evidence attached)', live.filter((a) => a.status === 'manual_review'));
  section('Skipped', live.filter((a) => a.status === 'skipped'));
  const waived = r.assertions.filter((a) => a.waived);
  if (waived.length) {
    L.push('## By design (from the rules file)');
    L.push('');
    for (const a of waived) L.push(`- \`${a.id}\` (${ICON[a.status]}): ${a.message} Reason: ${a.waived}`);
    L.push('');
  }
  const passes = live.filter((a) => a.status === 'pass');
  if (passes.length) {
    const byFamily = {};
    for (const a of passes) byFamily[a.family] = (byFamily[a.family] || 0) + 1;
    L.push('## Passed');
    L.push('');
    L.push(Object.entries(byFamily).map(([f, n]) => `\`${f}\` ${n}`).join(' · '));
    L.push('');
  }
  L.push('## Manual checklist');
  L.push('');
  let sec = null;
  for (const m of r.manual) {
    if (m.section !== sec) { sec = m.section; L.push(`**${sec}**`); L.push(''); }
    const box = m.answer === 'pass' ? '[x]' : m.answer === 'na' ? '[-]' : m.answer === 'fail' ? '[!]' : '[ ]';
    L.push(`- ${box} \`${m.id}\` ${m.text}${m.severity === 'blocker' ? ' (blocker)' : ''}${m.note ? ` — ${m.note}` : ''}`);
  }
  L.push('');
  L.push('## Summary to paste');
  L.push('');
  L.push('```');
  L.push(summaryText(r));
  L.push('```');
  L.push('');
  return L.join('\n');
}

function summaryText(r) {
  const live = r.assertions.filter((a) => !a.waived && !a.deferred);
  const lines = [];
  lines.push(`Launch check — ${r.entry_url}`);
  lines.push(`Result: ${r.disposition.replace(/_/g, ' ')} (${r.finished_at.slice(0, 10)})`);
  const bad = live.filter((a) => a.status === 'fail').concat(live.filter((a) => a.status === 'warn' && a.severity !== 'info'));
  if (bad.length) {
    lines.push('Needs attention:');
    for (const a of bad.slice(0, 20)) lines.push(`- ${a.page ? `[${a.page}] ` : ''}${a.message}`);
    if (bad.length > 20) lines.push(`- …and ${bad.length - 20} more in the report`);
  } else {
    lines.push('No automated failures.');
  }
  const failedManual = r.manual.filter((m) => m.answer === 'fail');
  for (const m of failedManual) lines.push(`- [manual ${m.id}] ${m.text}${m.note ? ` — ${m.note}` : ''}`);
  const open = r.manual.filter((m) => !m.answer).length;
  if (open) lines.push(`Manual checklist: ${open} item(s) still open.`);
  return lines.join('\n');
}

function write(outDir, result) {
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'results.json'), JSON.stringify(result, null, 2) + '\n');
  fs.writeFileSync(path.join(outDir, 'report.md'), markdown(result));
}

module.exports = { automatedDisposition, finalDisposition, counts, markdown, summaryText, write };
