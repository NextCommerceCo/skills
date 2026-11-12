'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const fs = require('fs');
const os = require('os');
const S = path.join(__dirname, '..', '..', 'scripts');
const H = require(path.join(S, 'lib/html'));
const D = require(path.join(S, 'lib/detect'));
const P = require(path.join(S, 'lib/page'));
const R = require(path.join(S, 'lib/rules'));
const U = require(path.join(S, 'lib/util'));
const report = require(path.join(S, 'lib/report'));
const pr = require(path.join(S, 'lib/checks/pr-review'));
const F = require(path.join(S, 'lib/figma'));
const rec = require(path.join(S, 'browser/record'));

const page = (html, url = 'https://shop.example.com/checkout/') => P.build(url, { status: 200, ok: true, html, finalUrl: url, ms: 10 });

test('html: attributes, meta, anchors and visible text', () => {
  const html = '<html><head><meta name="next-success-url" content="/upsell/"><meta property="og:title" content="T &amp; C"></head><body><script>var x="lorem ipsum"</script><a href="/terms/" target=_blank>Terms</a><p>Hello <b>world</b></p></body></html>';
  assert.equal(H.meta(html)['next-success-url'], '/upsell/');
  assert.equal(H.meta(html)['og:title'], 'T & C');
  const a = H.anchors(html)[0];
  assert.equal(a.href, '/terms/');
  assert.equal(a.target, '_blank');
  assert.equal(H.text(html).includes('lorem'), false, 'script bodies are not visible text');
  assert.match(H.text(html), /Hello world/);
});

test('detect: SDK version and pinning', () => {
  const cases = [
    ['https://cdn.example.com/gh/acme/campaign-cart@v0.4.38/dist/loader.js', '0.4.38', true],
    ['https://cdn.example.com/npm/campaign-cart@0.5.0/dist/loader.js', '0.5.0', true],
    ['/vendor/campaign-cart@latest/loader.js', null, false],
    ['https://cdn.example.com/campaign-cart@0.4/loader.js', '0.4', false],
  ];
  for (const [src, version, pinned] of cases) {
    const s = D.sdk(page(`<script src="${src}"></script>`));
    assert.equal(s.present, true);
    assert.equal(s.version, version, src);
    assert.equal(s.pinned, pinned, src);
  }
  assert.equal(D.sdk(page('<p>no sdk</p>')).present, false);
});

test('detect: page types and routes', () => {
  assert.equal(page('<meta name="next-page-type" content="upsell">').kind.type, 'upsell');
  assert.equal(page('<form data-next-checkout-step="1"></form>', 'https://x.example.com/buy/').kind.type, 'checkout');
  assert.equal(page('<div data-next-order-items></div>', 'https://x.example.com/done/').kind.type, 'receipt');
  assert.equal(page('<h1>story</h1>', 'https://x.example.com/').kind.type, 'content');
  const legacy = page('<meta name="next-next-url" content="/up/">');
  assert.equal(legacy.routes.success, '/up/');
  assert.equal(legacy.routes.successTag, 'next-next-url');
});

test('detect: tracking ids', () => {
  const t = D.tracking(page(`<script>gtag('config','G-ABC123XYZ9')</script><script src="https://www.googletagmanager.com/gtag/js?id=G-ABC123XYZ9"></script>
  <script>(window,document,'script','dataLayer','GTM-ABCD12')</script><script>fbq('init', '1234567890123')</script>`));
  assert.deepEqual(t.gtm, ['GTM-ABCD12']);
  assert.deepEqual(t.ga4, ['G-ABC123XYZ9']);
  assert.deepEqual(t.meta, ['1234567890123']);
  assert.deepEqual(D.tracking(page('<p>The G-SHOCKER watch</p>')).ga4, [], 'bare G- words are not GA4 ids');
});

test('crawl: follows calls to action from any non-commerce page, including the 0.4 product type', async () => {
  const { crawl } = require(path.join(S, 'lib/crawl'));
  const site = {
    'https://x.example.com/landing/': '<meta name="next-page-type" content="product"><a class="btn" href="/checkout/">Buy</a>',
    'https://x.example.com/checkout/': '<meta name="next-page-type" content="checkout"><meta name="next-success-url" content="/receipt/">',
    'https://x.example.com/receipt/': '<meta name="next-page-type" content="receipt">',
  };
  const load = async (u) => P.build(u, site[u] ? { status: 200, ok: true, html: site[u], finalUrl: u } : { status: 404, ok: false, html: '', finalUrl: u });
  const r = await crawl('https://x.example.com/landing/', { load });
  assert.deepEqual(r.pages.map((p) => p.kind.type), ['product', 'checkout', 'receipt']);
});

test('util: masking keeps ids recognisable but not copyable', () => {
  assert.equal(U.maskId('GTM-ABCD12'), 'GTM-A•••12');
  assert.ok(!U.maskKey('abcdef123456').includes('def1'));
  assert.throws(() => U.assertion({ id: 'x', family: 'x', status: 'nope' }));
});

