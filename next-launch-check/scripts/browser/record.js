'use strict';
// "You drive, I record": opens a real browser window on the checkout. The
// tester places one order with the public test card. The script watches and
// records the order call, upsell calls, dataLayer events, the parameters on
// each page, and the receipt. It does not type, click or submit anything.
const fs = require('fs');
const path = require('path');
const { assertion, pageLabel } = require('../lib/util');
const PW = require('./playwright');

const ORDER_CREATE = /\/api\/v1\/orders\/?(\?|$)/i;
const UPSELL = /\/api\/v1\/orders\/[^/]+\/upsells\/?|\/upsells\/?(\?|$)/i;
const SENSITIVE = /(card|cvv|cvc|token|password|secret|number|phone|email|address|first_name|last_name|postcode|zip)/i;

function redact(value, key = '') {
  if (value === null || value === undefined) return value;
  if (Array.isArray(value)) return value.map((v) => redact(v, key));
  if (typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, redact(v, k)]));
  if (SENSITIVE.test(key) && typeof value === 'string' && value.length) return '•••';
  return value;
}

function summarizeOrderBody(body) {
  if (!body || typeof body !== 'object') return null;
  const pick = (o, keys) => Object.fromEntries(keys.filter((k) => o && o[k] !== undefined).map((k) => [k, o[k]]));
  return {
    lines: (body.lines || []).map((l) => pick(l, ['package_id', 'quantity', 'is_upsell'])),
    shipping_method: body.shipping_method,
    vouchers: body.vouchers,
    payment_method: body.payment_detail && body.payment_detail.payment_method,
    attribution: body.attribution,
    success_url: body.success_url,
    payment_failed_url: body.payment_failed_url,
    language: body.language || (body.user && body.user.language) || null,
    currency: body.currency || null,
  };
}

const AFFILIATE = /^(affid|aff_id|affiliate_id|sub[1-9]|sub10|oid|offer_id|transaction_id|_ef_transaction_id|clickid|click_id|cid|s[1-5])$/i;

function affiliateParams(url) {
  const u = new URL(url);
  return [...u.searchParams.entries()].filter(([k]) => AFFILIATE.test(k));
}

// Refuse unless the affiliate parameters are confirmed test values.
function paramGate(url, { confirmed = false, allowed = null } = {}) {
  const found = affiliateParams(url);
  if (!found.length) return { ok: true, found };
  if (allowed) {
    const bad = found.filter(([k, v]) => !(allowed[k] || []).map(String).includes(v));
    if (bad.length) return { ok: false, found, reason: `Affiliate parameters not allowed by the rules file: ${bad.map(([k, v]) => `${k}=${v}`).join(', ')}.` };
    return { ok: true, found };
  }
  if (!confirmed) return { ok: false, found, reason: `The URL carries affiliate parameters (${found.map(([k, v]) => `${k}=${v}`).join(', ')}). A test order through a live affiliate value can record a real conversion and a payout. Re-run with --confirm-test-params only if these are the agreed test values.` };
  return { ok: true, found };
}

// Shared by the recorder and the automated order: capture dataLayer events,
// order and upsell calls (redacted), and every page the shopper lands on.
async function instrument(context) {
  const events = [];
  const pages = [];
  const calls = [];
  await context.exposeBinding('__nlcEvent', (_src, e) => { events.push(e); });
  await context.addInitScript(() => {
    const wrap = (name) => {
      const arr = (window[name] = window[name] || []);
      const push = arr.push.bind(arr);
      arr.push = function (...items) {
        for (const it of items) {
          try {
            const ev = it && typeof it === 'object' ? (it.event || (it[0] ? String(it[0]) + (it[1] ? ':' + String(it[1]) : '') : null)) : null;
            if (ev && !/^gtm\./.test(ev)) window.__nlcEvent({ layer: name, event: ev, page: location.pathname, value: it.ecommerce ? it.ecommerce.value || (it.ecommerce.purchase && it.ecommerce.purchase.actionField && it.ecommerce.purchase.actionField.revenue) : undefined, currency: it.ecommerce ? it.ecommerce.currency : undefined });
          } catch (_) { /* ignore */ }
        }
        return push(...items);
      };
    };
    wrap('dataLayer');
    wrap('NextDataLayer');
  });
  context.on('request', (req) => {
    const url = req.url();
    if (req.method() === 'POST' && (ORDER_CREATE.test(url) || UPSELL.test(url))) {
      let body = null;
      try { body = JSON.parse(req.postData() || 'null'); } catch (_) { body = null; }
      calls.push({ kind: ORDER_CREATE.test(url) ? 'order' : 'upsell', url: url.split('?')[0], body: summarizeOrderBody(redact(body)), at: new Date().toISOString(), req });
    }
  });
  context.on('response', async (res) => {
    const c = calls.find((x) => x.req === res.request());
    if (!c) return;
    c.status = res.status();
    try { const j = await res.json(); c.ref = j && (j.ref_id || j.number || j.id) ? { ref_id: j.ref_id, number: j.number } : null; } catch (_) { c.ref = null; }
  });
  return { events, pages, calls };
}

