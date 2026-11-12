'use strict';
const { assertion, resolve, sameOrigin } = require('../util');
const net = require('../net');
const H = require('../html');

const PLACEHOLDER_HREF = /^(#|javascript:\s*void|javascript:;?$)|example\.(com|org|net)|placeholder|lorem|localhost|127\.0\.0\.1/i;

module.exports = async function links(ctx) {
  const out = [];
  const pages = ctx.pages.filter((p) => p.ok);
  const internal = new Map();
  for (const p of pages) {
    const placeholders = [];
    const previewLinks = [];
    for (const a of p.anchors) {
      const href = (a.href || '').trim();
      const isAction = Object.keys(a.attrs).some((k) => k.startsWith('data-next-') || k.startsWith('on') || k === 'data-toggle' || k === 'data-bs-toggle' || k === 'aria-controls');
      if (!href && !isAction) placeholders.push({ label: a.text || '[image link]', href: '(none)' });
      else if (href && PLACEHOLDER_HREF.test(href) && !isAction && !(href.startsWith('#') && href.length > 1)) placeholders.push({ label: a.text || '[image link]', href });
      const abs = resolve(href, p.finalUrl);
      if (!abs) continue;
      if (/deploy-preview-\d+--|\.netlify\.app|\.vercel\.app|\.pages\.dev/i.test(abs) && !/\.netlify\.app|\.vercel\.app|\.pages\.dev/i.test(p.finalUrl)) previewLinks.push(abs);
      if (sameOrigin(abs, p.finalUrl) && !/\.(png|jpe?g|gif|webp|svg|mp4|pdf)(\?|$)/i.test(abs)) {
        const key = abs.split('#')[0];
        if (!internal.has(key)) internal.set(key, p.label);
      }
    }
    const base = { family: 'links', page: p.label, url: p.url };
    out.push(placeholders.length
      ? assertion({ ...base, id: `links:${p.label}:placeholder`, status: 'fail', severity: 'warn', message: `${placeholders.length} link(s) go nowhere or to a placeholder.`, evidence: placeholders.slice(0, 15) })
      : assertion({ ...base, id: `links:${p.label}:placeholder`, status: 'pass', severity: 'warn', message: 'No placeholder links.' }));
    if (previewLinks.length) out.push(assertion({ ...base, id: `links:${p.label}:preview-host`, status: 'fail', severity: 'warn', message: 'Links point at a preview deployment from a non-preview page.', evidence: [...new Set(previewLinks)].slice(0, 10) }));
    // Buttons outside forms with no SDK attribute, id or handler: likely dead.
    const buttons = H.tags(p.html, 'button', { inner: true }).filter((b) => {
      const keys = Object.keys(b.attrs);
      if (keys.some((k) => k.startsWith('data-') || k.startsWith('on') || k.startsWith('aria-') || k === 'id' || k === 'form')) return false;
      if ((b.attrs.type || '').toLowerCase() === 'submit') return false;
      const before = p.html.slice(0, b.index).toLowerCase();
      return before.lastIndexOf('<form') <= before.lastIndexOf('</form');
    });
    if (buttons.length) {
      out.push(assertion({ ...base, id: `links:${p.label}:buttons`, status: 'manual_review', severity: 'info', message: `${buttons.length} button(s) have no form, id, data attribute or handler in the source. Click them and confirm they do something.`, evidence: buttons.slice(0, 10).map((b) => H.text(b.inner).slice(0, 80) || '[icon button]') }));
    }
  }
  const targets = [...internal.keys()].filter((u) => !pages.some((p) => p.finalUrl.split('#')[0] === u)).slice(0, ctx.options.maxLinks || 60);
  const results = await net.pool(targets, 6, async (u) => ({ u, r: await net.status(u) }));
  const dead = results.filter((x) => !x.r.ok);
  out.push(dead.length
    ? assertion({ id: 'links:dead', family: 'links', status: 'fail', severity: 'warn', message: `${dead.length} of ${targets.length} same-site link(s) do not load.`, evidence: dead.map((d) => ({ url: d.u, status: d.r.status || 'error', from: internal.get(d.u) })) })
    : assertion({ id: 'links:dead', family: 'links', status: 'pass', severity: 'warn', message: `${targets.length} same-site link(s) checked; all load.` }));
  return out;
};
