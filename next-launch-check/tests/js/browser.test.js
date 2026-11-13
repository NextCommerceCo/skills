'use strict';
// Browser lane against the fixture funnels. Runs only where Playwright and a
// Chromium build are installed (not in the catalog's CI). Offline: every
// request outside 127.0.0.1 is answered locally.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const fs = require('fs');
const os = require('os');
const server = require('./server');
const S = path.join(__dirname, '..', '..', 'scripts');
const PW = require(path.join(S, 'browser/playwright'));
const cli = require(path.join(S, 'launch-check.js'));

process.env.NEXT_LAUNCH_CHECK_OFFLINE = '1';
function canRun() {
  const loaded = PW.load();
  if (!loaded) return 'Playwright is not installed';
  try { if (!fs.existsSync(loaded.pw.chromium.executablePath())) return 'Chromium for Playwright is not installed'; } catch (_) { return 'Chromium for Playwright is not installed'; }
  return false;
}
const skip = canRun();
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'nlc-br-'));
const row = (r, id) => r.assertions.find((a) => a.id === id);

test('browser lane: good funnel', { skip, timeout: 240000 }, async () => {
  const s = await server.start('good');
  try {
    const { result } = await cli.runCheck('funnel', `${s.base}/`, cli.parseArgs(['--no-speed', '--out', tmp()]));
    assert.equal(row(result, 'browser-runtime:/checkout:sdk-ready').status, 'pass');
    assert.equal(row(result, 'param-propagation:/landing->/checkout').status, 'pass');
    assert.equal(row(result, 'form-negative:/checkout:empty').status, 'pass');
    assert.equal(row(result, 'locale:/checkout').status, 'pass');
    assert.equal(row(result, 'responsive:/checkout:pinch-zoom').status, 'pass');
    assert.equal(row(result, 'addons:/checkout:preselected').status, 'pass');
    assert.equal(row(result, 'currency:/checkout:GBP').status, 'pass', 'GBP prices are converted');
    assert.equal(row(result, 'locale:/checkout:typed-amounts').status, 'pass', 'amounts inside SDK summaries are not typed amounts');
    assert.equal(row(result, 'responsive:/checkout:zoom-150').status, 'pass');
    assert.equal(row(result, 'param-toggles:/:timer').status, 'pass', 'timer=n hides the countdown');
    assert.equal(row(result, 'param-toggles:/:reviews').status, 'pass');
    assert.equal(row(result, 'param-toggles:/:seen').status, 'pass', 'nothing to hide is a pass, not a question');
    assert.equal(result.checklist.find((i) => i.id === 'toggle-timer').status, 'pass');
    assert.equal(result.checklist.find((i) => i.id === 'error-empty-fields').status, 'pass');
    assert.equal(row(result, 'tags-observed:/checkout').status, 'pass');
    for (const k of ['invalid-card', 'invalid-cvv', 'error-language', 'no-order']) assert.equal(row(result, `form-negative:/checkout:${k}`).status, 'pass', k);
    assert.equal(row(result, 'tags-observed:/checkout:ga4').status, 'pass');
    assert.equal(row(result, 'images:/checkout').status, 'pass');
    assert.equal(result.checklist.find((i) => i.id === 'error-invalid-card').status, 'pass');
    assert.equal(s.calls.length, 0, 'the browser lane never places an order');
  } finally { await s.close(); }
});

test('browser lane: broken funnel', { skip, timeout: 240000 }, async () => {
  const s = await server.start('broken');
  try {
    const { result } = await cli.runCheck('funnel', `${s.base}/`, cli.parseArgs(['--no-speed', '--page', `${s.base}/upsell/`, '--out', tmp()]));
    assert.equal(row(result, 'param-propagation:/landing->/checkout').status, 'fail');
    assert.equal(row(result, 'form-negative:/checkout:empty').severity, 'blocker');
    assert.equal(row(result, 'form-negative:/checkout:empty').status, 'fail');
    assert.equal(row(result, 'responsive:/upsell:mobile').status, 'warn');
    assert.equal(row(result, 'responsive:/checkout:pinch-zoom').status, 'fail', 'zoom is locked');
    assert.equal(row(result, 'addons:/checkout:preselected').status, 'warn', 'shipping protection is pre-ticked');
    assert.equal(row(result, 'currency:/checkout:GBP').status, 'fail', 'GBP shows the USD numbers');
    assert.equal(row(result, 'param-toggles:/:timer').status, 'fail', 'the countdown is not wired to timer=n');
    assert.equal(row(result, 'form-negative:/checkout:invalid-card').status, 'fail', 'an unvalidated card reaches the order step');
    assert.equal(result.checklist.find((i) => i.id === 'pinch-zoom').status, 'fail');
    assert.equal(result.checklist.find((i) => i.id === 'zoom-150').status === 'fail', false, 'zoom items judge overflow only');
    assert.equal(s.calls.length, 0, 'the unvalidated form was submitted, but the order call was blocked');
  } finally { await s.close(); }
});

