'use strict';
const { assertion, resolve, sameOrigin } = require('../util');
const H = require('../html');

// Header and footer links on the storefront home page, and whether each one
// loaded during the crawl.
module.exports = async function storefrontNav(ctx) {
  const home = ctx.pages[0];
  if (!home || !home.ok) return [];
  const loaded = new Map(ctx.pages.map((p) => [p.url, p]));
  const out = [];
  for (const region of ['header', 'nav', 'footer']) {
    const links = home.anchors.filter((a) => H.regionAt(home.html, a.index) === region).map((a) => ({ text: a.text || '[icon]', url: resolve(a.href, home.finalUrl) })).filter((l) => l.url && sameOrigin(l.url, home.finalUrl));
    if (!links.length) continue;
    const bad = links.filter((l) => loaded.has(l.url) && !loaded.get(l.url).ok);
    out.push(assertion({ id: `storefront-nav:${region}`, family: 'storefront-nav', page: '/', url: home.url, status: bad.length ? 'fail' : 'pass', severity: 'warn', message: bad.length ? `${bad.length} ${region} link(s) do not load: ${bad.map((b) => b.text).join(', ')}.` : `${links.length} ${region} link(s) found; the ones crawled all load. Check each goes to the right page (S-01).`, evidence: links.slice(0, 25).map((l) => `${l.text} → ${new URL(l.url).pathname}`) }));
  }
  return out;
};
