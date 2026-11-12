'use strict';
// End-to-end source lane against the local fixture funnels. Offline.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const fs = require('fs');
const os = require('os');
const server = require('./server');
const cli = require(path.join(__dirname, '..', '..', 'scripts', 'launch-check.js'));

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'nlc-'));
const ids = (r) => r.assertions.filter((a) => a.status !== 'pass').map((a) => a.id);

test('good funnel: crawl finds every page and nothing fails', async () => {
  const s = await server.start('good');
  try {
    const { result } = await cli.runCheck('funnel', `${s.base}/`, cli.parseArgs(['--no-browser', '--no-speed', '--out', tmp()]));
    assert.deepEqual(result.pages.map((p) => p.label), ['/', '/landing', '/checkout', '/upsell', '/receipt']);
    assert.deepEqual(result.pages.map((p) => p.type), ['content', 'content', 'checkout', 'upsell', 'receipt']);
    assert.equal(result.disposition_automated, 'ready', ids(result).join(', '));
    assert.deepEqual(result.pages.map((p) => p.role), ['presell', 'landing', 'checkout', 'upsell', 'receipt']);
    assert.equal(result.disposition, 'incomplete', 'manual checklist items are still open');
    assert.equal(s.calls.length, 0, 'the source lane never posts');
  } finally { await s.close(); }
});

