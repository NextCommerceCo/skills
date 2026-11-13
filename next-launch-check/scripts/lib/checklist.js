'use strict';
// Turns automated rows into the QA checklist. Each catalog item applies to
// some page types (roles). Its status comes from the rows its `auto` globs
// match on pages of those types; with no such rows the tester answers it.
const fs = require('fs');
const path = require('path');

const CATALOG = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'references', 'checklist.json'), 'utf8'));
const RANK = { fail: 0, warn: 1, check: 2, pass: 3 };

function globRe(glob) {
  return new RegExp('^' + glob.split('*').map((s) => s.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('.*') + '$');
}

function matcher(globs = []) {
  const inc = globs.filter((g) => !g.startsWith('!')).map(globRe);
  const exc = globs.filter((g) => g.startsWith('!')).map((g) => globRe(g.slice(1)));
  return (id) => inc.some((r) => r.test(id)) && !exc.some((r) => r.test(id));
}

// Roles present in this run, from the pages in scope.
function rolesOf(pages) {
  return new Set(pages.filter((p) => p.in_scope !== false).map((p) => p.role).filter(Boolean));
}

function rowsFor(item, rows, pageRole, globs) {
  const m = matcher(globs);
  return rows.filter((r) => {
    if (r.waived || r.deferred || !m(r.id)) return false;
    if (!r.page && !r.role) return true; // run-wide rows (consistency across pages)
    const role = r.role !== undefined ? r.role : pageRole.get(r.page);
    return !role || item.roles.includes(role);
  });
}

function autoStatus(rows) {
  if (!rows.length) return null;
  const live = rows.filter((r) => r.status !== 'skipped');
  if (!live.length) return null;
  let worst = 'pass';
  for (const r of live) {
    const s = r.status === 'fail' ? 'fail' : r.status === 'warn' && r.severity !== 'info' ? 'warn' : r.status === 'manual_review' && r.severity !== 'info' ? 'check' : 'pass';
    if (RANK[s] < RANK[worst]) worst = s;
  }
  // Only advice-level "no visible change" rows: still needs a person.
  if (worst === 'pass' && live.every((r) => r.status === 'manual_review')) worst = 'check';
  return worst;
}

function build(pages, rows, { prior = [], extra = [] } = {}) {
  const roles = rolesOf(pages);
  const pageRole = new Map(pages.map((p) => [p.label, p.role]));
  const answers = new Map(prior.filter((i) => i.answer).map((i) => [i.id, { answer: i.answer, note: i.note || null }]));
  const items = [];
  const cosRan = rows.some((r) => r.id === 'campaigns-os:verdict');
  const prRan = rows.some((r) => r.family === 'pr-review' && r.status !== 'skipped');
  for (const item of [...CATALOG.items, ...extra]) {
    if (!item.roles.some((r) => roles.has(r))) continue;
    if (item.requires === 'campaigns-os' && !cosRan) continue;
    if (item.requires === 'pr' && !prRan) continue;
    const autoRows = item.auto ? rowsFor(item, rows, pageRole, item.auto) : [];
    const evRows = item.evidence ? rowsFor(item, rows, pageRole, item.evidence) : [];
    const auto = autoStatus(autoRows);
    const blocker = autoRows.some((r) => r.status === 'fail' && r.severity === 'blocker');
    const out = {
      id: item.id, section: item.section, text: item.text, severity: blocker ? 'blocker' : item.severity,
      roles: item.roles.filter((r) => roles.has(r)),
      // manual: always a person; not_run: automated, but it did not run this
      // time; check: automated evidence a person confirms.
      auto: auto || (item.auto ? 'not_run' : 'manual'),
      auto_globs: item.auto || [],
      rows: autoRows.map((r) => r.id),
      evidence_rows: evRows.map((r) => r.id),
      hint: item.hint || null,
      answer: null, note: null,
    };
    const a = answers.get(item.id);
    if (a) { out.answer = a.answer; out.note = a.note; }
    out.status = finalStatus(out);
    items.push(out);
  }
  return items;
}

// pass | fail | warn | na  — or open (check, manual, not_run) until answered.
function finalStatus(i) {
  if (i.answer) return i.answer;
  if (['pass', 'fail', 'warn'].includes(i.auto)) return i.auto;
  return 'open';
}

function disposition(items, rowsDisposition) {
  if (rowsDisposition === 'blocked' || items.some((i) => i.status === 'fail' && i.severity === 'blocker')) return 'blocked';
  if (items.some((i) => i.status === 'open')) return 'incomplete';
  if (rowsDisposition === 'ready_with_exceptions' || items.some((i) => i.status === 'fail' || i.status === 'warn')) return 'ready_with_exceptions';
  return 'ready';
}

// Rows that no applicable item covers, for the "other findings" section.
function unmapped(items, rows) {
  const used = new Set(items.flatMap((i) => [...i.rows, ...i.evidence_rows]));
  return rows.filter((r) => !used.has(r.id));
}

module.exports = { CATALOG, build, disposition, unmapped, matcher, finalStatus };
