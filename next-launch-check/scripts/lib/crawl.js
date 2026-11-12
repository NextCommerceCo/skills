'use strict';
// Find the funnel from its entry URL. Order of discovery:
//  1. pages the tester named, in the order given;
//  2. the route tags (next-success-url, accept and decline URLs);
//  3. on content pages without route tags, a few call-to-action links that
//     stay on the same site.
// Only GET requests are made, so nothing on the funnel runs or fires.

const P = require('./page');
const { resolve, sameOrigin, pathKey } = require('./util');
const { isCommerce } = require('./detect');

const LEGAL_RE = /(terms|privacy|refund|return|shipping|contact|faq|policy|legal|disclaimer|cookie|accessib|imprint|about)/i;
const ASSET_RE = /\.(png|jpe?g|gif|webp|svg|ico|pdf|zip|mp4|webm|mov|css|js|json|xml|txt)(\?|#|$)/i;
const CTA_CLASS_RE = /\b(btn|button|cta|order|buy|checkout|claim|shop)/i;
const CTA_HREF_RE = /(checkout|order|buy|offer|landing|cart|claim|select|special|deal)/i;

function ctaLinks(page, limit = 3) {
  const out = [];
  const seen = new Set();
  for (const a of page.anchors) {
    const abs = resolve(a.href, page.finalUrl);
    if (!abs || !sameOrigin(abs, page.finalUrl)) continue;
    if (pathKey(abs) === pathKey(page.finalUrl)) continue;
    if (ASSET_RE.test(abs) || LEGAL_RE.test(new URL(abs).pathname)) continue;
    if (!(CTA_CLASS_RE.test(a.cls) || CTA_HREF_RE.test(new URL(abs).pathname) || /data-next-action/i.test(JSON.stringify(a.attrs)))) continue;
    const key = pathKey(abs);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(abs);
    if (out.length >= limit) break;
  }
  return out;
}

async function crawl(entryUrl, { pages = [], maxPages = 12, follow = true, load = P.load } = {}) {
  const queue = [{ url: entryUrl, via: 'entry' }, ...pages.map((u) => ({ url: u, via: 'named' }))];
  const seen = new Map();
  const ordered = [];
  const edges = [];
  while (queue.length && ordered.length < maxPages) {
    const { url, via, from } = queue.shift();
    const key = pathKey(url);
    if (seen.has(key)) { if (from) edges.push({ from, to: seen.get(key).url, via }); continue; }
    const page = await load(url);
    page.via = via;
    seen.set(key, page);
    ordered.push(page);
    if (from) edges.push({ from, to: page.url, via });
    if (!follow || !page.ok) continue;
    const next = [];
    const r = page.routes;
    for (const [tag, href] of [[r.successTag, r.success], ['next-upsell-accept-url', r.accept], ['next-upsell-decline-url', r.decline]]) {
      const abs = resolve(href, page.finalUrl);
      if (abs) next.push({ url: abs, via: tag, from: page.url });
    }
    if (!next.length && !isCommerce(page)) {
      for (const u of ctaLinks(page)) next.push({ url: u, via: 'cta link', from: page.url });
    }
    queue.push(...next);
  }
  return { pages: ordered, edges, truncated: queue.length > 0 };
}

module.exports = { crawl, ctaLinks, LEGAL_RE, ASSET_RE };
