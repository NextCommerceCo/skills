'use strict';
const { assertion, resolve } = require('../util');
const net = require('../net');
const H = require('../html');

const CDN_HINTS = ['cloudfront.net', 'cloudinary.com', 'imgix.net', 'fastly', 'akamai', 'imagekit.io', 'bunnycdn', 'b-cdn.net', 'imagedelivery.net', 'cdn.', '.cdn', 'amazonaws.com', 'azureedge.net', 'googleusercontent.com', 'shopify.com', 'jsdelivr.net', 'unpkg.com', 'netlify.app', 'vercel.app', 'pages.dev', 'twimg.com', 'vimeocdn.com', 'ytimg.com', '29next.store'];
const HEAVY = 500 * 1024;
const LARGE = 300 * 1024;

function mediaUrls(p) {
  const urls = new Map();
  const add = (kind, raw) => {
    // Skip unrendered template tokens such as {item.image}: the SDK fills
    // them in the browser.
    if (!raw || /[{}]|%7B|%7D/i.test(raw)) return;
    const u = resolve(raw, p.finalUrl);
    if (u && !urls.has(u)) urls.set(u, kind);
  };
  for (const img of H.tags(p.html, 'img')) {
    for (const k of ['src', 'data-src', 'data-lazy-src']) add('image', img.attrs[k]);
    for (const part of (img.attrs.srcset || '').split(',')) add('image', part.trim().split(/\s+/)[0]);
  }
  for (const s of H.tags(p.html, 'source')) { add(/video/i.test(s.attrs.type || '') ? 'video' : 'image', s.attrs.src); for (const part of (s.attrs.srcset || '').split(',')) add('image', part.trim().split(/\s+/)[0]); }
  for (const v of H.tags(p.html, 'video')) { add('video', v.attrs.src); add('image', v.attrs.poster); }
  for (const m of p.html.matchAll(/background(?:-image)?\s*:\s*url\(\s*['"]?([^'")\s]+)['"]?\s*\)/gi)) add('image', m[1]);
  return [...urls].map(([url, kind]) => ({ url, kind }));
}

function onCdn(url) {
  try { const h = new URL(url).hostname.toLowerCase(); return CDN_HINTS.some((c) => h.includes(c)); } catch (_) { return false; }
}

module.exports = async function media(ctx) {
  const out = [];
  const all = new Map();
  for (const p of ctx.pages.filter((x) => x.ok)) for (const m of mediaUrls(p)) if (!all.has(m.url)) all.set(m.url, { ...m, page: p.label });
  const list = [...all.values()].slice(0, ctx.options.maxMedia || 120);
  if (!list.length) return [assertion({ id: 'media-weight', family: 'media-weight', status: 'skipped', severity: 'info', message: 'No images or video in the page source.' })];
  const sized = await net.pool(list, 8, async (m) => ({ ...m, ...(await net.size(m.url)) }));
  const heavy = sized.filter((m) => m.bytes && m.bytes > HEAVY);
  const offCdn = sized.filter((m) => m.bytes && m.bytes > LARGE && !onCdn(m.url) && !heavy.includes(m));
  const broken = sized.filter((m) => m.status >= 400);
  const kb = (b) => `${Math.round(b / 1024)} KB`;
  out.push(heavy.length
    ? assertion({ id: 'media-weight:heavy', family: 'media-weight', status: 'warn', severity: 'warn', message: `${heavy.length} file(s) over 500 KB slow the page on mobile.`, evidence: heavy.map((m) => ({ url: m.url, size: kb(m.bytes), page: m.page, cdn: onCdn(m.url) })) })
    : assertion({ id: 'media-weight:heavy', family: 'media-weight', status: 'pass', severity: 'warn', message: `${sized.length} media file(s) checked; none over 500 KB.` }));
  if (offCdn.length) out.push(assertion({ id: 'media-weight:cdn', family: 'media-weight', status: 'warn', severity: 'info', message: `${offCdn.length} file(s) over 300 KB are not on a known CDN host.`, evidence: offCdn.map((m) => ({ url: m.url, size: kb(m.bytes), page: m.page })) }));
  if (broken.length) out.push(assertion({ id: 'media-weight:broken', family: 'media-weight', status: 'fail', severity: 'warn', message: `${broken.length} image or video file(s) do not load.`, evidence: broken.map((m) => ({ url: m.url, status: m.status, page: m.page })) }));
  return out;
};

module.exports.mediaUrls = mediaUrls;
module.exports.onCdn = onCdn;
