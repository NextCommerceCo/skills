'use strict';
const { assertion } = require('../util');
const H = require('../html');

module.exports = async function seoMeta(ctx) {
  const out = [];
  for (const p of ctx.pages.filter((x) => x.ok)) {
    const base = { family: 'seo-meta', page: p.label, url: p.url };
    const m = p.meta;
    const issues = [];
    if (!p.title) issues.push('no <title>');
    else if (p.title.length > 60) issues.push(`title is ${p.title.length} characters (search results cut at about 60)`);
    if (!m.description) issues.push('no meta description');
    else if (m.description.length > 160) issues.push(`description is ${m.description.length} characters (about 160 show)`);
    if (!m['og:image']) issues.push('no og:image (shared links show no picture)');
    if (!m['og:title']) issues.push('no og:title');
    if (!m['twitter:card']) issues.push('no twitter:card');
    const icon = H.tags(p.html, 'link').some((l) => /\bicon\b/i.test(l.attrs.rel || ''));
    if (!icon) issues.push('no favicon link');
    if (!p.lang) issues.push('no lang on <html>');
    if (!m.viewport) {
      out.push(assertion({ ...base, id: `seo-meta:${p.label}:viewport`, status: 'fail', severity: 'warn', message: 'No viewport meta tag. Phones will render the desktop layout zoomed out.' }));
    }
    out.push(issues.length
      ? assertion({ ...base, id: `seo-meta:${p.label}`, status: 'warn', severity: 'info', message: issues.join('; ') + '.', actual: { title: p.title, description: m.description || null, 'og:image': m['og:image'] || null } })
      : assertion({ ...base, id: `seo-meta:${p.label}`, status: 'pass', severity: 'info', message: 'Title, description, social tags and favicon present.' }));
  }
  return out;
};