test('rules: Markdown list items become manual items under their heading', () => {
  const r = R.fromMarkdown('---\nstatus: active\n---\n# QA\n## Tracking\n- Every page loads the container.\n- [ ] Purchase fires once.\n```\n- not a rule\n```\n', 'qa.md');
  assert.equal(r.manual.length, 2);
  assert.equal(r.manual[0].section, 'Tracking');
  assert.equal(r.manual[1].text, 'Purchase fires once.');
});

test('rules: by-design waivers match ids with globs and leave passes alone', () => {
  const rules = R.fromJson({ byDesign: [{ id: 'tracking-consistency:*', reason: 'agreed' }] }, 'r.json');
  const rows = [
    U.assertion({ id: 'tracking-consistency:gtm:missing', family: 'tracking-consistency', status: 'warn', severity: 'warn' }),
    U.assertion({ id: 'tracking-consistency:ga4', family: 'tracking-consistency', status: 'pass', severity: 'warn' }),
    U.assertion({ id: 'links:dead', family: 'links', status: 'fail', severity: 'warn' }),
  ];
  const out = R.applyByDesign(rows, rules);
  assert.equal(out[0].waived, 'agreed');
  assert.equal(out[1].waived, undefined);
  assert.equal(out[2].waived, undefined);
  assert.equal(report.automatedDisposition(out), 'ready_with_exceptions');
  assert.equal(report.automatedDisposition(out.slice(0, 2)), 'ready');
});

test('report: automated row disposition', () => {
  const row = (status, severity) => U.assertion({ id: `x:${status}:${severity}`, family: 'x', status, severity });
  assert.equal(report.automatedDisposition([row('pass', 'blocker')]), 'ready');
  assert.equal(report.automatedDisposition([row('warn', 'info')]), 'ready', 'advice does not count');
  assert.equal(report.automatedDisposition([row('manual_review', 'warn')]), 'ready_with_exceptions');
  assert.equal(report.automatedDisposition([row('fail', 'blocker')]), 'blocked');
});

test('pr-review: added lines keep their line numbers and patterns are flagged', () => {
  const diff = `diff --git a/src/checkout.html b/src/checkout.html
--- a/src/checkout.html
+++ b/src/checkout.html
@@ -10,3 +10,5 @@
 <div>
+<span class="price">$49.95</span>
+<span data-next-display="package.price">$49.95</span>
 </div>
+<script>fetch('/api/v1/carts/', {method:'POST'})</script>
--- a/src/app.js
+++ b/src/app.js
@@ -1,1 +1,2 @@
+const adminApiToken = "abcd1234efgh5678";
`;
  const lines = pr.addedLines(diff);
  assert.deepEqual(lines.slice(0, 2).map((l) => l.line), [11, 12]);
  const ids = pr.review(diff).map((a) => a.id);
  assert.ok(ids.includes('pr-review:hardcoded-price'));
  assert.ok(ids.includes('pr-review:custom-cart-call'));
  assert.ok(ids.includes('pr-review:secret'));
  const price = pr.review(diff).find((a) => a.id === 'pr-review:hardcoded-price');
  assert.equal(price.evidence.length, 1, 'the SDK-bound price is not flagged');
});

test('figma: both copy formats and PNG size', () => {
  const rules = F.parse('.title {\n  font-family: Inter;\n  font-size: 48px;\n  font-weight: 700;\n  color: #1A7F37;\n}');
  assert.equal(rules[0].props['font-size'], '48px');
  const bare = F.parse('/* Heading */\nfont-family: "Inter";\nfont-size: 2rem;\n\n/* Body */\nfont-size: 16px;\ncolor: rgba(17, 24, 39, 1);');
  assert.equal(bare.length, 2);
  assert.equal(F.px(bare[0].props['font-size']), 32);
  assert.deepEqual(F.colors(bare), ['#111827']);
  const png = Buffer.alloc(24); png.write('\x89PNG', 0, 'latin1'); png.writeUInt32BE(1440, 16); png.writeUInt32BE(900, 20);
  assert.deepEqual(F.pngSize(png), { width: 1440, height: 900 });
});

test('record: affiliate parameters gate a test order', () => {
  assert.equal(rec.paramGate('https://x.example.com/checkout/?utm_source=fb').ok, true);
  const refused = rec.paramGate('https://x.example.com/checkout/?affid=12&sub1=abc');
  assert.equal(refused.ok, false);
  assert.match(refused.reason, /affid=12/);
  assert.equal(rec.paramGate('https://x.example.com/checkout/?affid=1', { confirmed: true }).ok, true);
  const allowed = { affid: ['1'], sub1: ['test'] };
  assert.equal(rec.paramGate('https://x.example.com/c/?affid=1&sub1=test', { allowed }).ok, true);
  assert.equal(rec.paramGate('https://x.example.com/c/?affid=2&sub1=test', { allowed }).ok, false);
});

