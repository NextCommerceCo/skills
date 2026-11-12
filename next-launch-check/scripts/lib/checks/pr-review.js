'use strict';
// Static review of the lines a pull request adds. It reads a unified diff
// (from `gh pr diff` or `git diff`) and flags patterns that often break
// launch: prices typed into markup, custom cart calls, secrets, leftovers.
const fs = require('fs');
const { assertion } = require('../util');

function addedLines(diff) {
  const out = [];
  let file = null;
  let line = 0;
  for (const raw of diff.split('\n')) {
    if (raw.startsWith('+++ ')) { file = raw.replace(/^\+\+\+ (b\/)?/, '').trim(); continue; }
    const hunk = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(raw);
    if (hunk) { line = parseInt(hunk[1], 10); continue; }
    if (!file || file === '/dev/null') continue;
    if (raw.startsWith('+') && !raw.startsWith('+++')) { out.push({ file, line, text: raw.slice(1) }); line += 1; }
    else if (!raw.startsWith('-')) line += 1;
  }
  return out;
}

const RULES = [
  { id: 'hardcoded-price', severity: 'warn', files: /\.(html?|njk|liquid|hbs|jsx|tsx|vue|astro|md)$/i,
    test: (t) => /(?:[$€£]\s?\d{1,4}(?:[.,]\d{2})?|\d{1,4}[.,]\d{2}\s?(?:[$€£]|USD|EUR|GBP))/.test(t) && !/data-next-[a-z-]*(display|format|price)/.test(t),
    message: 'A money amount is typed into the markup. Bind it to the SDK (data-next-display) so a reprice or a currency switch cannot leave it wrong.' },
  { id: 'custom-cart-call', severity: 'warn', files: /\.(js|ts|jsx|tsx|html?|njk|vue|astro)$/i,
    test: (t) => /fetch\([^)]*(\/api\/v1\/(carts|orders)|campaigns?\.[a-z.]*\/api)/i.test(t) || /XMLHttpRequest[\s\S]*\/api\/v1\//.test(t),
    message: 'Page code calls the cart or order API directly. Use the SDK\'s data-next-* attributes or its JavaScript API so totals, offers and tracking stay in step.' },
  { id: 'secret', severity: 'blocker', files: /.*/,
    test: (t) => /\b(sk_live_|sk_test_|ghp_|xox[bp]-|AKIA[0-9A-Z]{12,})/.test(t) || /(admin[_-]?api[_-]?token|client[_-]?secret|private[_-]?key)\s*[:=]\s*['"][^'"]{8,}/i.test(t),
    message: 'Looks like a secret in the diff. Remove it, rotate it, and load it from the environment.' },
  { id: 'debug-leftover', severity: 'info', files: /\.(js|ts|jsx|tsx|html?)$/i,
    test: (t) => /\bconsole\.(log|debug)\(|\bdebugger;/.test(t),
    message: 'Debug output left in page code.' },
  { id: 'placeholder', severity: 'warn', files: /\.(html?|njk|liquid|hbs|jsx|tsx|vue|astro|md|json)$/i,
    test: (t) => /lorem ipsum|\bTODO\b|\bFIXME\b|example\.com|your text here/i.test(t),
    message: 'Placeholder or unfinished content.' },
  { id: 'route-tag-change', severity: 'info', files: /.*/,
    test: (t) => /next-(success|next|upsell-accept|upsell-decline)-url|next-page-type/.test(t),
    message: 'A funnel route tag changed. The funnel-chain family checks where it now leads; confirm that is intended.' },
  { id: 'sdk-version-change', severity: 'info', files: /.*/,
    test: (t) => /campaign[-.]cart[^'"\s]*@v?\d+\.\d+/i.test(t),
    message: 'The SDK version changed. Run a full funnel pass including a test order, since behaviour can differ between versions.' },
];

function review(diff) {
  const lines = addedLines(diff);
  const out = [];
  for (const rule of RULES) {
    const hits = lines.filter((l) => rule.files.test(l.file) && rule.test(l.text));
    if (!hits.length) continue;
    out.push(assertion({ id: `pr-review:${rule.id}`, family: 'pr-review', status: rule.severity === 'info' ? 'manual_review' : 'fail', severity: rule.severity, message: `${rule.message} (${hits.length} line${hits.length > 1 ? 's' : ''})`, evidence: hits.slice(0, 20).map((h) => `${h.file}:${h.line}  ${h.text.trim().slice(0, 140)}`) }));
  }
  const files = [...new Set(lines.map((l) => l.file))];
  out.push(assertion({ id: 'pr-review:scope', family: 'pr-review', status: 'pass', severity: 'info', message: `${lines.length} added line(s) in ${files.length} file(s) reviewed.`, evidence: files.slice(0, 40) }));
  return out;
}

module.exports = async function prReview(ctx) {
  if (!ctx.options.diff) return [];
  return review(fs.readFileSync(ctx.options.diff, 'utf8'));
};
module.exports.review = review;
module.exports.addedLines = addedLines;