test('recorder: --into adds the order to an existing run and its checklist', { skip, timeout: 120000 }, async () => {
  const s = await server.start('good');
  const out = tmp();
  try {
    await cli.runCheck('checkout', `${s.base}/checkout/`, cli.parseArgs(['--no-browser', '--no-speed', '--out', out]));
    const code = await cli.main(['record', `${s.base}/checkout/?autotest=1&utm_source=fb`, '--headless', '--record-minutes', '0.5', '--into', out]);
    assert.equal(code, 0);
    const r = JSON.parse(fs.readFileSync(path.join(out, 'results.json'), 'utf8'));
    assert.equal(r.checklist.find((i) => i.id === 'standard-card-order').status, 'pass');
    assert.ok(r.assertions.some((a) => a.id === 'form-negative:/checkout:empty' || a.family === 'http'), 'earlier rows kept');
  } finally { await s.close(); }
});

test('order: two typed-card orders, accept and decline, land in the run', { skip, timeout: 180000 }, async () => {
  const s = await server.start('good');
  const out = tmp();
  try {
    await cli.runCheck('checkout', `${s.base}/checkout/`, cli.parseArgs(['--no-browser', '--no-speed', '--out', out]));
    assert.equal(await cli.main(['order', `${s.base}/checkout/`, '--into', out, '--orders', 'standard:accept,3ds:decline']), 0);
    const r = JSON.parse(fs.readFileSync(path.join(out, 'results.json'), 'utf8'));
    for (const id of ['order:created:1-standard-accept', 'order:upsells:1-standard-accept', 'order:receipt:1-standard-accept', 'order:created:2-3ds-decline', 'order:receipt:2-3ds-decline']) assert.equal(row(r, id).status, 'pass', id);
    assert.equal(row(r, 'order:upsells:2-3ds-decline'), undefined, 'declined upsell sends no upsell call');
    const byId = Object.fromEntries(r.checklist.map((i) => [i.id, i]));
    assert.equal(byId['standard-card-order'].status, 'pass');
    assert.equal(byId['3ds-card-order'].status, 'pass');
    assert.equal(s.calls.filter((c) => c.kind === 'order').length, 2, 'exactly one order per plan');
  } finally { await s.close(); }
});

test('order: --dry-run fills the checkout and sends no order', { skip, timeout: 120000 }, async () => {
  const s = await server.start('good');
  const out = tmp();
  try {
    await cli.runCheck('checkout', `${s.base}/checkout/`, cli.parseArgs(['--no-browser', '--no-speed', '--out', out]));
    await cli.main(['order', `${s.base}/checkout/`, '--into', out, '--dry-run']);
    const r = JSON.parse(fs.readFileSync(path.join(out, 'results.json'), 'utf8'));
    assert.equal(row(r, 'order:rehearsal:1-standard-accept').status, 'pass');
    assert.equal(s.calls.length, 0);
  } finally { await s.close(); }
});

test('order: affiliate parameters and too many orders are refused', { skip, timeout: 60000 }, async () => {
  const out = tmp();
  assert.equal(await cli.main(['order', 'http://127.0.0.1:9/checkout/?affid=5', '--out', out]), 3);
  assert.equal(await cli.main(['order', 'http://127.0.0.1:9/checkout/', '--orders', 'standard,standard,standard,standard,standard', '--out', out]), 3);
});

test('recorder: observes one order, the upsell and the purchase event', { skip, timeout: 120000 }, async () => {
  const s = await server.start('good');
  const out = tmp();
  try {
    const code = await cli.main(['record', `${s.base}/checkout/?autotest=1&utm_source=fb`, '--headless', '--record-minutes', '0.5', '--out', out]);
    assert.equal(code, 0);
    const r = JSON.parse(fs.readFileSync(path.join(out, 'results.json'), 'utf8'));
    for (const id of ['order:created', 'order:upsells', 'order:purchase-event', 'order:attribution', 'order:receipt']) assert.equal(row(r, `${id}:recorded`).status, 'pass', id);
    const record = JSON.parse(fs.readFileSync(path.join(out, 'order-record.json'), 'utf8'));
    assert.equal(JSON.stringify(record).includes('qa@example.com'), false, 'emails are redacted');
  } finally { await s.close(); }
});
