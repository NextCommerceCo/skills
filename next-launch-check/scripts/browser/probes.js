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

// Elements each content toggle is meant to hide, found by common markup and
// text. Returns how many exist and how many are visible.
function toggleTargets(name) {
  // cls: class/id/data markers. text: text of a small element (under 80
  // characters). must: text the element has to contain to count. not:
  // markers or text that rule an element out.
  const PAT = {
    timer: { cls: /countdown|timer|clock/i, text: /\b(expires?|ends?|left|hurry)\b.{0,30}\d{1,2}\s*:\s*\d{2}|^\s*\d{1,2}\s*:\s*\d{2}(\s*:\s*\d{2})?\s*$/i },
    reviews: { cls: /review|testimonial|star-?rating|trustpilot|ugc/i, text: /verified (buyer|customer|purchase)/i },
    exit: { cls: /exit[-_ ]?(pop|intent|modal|offer)|exitpop|exit-popup/i },
    banner: { cls: /banner|announcement|promo[-_ ]?(bar|sale|strip)|sale[-_ ]?(bar|strip)|top[-_ ]?bar|sticky[-_ ]?bar|notice[-_ ]?bar/i, notCls: /applied|coupon|voucher|summary|cart|order[-_ ]?total/i, must: /%|\boff\b|free|promo|code|sale|deal|limited|today|save|discount/i, not: /secure|encrypted|ssl|guarantee|trusted/i },
    seen: { cls: /as[-_ ]?seen|seen[-_ ]?on|featured[-_ ]?in|press[-_ ]?logos|media[-_ ]?logos/i, text: /\bas seen (on|in)\b|\bfeatured (on|in)\b/i },
    loading: { cls: /fake[-_ ]?load|loading[-_ ]?bar|status[-_ ]?bar|progress[-_ ]?bar|stock[-_ ]?bar|scarcity/i, notCls: /checkout[-_ ]?progress|step|swiper|submit|button|spinner|next-loading/i, must: /%|\bleft\b|sold|stock|loading|processing|remaining|claimed|reserved/i, not: /secure|encrypted|ssl|guarantee/i },
  }[name];
  if (!PAT) return { present: 0, visible: 0, sample: [] };
  const isVisible = (el) => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el); return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0'; };
  const hits = [];
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('script,style,noscript,template,button')) continue;
    if (hits.some((h) => h.contains(el))) continue;
    const markers = `${typeof el.className === 'string' ? el.className : ''} ${el.id || ''} ${[...el.attributes].filter((a) => a.name.startsWith('data-')).map((a) => `${a.name}=${a.value}`).join(' ')}`;
    const text = (el.textContent || '').replace(/\s+/g, ' ').trim();
    let hit = PAT.cls.test(markers);
    if (!hit && PAT.text && text.length > 0 && text.length < 80) hit = PAT.text.test(text);
    if (hit && PAT.notCls && PAT.notCls.test(markers)) hit = false;
    if (hit && PAT.must && !PAT.must.test(text)) hit = false;
    if (hit && PAT.not && PAT.not.test(text)) hit = false;
    if (hit) hits.push(el);
    if (hits.length > 40) break;
  }
  return { present: hits.length, visible: hits.filter(isVisible).length, sample: hits.slice(0, 3).map((el) => `${el.tagName.toLowerCase()}.${(typeof el.className === 'string' ? el.className : '').split(/\s+/).filter(Boolean).slice(0, 2).join('.')}`) };
}

// Paid add-ons that are already selected when the page loads (shipping
// protection, warranties, priority handling…).
function preselectedAddons() {
  const ADDON = /protect|insur|warrant|guarantee|priority|express|rush|vip|club|membership|subscription|bump|upgrade|add-?on/i;
  const out = [];
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const label = (el) => {
    const box = el.closest('label, [data-next-bump], [data-next-upsell], .bump, .order-bump, .addon, .add-on, li, .form-check, .checkbox, div');
    return ((box && box.textContent) || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 120);
  };
  for (const el of document.querySelectorAll('input[type="checkbox"], [role="switch"], [role="checkbox"]')) {
    const on = el.matches('input') ? el.checked : el.getAttribute('aria-checked') === 'true';
    if (!on) continue;
    const text = label(el);
    if (!ADDON.test(text) && !ADDON.test(`${el.name || ''} ${el.id || ''} ${el.className || ''}`)) continue;
    if (/same as|billing|newsletter|remember|terms|agree/i.test(text)) continue;
    if (!visible(el) && !visible(el.closest('label') || el)) continue;
    out.push({ text, price: (text.match(/[$€£]\s?\d+(?:[.,]\d{2})?|\d+(?:[.,]\d{2})?\s?(?:[$€£]|USD|EUR|GBP)/) || [null])[0] });
  }
  // SDK order bumps that are in the cart before any click.
  for (const el of document.querySelectorAll('[data-next-bump][data-next-in-cart="true"], [data-next-bump].next-in-cart, [data-next-toggle][data-next-in-cart="true"]')) {
    if (visible(el)) out.push({ text: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120), price: null });
  }
  return out.slice(0, 8);
}

module.exports = { snapshot, toggleTargets, preselectedAddons, widest, visibleSummary, linksTo, prices, formState };