test('record: order bodies are redacted before they are written', () => {
  const body = rec.summarizeOrderBody(rec.redact({ lines: [{ package_id: 3, quantity: 1 }], user: { email: 'a@example.com', language: 'en' }, payment_detail: { payment_method: 'card_token', card_token: 'tok_1' }, attribution: { utm_source: 'fb' } }));
  assert.equal(JSON.stringify(body).includes('a@example.com'), false);
  assert.equal(JSON.stringify(body).includes('tok_1'), false);
  assert.equal(body.language, 'en');
  assert.deepEqual(body.lines, [{ package_id: 3, quantity: 1 }]);
});

test('checklist catalog: unique ids, known roles, valid severities, globs that match real row ids', () => {
  const CL = require(path.join(S, 'lib/checklist'));
  const items = CL.CATALOG.items;
  assert.equal(new Set(items.map((x) => x.id)).size, items.length);
  for (const x of items) {
    assert.ok(U.SEVERITIES.includes(x.severity), x.id);
    assert.ok(x.roles.length && x.roles.every((r) => CL.CATALOG.roles.includes(r)), x.id);
  }
  const m = CL.matcher(['locale:*', '!locale:*:typed-amounts']);
  assert.equal(m('locale:/checkout'), true);
  assert.equal(m('locale:/checkout:typed-amounts'), false);
});

test('checklist: items follow the page types tested and rows decide automated items', () => {
  const CL = require(path.join(S, 'lib/checklist'));
  const row = (id, page, status, severity = 'warn') => U.assertion({ id, family: id.split(':')[0], page, status, severity });
  const pages = [{ label: '/checkout', role: 'checkout', in_scope: true }];
  const items = CL.build(pages, [row('form-negative:/checkout:empty', '/checkout', 'fail', 'blocker'), row('param-toggles:/checkout:exit', '/checkout', 'manual_review')]);
  const byId = Object.fromEntries(items.map((i) => [i.id, i]));
  assert.ok(byId['error-empty-fields'] && byId['order-every-package'], 'checkout items present');
  assert.equal(byId['toggle-seen'], undefined, '?seen=n is a presell item');
  assert.equal(byId['cart-contents'], undefined, 'storefront items absent');
  assert.equal(byId['error-empty-fields'].status, 'fail');
  assert.equal(byId['error-empty-fields'].severity, 'blocker');
  assert.equal(byId['toggle-exit'].status, 'open', 'evidence to look at is still open');
  assert.equal(byId['order-every-package'].auto, 'manual');
  assert.equal(CL.disposition(items, 'blocked'), 'blocked');
  const answered = CL.build(pages, [], { prior: items.map((i) => ({ id: i.id, answer: 'pass' })) });
  assert.equal(CL.disposition(answered, 'ready'), 'ready');
});

test('order: plans parse, and bad plans are refused', () => {
  const O = require(path.join(S, 'browser/order'));
  const plans = O.parsePlans('standard:accept, 3ds:decline@2 ,standard:accept-decline');
  assert.deepEqual(plans.map((p) => [p.card, p.upsell, p.pkg]), [['standard', 'accept', null], ['3ds', 'decline', 2], ['standard', 'accept-decline', null]]);
  assert.deepEqual(O.parsePlans('').map((p) => p.card), ['standard']);
  assert.throws(() => O.parsePlans('visa:accept'));
  assert.match(O.field('fname'), /data-next-checkout-field="first_name"/, 'SDK 0.5 name is matched too');
});

test('order: two different purchase events are not a duplicate; the same one twice is', () => {
  const rec = require(path.join(S, 'browser/record'));
  const base = { pages: [{ url: 'https://x.example.com/c/', params: {} }], calls: [{ kind: 'order', status: 201, body: {} }], receiptReached: true };
  const two = rec.judge({ ...base, events: [{ layer: 'dataLayer', event: 'dl_purchase', page: '/u/' }, { layer: 'dataLayer', event: 'purchase', page: '/u/' }] }, 'x');
  assert.equal(two.find((r) => r.id === 'order:purchase-event:x').status, 'pass');
  const dup = rec.judge({ ...base, events: [{ layer: 'dataLayer', event: 'dl_purchase', page: '/u/' }, { layer: 'dataLayer', event: 'dl_purchase', page: '/r/' }] }, 'x');
  assert.equal(dup.find((r) => r.id === 'order:purchase-event:x').status, 'warn');
});

test('speed: affiliate and click ids are removed before a speed visit', () => {
  const speed = require(path.join(S, 'lib/checks/speed'));
  assert.equal(speed.cleanUrl('https://x.example.com/p/?affid=7&sub1=a&utm_source=fb&gclid=1'), 'https://x.example.com/p/?utm_source=fb');
});

test('speed: targets flag slow pages', () => {
  const speed = require(path.join(S, 'lib/checks/speed'));
  assert.deepEqual(speed.judge({ score: 90, fcp: 1000, lcp: 2000, tbt: 50, cls: 0.01, si: 2000 }), []);
  const missed = speed.judge({ score: 30, fcp: 5000, lcp: 9000, tbt: 900, cls: 0.3, si: 9000 });
  assert.equal(missed.length, 6);
});