function watchPages(tab, pages) {
  tab.on('framenavigated', (f) => { if (f === tab.mainFrame()) pages.push({ url: f.url(), at: new Date().toISOString() }); });
}

function toRecord(entry, { pages, calls, events, beacons, receiptReached }) {
  return {
    entry,
    pages: pages.filter((p) => /^https?:/.test(p.url)).map((p) => ({ ...p, label: pageLabel(p.url), params: Object.fromEntries(new URL(p.url).searchParams) })),
    calls: calls.map(({ req, ...c }) => c),
    events,
    beacons,
    receiptReached,
  };
}

async function record(ctx) {
  const loaded = PW.load();
  if (!loaded) throw new Error('Playwright is not installed; the recorder needs it.');
  const { pw } = loaded;
  const headless = !!ctx.options.headless;
  const browser = await pw.chromium.launch({ headless });
  const log = PW.newLog();
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await PW.guard(context, { liveBeacons: ctx.options.liveBeacons, log, blockOrders: false });
  const { events, pages, calls } = await instrument(context);
  const tab = await context.newPage();
  watchPages(tab, pages);
  await tab.goto(ctx.entryUrl, { waitUntil: 'load', timeout: 60000 });
  if (ctx.options.autodrive) await tab.evaluate(ctx.options.autodrive).catch(() => {});
  const deadline = Date.now() + (ctx.options.recordMinutes || 20) * 60000;
  let receiptAt = null;
  while (Date.now() < deadline) {
    if (tab.isClosed()) break;
    const url = tab.url();
    const atReceipt = await tab.evaluate(() => !!document.querySelector('[data-next-order-items], [data-next-order], meta[name="next-page-type"][content="receipt"]') || /\/(receipt|thank-?you|confirmation)(\/|\.html?|$)/i.test(location.pathname)).catch(() => false);
    if (atReceipt && !receiptAt) receiptAt = Date.now();
    if (receiptAt && Date.now() - receiptAt > 6000) break;
    await new Promise((r) => setTimeout(r, 1000));
    void url;
  }
  if (!tab.isClosed()) await require('./funnel').capture(tab, path.join(ctx.outDir, 'screenshots', 'order-last-page.jpg'), 55).catch(() => {});
  await browser.close();
  const rec = toRecord(ctx.entryUrl, { pages, calls, events, beacons: log.beacons.length, receiptReached: !!receiptAt });
  fs.writeFileSync(path.join(ctx.outDir, 'order-record.json'), JSON.stringify(rec, null, 2) + '\n');
  return { record: rec, assertions: judge(rec, 'recorded') };
}

