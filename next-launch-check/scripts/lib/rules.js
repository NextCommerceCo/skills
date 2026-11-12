'use strict';
// Optional rules file: an agency's or a client's own QA rules.
//  - JSON: structured rules (see references/rules-file.md).
//  - Markdown: every list item becomes a manual checklist item under its
//    heading, so a team's existing QA document can be passed as is.
const fs = require('fs');
const path = require('path');

function empty() { return { name: null, require: {}, allowedParams: null, byDesign: [], manual: [], source: null }; }

function fromMarkdown(text, file) {
  const rules = empty();
  rules.name = path.basename(file);
  rules.source = file;
  let body = text.replace(/^---\n[\s\S]*?\n---\n/, '');
  body = body.replace(/```[\s\S]*?```/g, '');
  let section = 'Rules file';
  let n = 0;
  for (const raw of body.split('\n')) {
    const line = raw.trimEnd();
    const h = /^#{1,6}\s+(.*)$/.exec(line);
    if (h) { section = h[1].trim(); continue; }
    const item = /^\s{0,3}(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.+)$/.exec(line);
    if (item && item[1].trim().length > 3) {
      n += 1;
      rules.manual.push({ id: `R-${String(n).padStart(2, '0')}`, section, text: item[1].trim(), severity: 'warn' });
    }
  }
  return rules;
}

function fromJson(obj, file) {
  const rules = empty();
  rules.name = obj.name || path.basename(file);
  rules.source = file;
  rules.require = obj.require || {};
  rules.allowedParams = obj.allowedParams || null;
  rules.byDesign = Array.isArray(obj.byDesign) ? obj.byDesign : [];
  rules.manual = (obj.manual || []).map((m, i) => ({ id: m.id || `R-${String(i + 1).padStart(2, '0')}`, section: m.section || rules.name, text: m.text, severity: m.severity || 'warn' }));
  return rules;
}

function load(file) {
  if (!file) return empty();
  const text = fs.readFileSync(file, 'utf8');
  if (/\.json$/i.test(file)) return fromJson(JSON.parse(text), file);
  return fromMarkdown(text, file);
}

function globToRe(glob) {
  return new RegExp('^' + glob.split('*').map((s) => s.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('.*') + '$');
}

// Mark assertions the rules file says are by design. They stay in the report
// with the reason, and no longer count against the disposition.
function applyByDesign(assertions, rules) {
  if (!rules.byDesign.length) return assertions;
  const pats = rules.byDesign.map((b) => ({ re: globToRe(b.id), reason: b.reason || 'by design' }));
  return assertions.map((a) => {
    if (a.status === 'pass' || a.status === 'skipped') return a;
    const hit = pats.find((p) => p.re.test(a.id));
    return hit ? { ...a, waived: hit.reason } : a;
  });
}

module.exports = { load, fromMarkdown, fromJson, applyByDesign, globToRe, empty };
