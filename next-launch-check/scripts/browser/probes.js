'use strict';
// Functions evaluated inside the page. Kept free of closures so Playwright
// can serialise them.

function snapshot() {
  const html = document.documentElement;
  const dl = Array.isArray(window.dataLayer) ? window.dataLayer : [];
  const ndl = Array.isArray(window.NextDataLayer) ? window.NextDataLayer : [];
  const name = (e) => (e && typeof e === 'object' ? e.event || (Array.isArray(e) || e[0] ? String(e[0]) : null) : null);
  const visibleText = (document.body && document.body.innerText) || '';
  return {
    ready: html.hasAttribute('data-next-ready') || html.classList.contains('next-display-ready'),
    readySignal: html.hasAttribute('data-next-ready') ? 'data-next-ready' : html.classList.contains('next-display-ready') ? 'next-display-ready' : null,
    hasNext: typeof window.next !== 'undefined' || typeof window.nextConfig !== 'undefined',
    dataLayer: dl.map(name).filter(Boolean),
    nextDataLayer: ndl.map(name).filter(Boolean),
    // Mobile browsers widen the layout viewport to fit wide content, so
    // compare against the visible width (clientWidth), not innerWidth.
    overflowX: Math.max(document.documentElement.scrollWidth, document.body ? document.body.scrollWidth : 0) - document.documentElement.clientWidth,
    width: document.documentElement.clientWidth,
    badText: (visibleText.match(/\b(NaN|undefined|null)\b|\{\{[^}]*\}\}/g) || []).slice(0, 5),
    url: location.href,
  };
}

function widest() {
  // The elements that stick out past the right edge, for the overflow report.
  const out = [];
  const w = document.documentElement.clientWidth;
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.right > w + 2) {
      const s = getComputedStyle(el);
      if (s.position === 'fixed' || s.visibility === 'hidden' || s.display === 'none') continue;
      // Skip elements inside a clipped or scrolling container (carousels,
      // marquees): they do not make the page scroll sideways.
      let clipped = false;
      for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
        if (/(hidden|clip|auto|scroll)/.test(getComputedStyle(a).overflowX)) { clipped = true; break; }
      }
      if (clipped) continue;
      out.push({ tag: el.tagName.toLowerCase(), cls: (el.className && typeof el.className === 'string' ? el.className : '').slice(0, 60), right: Math.round(r.right) });
      if (out.length > 6) break;
    }
  }
  return out;
}

function visibleSummary() {
  let count = 0;
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) {
      const s = getComputedStyle(el);
      if (s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0') count++;
    }
  }
  return { elements: count, textLength: ((document.body && document.body.innerText) || '').length };
}

function linksTo(paths) {
  const want = new Set(paths);
  const out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    try {
      const u = new URL(a.getAttribute('href'), location.href);
      let p = u.pathname.replace(/\/index\.html?$/i, '/');
      if (p.length > 1 && p.endsWith('/')) p = p.slice(0, -1);
      if (u.origin === location.origin && want.has(p) && p !== location.pathname.replace(/\/index\.html?$/i, '/').replace(/(.)\/$/, '$1')) {
        const r = a.getBoundingClientRect();
        out.push({ href: u.href, path: p, visible: r.width > 0 && r.height > 0 });
      }
    } catch (_) { /* ignore */ }
  }
  return out;
}

function prices() {
  // An amount counts as SDK-rendered when it sits inside an element the SDK
  // owns: any data-next-* attribute (display, bundle, cart summary,
  // discounts, upsell…) or the summary template markers, up to ten levels up.
  // data-next-hide / data-next-show only toggle visibility, so they do not count.
  const re = /(?:[$€£]\s?\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{2})?|\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{2})\s?(?:[$€£]|USD|EUR|GBP|kr|zł))/g;
  const isBoundAttr = (name) => (/^data-next-/.test(name) && !/^data-next-(hide|show|ready|page|funnel)$/.test(name)) || /^data-(summary-lines|os-cart-summary|package-id)$/.test(name);
  const boundOf = (el) => {
    for (let e = el, i = 0; e && e !== document.body && i < 10; e = e.parentElement, i++) {
      for (const a of e.attributes) if (isBoundAttr(a.name)) return `${a.name}=${a.value}`;
    }
    return null;
  };
  const bound = [];
  const free = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const t = n.textContent;
    re.lastIndex = 0;
    if (!re.test(t)) continue;
    re.lastIndex = 0;
    const el = n.parentElement;
    if (!el || el.closest('script,style,noscript,template')) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const key = boundOf(el);
    if (key) bound.push({ key, text: t.trim().slice(0, 40) });
    else free.push((t.match(re) || []).join(' ').slice(0, 60));
    if (bound.length + free.length > 120) break;
  }
  return { bound, free };
}

function formState() {
  const errSel = '[aria-invalid="true"], .next-error, .next-error-field, .next-error-label, [data-next-error], .error:not(:empty), .is-invalid, .invalid-feedback, .field-error, .error-message, .has-error';
  const errors = [...document.querySelectorAll(errSel)].filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
  const invalid = [...document.querySelectorAll('input, select, textarea')].filter((i) => i.willValidate && !i.checkValidity());
  return { visibleErrors: errors.length, invalidFields: invalid.length, sample: errors.slice(0, 5).map((e) => (e.textContent || e.getAttribute('aria-label') || e.name || '').trim().slice(0, 80)), url: location.href };
}

module.exports = { snapshot, widest, visibleSummary, linksTo, prices, formState };
