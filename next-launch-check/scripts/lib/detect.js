'use strict';
// Detectors over one page's source. Each returns plain data; families decide
// what is a pass or a finding.

const H = require('./html');

// Campaign Cart SDK script: matched by the package name in the path, with the
// version taken from "@v0.4.38", "@0.5.0" or "/v0.5.0/".
const SDK_SRC_RE = /campaign[-.]cart/i;
const SDK_VERSION_RE = /(?:@v?|\/v)(\d+\.\d+(?:\.\d+)?(?:-[0-9a-z.]+)?)(?=[/?#]|$)/i;

function sdk(page) {
  const found = page.scripts.filter((s) => s.src && SDK_SRC_RE.test(s.src));
  if (!found.length) return { present: false };
  const src = found[0].src;
  const m = SDK_VERSION_RE.exec(src);
  const version = m ? m[1] : null;
  const pinned = !!(version && /^\d+\.\d+\.\d+/.test(version)) && !/@latest\b/i.test(src);
  return { present: true, src, version, pinned, count: found.length };
}

// Campaign API key: public by design (it ships to every shopper), but always
// reported masked.
const KEY_RES = [
  /["']?apiKey["']?\s*:\s*["']([A-Za-z0-9_-]{8,80})["']/,
  /["']?api_key["']?\s*:\s*["']([A-Za-z0-9_-]{8,80})["']/,
];
function apiKey(page, extraJs = []) {
  const metaKey = page.meta['next-api-key'];
  if (metaKey && metaKey.length >= 8) return { key: metaKey, where: 'meta next-api-key' };
  const bodies = page.scripts.map((s) => s.body).concat(extraJs.map((j) => j.body));
  for (const body of bodies) {
    for (const re of KEY_RES) {
      const m = re.exec(body || '');
      if (m) return { key: m[1], where: 'script config' };
    }
  }
  return null;
}

function pageType(page) {
  const declared = (page.meta['next-page-type'] || '').toLowerCase().trim();
  if (declared) return { type: declared, source: 'meta next-page-type' };
  const h = page.html;
  if (/data-next-checkout-field|data-next-checkout-step|data-next-checkout=/i.test(h)) return { type: 'checkout', source: 'checkout attributes' };
  if (/data-next-upsell-action|data-next-upsell=|data-next-offer/i.test(h) || page.meta['next-upsell-accept-url']) return { type: 'upsell', source: 'upsell attributes' };
  if (/data-next-order-items|data-next-order=/i.test(h) || /\/(receipt|thank-?you|confirmation)(\/|\.html?|$)/i.test(new URL(page.url).pathname)) return { type: 'receipt', source: 'receipt attributes or path' };
  if (/\/(checkout)(\/|\.html?|$)/i.test(new URL(page.url).pathname)) return { type: 'checkout', source: 'path' };
  return { type: 'content', source: 'no commerce markers (presell, landing or other content page)' };
}

// Funnel route tags. next-success-url is the checkout's next page;
// next-next-url is an older alias some templates still carry.
function routes(page) {
  const m = page.meta;
  return {
    success: m['next-success-url'] || m['next-next-url'] || null,
    successTag: m['next-success-url'] ? 'next-success-url' : m['next-next-url'] ? 'next-next-url' : null,
    accept: m['next-upsell-accept-url'] || null,
    decline: m['next-upsell-decline-url'] || null,
    funnel: m['next-funnel'] || null,
  };
}

function uniq(list) { return [...new Set(list)]; }

function tracking(page) {
  const h = page.html;
  const gtm = uniq((h.match(/\bGTM-[A-Z0-9]{4,10}\b/g) || []));
  const ga4 = uniq([
    ...[...h.matchAll(/gtag\/js\?id=(G-[A-Z0-9]{6,14})/g)].map((m) => m[1]),
    ...[...h.matchAll(/config['"]\s*,\s*['"](G-[A-Z0-9]{6,14})['"]/g)].map((m) => m[1]),
    ...[...h.matchAll(/(?:measurement_?id|ga4_?id)['"]?\s*[:=]\s*['"](G-[A-Z0-9]{6,14})['"]/gi)].map((m) => m[1]),
  ]);
  const meta = uniq([
    ...[...h.matchAll(/fbq\(\s*['"]init['"]\s*,\s*['"](\d{10,20})['"]/g)].map((m) => m[1]),
    ...(page.meta['facebook-pixel-id'] ? [page.meta['facebook-pixel-id']] : []),
  ]);
  const tiktok = uniq([...h.matchAll(/ttq\.load\(\s*['"]([A-Z0-9]{10,30})['"]/g)].map((m) => m[1]));
  const everflowScript = page.scripts.some((s) => /everflow|\/scripts\/main\.js\?.*ef/i.test(s.src)) || /\bEF\.(click|conversion)\s*\(/.test(h);
  const everflowOffers = uniq([...h.matchAll(/EF\.click\(\s*\{[\s\S]{0,400}?offer_id\s*:\s*['"]?(\d+)/g)].map((m) => m[1]));
  return { gtm, ga4, meta, tiktok, everflow: { present: everflowScript, offers: everflowOffers } };
}

// Pages the SDK runs commerce on. Anything else (presell, landing, the 0.4
// `product` type, content pages) is crawled for calls to action.
const COMMERCE = new Set(['checkout', 'upsell', 'receipt']);
function isCommerce(page) { return COMMERCE.has(page.kind && page.kind.type); }

function hasViewport(page) { return !!page.meta.viewport; }

module.exports = { sdk, apiKey, pageType, routes, tracking, hasViewport, isCommerce, COMMERCE, SDK_SRC_RE, SDK_VERSION_RE };
