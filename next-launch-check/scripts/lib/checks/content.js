'use strict';
const { assertion } = require('../util');
const H = require('../html');

const PATTERNS = [
  [/lorem ipsum/i, 'lorem ipsum'],
  [/\bTODO\b|\bFIXME\b|\bTBD\b/, 'TODO / FIXME / TBD'],
  [/placeholder text|your text here|sample text|insert (?:text|copy|headline)|headline goes here/i, 'placeholder copy'],
  [/\{\{\s*[\w.]+\s*\}\}|\{%\s*\w+/, 'unrendered template tag'],
  [/\[(?:product|brand|price|name) ?(?:name|here)?\]/i, 'bracketed placeholder'],
];

module.exports = async function content(ctx) {
  const out = [];
  for (const p of ctx.pages.filter((x) => x.ok)) {
    const base = { family: 'content-hygiene', page: p.label, url: p.url };
    const visible = H.text(p.html);
    const hits = PATTERNS.filter(([re]) => re.test(visible)).map(([re, name]) => {
      const m = re.exec(visible);
      return `${name}: "…${visible.slice(Math.max(0, m.index - 30), m.index + 40)}…"`;
    });
    out.push(hits.length
      ? assertion({ ...base, id: `content-hygiene:${p.label}:placeholder`, status: 'fail', severity: 'warn', message: 'Placeholder or unfinished copy is visible.', evidence: hits })
      : assertion({ ...base, id: `content-hygiene:${p.label}:placeholder`, status: 'pass', severity: 'warn', message: 'No placeholder copy found.' }));
    const clean = H.stripNoise(p.html);
    const empty = [];
    for (let level = 1; level <= 6; level++) {
      for (const h of H.tags(clean, `h${level}`, { inner: true })) {
        if (!H.text(h.inner) && !/<img\b|<svg\b/i.test(h.inner)) empty.push(`h${level}`);
      }
    }
    if (empty.length) out.push(assertion({ ...base, id: `content-hygiene:${p.label}:empty-headings`, status: 'warn', severity: 'info', message: `${empty.length} empty heading(s) in the source. Fine if the SDK or a script fills them; check the rendered page.`, evidence: empty }));
    const noAlt = H.tags(clean, 'img').filter((i) => !('alt' in i.attrs)).map((i) => (i.attrs.src || i.attrs['data-src'] || '').split('/').pop().slice(0, 60));
    if (noAlt.length) out.push(assertion({ ...base, id: `content-hygiene:${p.label}:alt`, status: 'warn', severity: 'info', message: `${noAlt.length} image(s) have no alt attribute.`, evidence: noAlt.slice(0, 15) }));
  }
  return out;
};
