'use strict';
const { assertion } = require('../util');
const H = require('../html');
const net = require('../net');
const { resolve } = require('../util');

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
    const iconTag = H.tags(p.html, 'link').find((l) => /\bicon\b/i.test(l.attrs.rel || '') && !/apple/i.test(l.attrs.rel || ''));
    const iconUrl = iconTag ? resolve(iconTag.attrs.href || '', p.finalUrl) : resolve('/favicon.ico', p.finalUrl);
    const iconStatus = iconUrl ? await net.status(iconUrl) : { ok: false };
    out.push(iconStatus.ok
      ? assertion({ family: 'favicon', page: p.label, url: p.url, id: `favicon:${p.label}`, status: 'pass', severity: 'warn', message: `Favicon loads (${iconUrl}).`, evidence: iconUrl })
      : assertion({ family: 'favicon', page: p.label, url: p.url, id: `favicon:${p.label}`, status: 'fail', severity: 'warn', message: iconTag ? `The favicon link points at ${iconUrl}, which does not load (HTTP ${iconStatus.status || 'error'}).` : 'No favicon: no <link rel="icon"> and no /favicon.ico. The browser tab shows a blank icon.' }));
    if (!p.lang) issues.push('no lang on <html>');
    if (!m.viewport) {
      out.push(assertion({ ...base, id: `seo-meta:${p.label}:viewport`, status: 'fail', severity: 'warn', message: 'No viewport meta tag. Phones will render the desktop layout zoomed out.' }));
    } else {
      // Older shoppers zoom to read; a viewport that forbids it fails them.
      const v = m.viewport.toLowerCase().replace(/\s+/g, '');
      const locked = /user-scalable=(no|0)/.test(v) || /maximum-scale=(1(\.0+)?|0?\.\d+)(,|$)/.test(v);
      out.push(locked
        ? assertion({ family: 'responsive', page: p.label, url: p.url, id: `responsive:${p.label}:pinch-zoom`, status: 'fail', severity: 'warn', message: `Pinch-zoom is disabled on phones (viewport "${m.viewport}"). Shoppers cannot zoom in to read; remove user-scalable=no and maximum-scale=1.`, actual: m.viewport })
        : assertion({ family: 'responsive', page: p.label, url: p.url, id: `responsive:${p.label}:pinch-zoom`, status: 'pass', severity: 'warn', message: 'Pinch-zoom is allowed on phones.' }));
    }
    out.push(issues.length
      ? assertion({ ...base, id: `seo-meta:${p.label}`, status: 'warn', severity: 'info', message: issues.join('; ') + '.', actual: { title: p.title, description: m.description || null, 'og:image': m['og:image'] || null } })
      : assertion({ ...base, id: `seo-meta:${p.label}`, status: 'pass', severity: 'info', message: 'Title, description, social tags and favicon present.' }));
  }
  return out;
};
