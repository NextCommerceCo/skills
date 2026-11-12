'use strict';
const { assertion, resolve } = require('../util');
const H = require('../html');

// A live page should not send shoppers to the platform's default store
// address. The payments host is expected and is not flagged.
const STORE_HOST = /(^|\.)[a-z0-9-]+\.29next\.store$/i;

module.exports = async function storefrontDomain(ctx) {
  const out = [];
  for (const p of ctx.pages.filter((x) => x.ok)) {
    let host = '';
    try { host = new URL(p.finalUrl).hostname; } catch (_) { /* ignore */ }
    if (STORE_HOST.test(host)) continue;
    const found = new Set();
    const consider = (raw, where) => {
      const u = resolve(raw, p.finalUrl);
      if (!u) return;
      try { if (STORE_HOST.test(new URL(u).hostname)) found.add(`${where}: ${u}`); } catch (_) { /* ignore */ }
    };
    for (const a of p.anchors) consider(a.href, 'link');
    for (const f of H.tags(p.html, 'form')) consider(f.attrs.action, 'form action');
    for (const l of H.tags(p.html, 'link')) if (/canonical/i.test(l.attrs.rel || '')) consider(l.attrs.href, 'canonical');
    consider(p.meta['og:url'], 'og:url');
    out.push(found.size
      ? assertion({ id: `storefront-domain:${p.label}`, family: 'storefront-domain', page: p.label, url: p.url, status: 'warn', severity: 'warn', message: 'Links point at the default platform store address instead of your own domain.', evidence: [...found].slice(0, 10) })
      : assertion({ id: `storefront-domain:${p.label}`, family: 'storefront-domain', page: p.label, url: p.url, status: 'pass', severity: 'warn', message: 'No links to the default platform store address.' }));
  }
  return out;
};