test('broken funnel: each planted fault is reported', async () => {
  const s = await server.start('broken');
  try {
    const out = tmp();
    const { result } = await cli.runCheck('funnel', `${s.base}/`, cli.parseArgs(['--no-browser', '--no-speed', '--page', `${s.base}/upsell/`, '--out', out]));
    const bad = ids(result);
    for (const want of ['http:/upsel', 'funnel-chain:/checkout:next-success-url', 'sdk:/upsell', 'sdk:version-consistency', 'tracking-consistency:gtm:differs', 'legal-links:privacy', 'legal-links:terms:new-tab', 'links:/landing:placeholder', 'links:dead', 'content-hygiene:/landing:placeholder', 'seo-meta:/landing:viewport', 'media-weight:heavy']) {
      assert.ok(bad.includes(want), `missing ${want}; got ${bad.join(', ')}`);
    }
    assert.equal(result.disposition, 'blocked');
    const md = fs.readFileSync(path.join(out, 'report.md'), 'utf8');
    assert.match(md, /### `pages-load` — Every page loads/);
    assert.match(md, /likely fix:/);
    const html = fs.readFileSync(path.join(out, 'report.html'), 'utf8');
    assert.match(html, /<b>Blocked<\/b>/);
    assert.match(html, /What to fix/);
  } finally { await s.close(); }
});

test('--mask-ids hides tracking ids in the report', async () => {
  const s = await server.start('broken');
  try {
    const out = tmp();
    await cli.runCheck('funnel', `${s.base}/`, cli.parseArgs(['--no-browser', '--no-speed', '--mask-ids', '--out', out]));
    const md = fs.readFileSync(path.join(out, 'report.md'), 'utf8');
    assert.equal(md.includes('GTM-TESTAA1'), false);
    assert.ok(md.includes('GTM-T'));
  } finally { await s.close(); }
});

test('rules file: by-design items stop counting and Markdown items join the checklist', async () => {
  const s = await server.start('broken');
  const dir = tmp();
  const rules = path.join(dir, 'rules.json');
  fs.writeFileSync(rules, JSON.stringify({ name: 'Client rules', byDesign: [{ id: 'tracking-consistency:gtm:*', reason: 'two containers by agreement' }], manual: [{ text: 'Shipping insurance sits before payment.' }] }));
  try {
    const { result } = await cli.runCheck('funnel', `${s.base}/`, cli.parseArgs(['--no-browser', '--no-speed', '--rules', rules, '--out', dir]));
    const row = result.assertions.find((a) => a.id === 'tracking-consistency:gtm:differs');
    assert.equal(row.waived, 'two containers by agreement');
    assert.ok(result.checklist.some((m) => m.text === 'Shipping insurance sits before payment.'));
  } finally { await s.close(); }
});

test('campaigns-os verdict: its rows join the checklist and our rows in the families it proved defer to it', async () => {
  const s = await server.start('good');
  const v = path.join(__dirname, '..', 'fixtures', 'campaigns-os-verdict.json');
  try {
    const { result } = await cli.runCheck('funnel', `${s.base}/`, cli.parseArgs(['--no-browser', '--no-speed', '--campaigns-os-verdict', v, '--out', tmp()]));
    assert.ok(result.assertions.filter((a) => a.family === 'funnel-chain').every((a) => a.deferred), 'route checks defer to funnel-flow');
    assert.ok(result.assertions.some((a) => a.id.startsWith('cos:browser-runtime:browser-payment-surface:')));
    assert.equal(result.campaigns_os.disposition, 'blocked');
    const byId = Object.fromEntries(result.checklist.map((i) => [i.id, i]));
    assert.equal(byId['cos-verdict'].status, 'fail', 'the campaigns-os verdict is a checklist item');
    assert.equal(byId['cos-commerce-structure'].status, 'pass', 'commerce structure from campaigns-os');
    assert.equal(byId['no-placeholder-copy'].status, 'fail', 'template placeholder text from campaigns-os');
    assert.equal(byId['standard-card-order'].status, 'pass', 'typed-card order from campaigns-os');
    assert.equal(result.disposition, 'blocked');
  } finally { await s.close(); }
});

test('campaigns-os items stay out of a run where it did not run', async () => {
  const s = await server.start('good');
  try {
    const { result } = await cli.runCheck('funnel', `${s.base}/`, cli.parseArgs(['--no-browser', '--no-speed', '--out', tmp()]));
    assert.ok(!result.checklist.some((i) => i.id.startsWith('CO-')));
    assert.match(result.lanes.campaigns_os, /not run/);
  } finally { await s.close(); }
});

test('campaigns-os: project detection and page types', () => {
  const COS = require(path.join(__dirname, '..', '..', 'scripts', 'lib', 'campaigns-os'));
  assert.equal(COS.detect({}, os.tmpdir()).run, false);
  assert.equal(COS.detect({ site: '.', family: 'olympus' }).kind, 'site');
  assert.equal(COS.detect({ site: '.' }).run, false, '--site needs --family');
  assert.equal(COS.detect({ mapId: 'm1', spec: 's.json' }).kind, 'map');
  assert.equal(COS.detect({ packet: 'p.json', campaignsOs: false }).run, false);
  assert.equal(COS.roleOf('upsell-bundle-stepper'), 'upsell');
  assert.equal(COS.roleOf('checkout'), 'checkout');
  assert.equal(COS.roleOf('campaign'), null);
});

test('finalize: answers close the checklist', async () => {
  const s = await server.start('good');
  const dir = tmp();
  try {
    const { result } = await cli.runCheck('funnel', `${s.base}/`, cli.parseArgs(['--no-browser', '--no-speed', '--out', dir]));
    const answers = Object.fromEntries(result.checklist.filter((i) => i.status === 'open').map((i) => [i.id, 'pass']));
    answers['browsers'] = { answer: 'fail', note: 'Safari cuts the footer' };
    fs.writeFileSync(path.join(dir, 'answers.json'), JSON.stringify(answers));
    const done = await cli.finalize(dir, path.join(dir, 'answers.json'), { pdf: false });
    assert.equal(done.result.disposition, 'ready_with_exceptions');
    assert.match(fs.readFileSync(path.join(dir, 'report.md'), 'utf8'), /Safari cuts the footer/);
    fs.writeFileSync(path.join(dir, 'bad.json'), '{"no-such-item":"pass"}');
    await assert.rejects(() => cli.finalize(dir, path.join(dir, 'bad.json'), { pdf: false }));
    assert.ok(fs.existsSync(path.join(dir, 'report.html')), 'html report written');
    assert.match(fs.readFileSync(path.join(dir, 'report.md'), 'utf8'), /## Fix list/);
  } finally { await s.close(); }
});

test('scope: a checkout-only run checks that page and applies checkout items only', async () => {
  const s = await server.start('broken');
  try {
    const { result } = await cli.runCheck('checkout', `${s.base}/checkout/`, cli.parseArgs(['--no-browser', '--no-speed', '--out', tmp()]));
    assert.deepEqual(result.pages.filter((p) => p.in_scope).map((p) => p.label), ['/checkout']);
    const ids = result.checklist.map((i) => i.id);
    assert.ok(ids.includes('error-invalid-card') && ids.includes('order-every-package'));
    assert.ok(!ids.includes('toggle-seen') && !ids.includes('scrolling-cta'), 'presell-only items are absent');
    assert.equal(result.checklist.find((i) => i.id === 'route-tags').status, 'fail');
  } finally { await s.close(); }
});

test('scope: a page that does not look like the type you said gets a note', async () => {
  const s = await server.start('good');
  try {
    const { result } = await cli.runCheck('checkout', `${s.base}/`, cli.parseArgs(['--no-browser', '--no-speed', '--out', tmp()]));
    assert.ok(result.assertions.some((a) => a.id === 'scope:entry'));
  } finally { await s.close(); }
});

test('storefront mode crawls header and footer links', async () => {
  const s = await server.start('good');
  try {
    const { result } = await cli.runCheck('storefront', `${s.base}/`, cli.parseArgs(['--no-browser', '--no-speed', '--out', tmp()]));
    assert.ok(result.pages.length > 1);
    assert.ok(result.assertions.some((a) => a.id === 'storefront-nav:footer'));
    assert.ok(result.checklist.some((m) => m.id === 'cart-contents'));
    assert.ok(!result.checklist.some((m) => m.id === 'error-empty-fields'), 'no checkout items on a storefront');
  } finally { await s.close(); }
});

test('cli rejects unknown options and non-http URLs', async () => {
  assert.equal(await cli.main(['funnel', 'file:///etc/passwd', '--out', tmp()]), 2);
  assert.equal(await cli.main(['funnel', 'https://x.example.com', '--bogus']), 2);
});