function judge(rec, label = 'recorded') {
  const out = [];
  const order = rec.calls.find((c) => c.kind === 'order');
  if (!order) {
    out.push(assertion({ id: `order:created:${label}`, family: 'order', status: 'fail', severity: 'blocker', message: 'No order call was seen. Either no order was placed in the window, or the checkout sends it some other way.' }));
    return out;
  }
  const okOrder = order.status >= 200 && order.status < 300;
  out.push(assertion({ id: `order:created:${label}`, family: 'order', status: okOrder ? 'pass' : 'fail', severity: 'blocker', message: okOrder ? `Order created${order.ref && order.ref.number ? ` (number ${order.ref.number})` : ''}. Check it in the store's orders page against F-10 and F-16.` : `Order call answered HTTP ${order.status}.`, evidence: order.body }));
  const attribution = (order.body && order.body.attribution) || {};
  const firstParams = (rec.pages[0] && rec.pages[0].params) || {};
  const tracked = Object.keys(firstParams).filter((k) => /^utm_|^(affid|sub\d+|oid|offer_id|gclid|fbclid)$/i.test(k));
  if (tracked.length) {
    const flat = JSON.stringify(attribution);
    const lost = tracked.filter((k) => !flat.includes(k) && !flat.includes(String(firstParams[k])));
    out.push(assertion({ id: `order:attribution:${label}`, family: 'order', status: lost.length ? 'warn' : 'pass', severity: 'warn', message: lost.length ? `Parameters on the entry URL are not on the order's attribution: ${lost.join(', ')}.` : 'Entry URL parameters reached the order attribution.', evidence: attribution }));
  }
  const upsells = rec.calls.filter((c) => c.kind === 'upsell');
  if (upsells.length) out.push(assertion({ id: `order:upsells:${label}`, family: 'order', status: upsells.every((u) => u.status >= 200 && u.status < 300) ? 'pass' : 'fail', severity: 'blocker', message: `${upsells.length} upsell call(s): ${upsells.map((u) => u.status).join(', ')}.` }));
  // A purchase event per name: a funnel may send the SDK's dl_purchase and a
  // separate GA4 purchase on purpose. Only the same name twice is a duplicate.
  const purchase = rec.events.filter((e) => /(^|_)purchase$|checkout_completed/i.test(e.event) && !/upsell/i.test(e.event));
  const byName = {};
  for (const e of purchase) (byName[`${e.layer}:${e.event}`] = byName[`${e.layer}:${e.event}`] || []).push(e);
  const dupes = Object.entries(byName).filter(([, list]) => list.length > 1);
  const names = Object.keys(byName).map((k) => k.split(':')[1]);
  out.push(!purchase.length
    ? assertion({ id: `order:purchase-event:${label}`, family: 'order', status: 'fail', severity: 'warn', message: 'No purchase event reached the dataLayer.' })
    : dupes.length
      ? assertion({ id: `order:purchase-event:${label}`, family: 'order', status: 'warn', severity: 'warn', message: `The same purchase event fired more than once for one order: ${dupes.map(([k, l]) => `${k.split(':')[1]} ×${l.length} (${[...new Set(l.map((e) => e.page))].join(', ')})`).join('; ')}. Revenue may count twice.`, evidence: purchase })
      : assertion({ id: `order:purchase-event:${label}`, family: 'order', status: 'pass', severity: 'warn', message: `Purchase sent once per event name: ${names.join(', ')} (${purchase.map((e) => `${e.event} on ${e.page}${e.value ? ` ${e.value} ${e.currency || ''}` : ''}`).join('; ')}).` }));
  const dropped = rec.pages.slice(1).filter((p) => tracked.some((k) => !(k in p.params)) && !/\.(29next|nextcommerce)\./.test(p.url)).map((p) => p.label);
  if (tracked.length) out.push(assertion({ id: `order:params-per-page:${label}`, family: 'order', status: dropped.length ? 'warn' : 'pass', severity: 'info', message: dropped.length ? `Pages after checkout without the entry parameters in the URL: ${dropped.join(', ')}. Fine if attribution already reached the order.` : 'Entry parameters stayed in the URL on every page.' }));
  out.push(assertion({ id: `order:receipt:${label}`, family: 'order', status: rec.receiptReached ? 'pass' : 'fail', severity: 'blocker', message: rec.receiptReached ? 'Receipt page reached.' : 'The receipt page was not reached in the recording window.' }));
  return out;
}

module.exports = { record, judge, paramGate, affiliateParams, redact, summarizeOrderBody, instrument, watchPages, toRecord };
