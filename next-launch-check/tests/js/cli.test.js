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
    const { result } = await cli.runCheck('funnel', `${s.base}/`, cli.parseArgs(['--out', tmp()]));
    assert.deepEqual(result.pages.map((p) => p.label), ['/', '/landing', '/checkout', '/upsell', '/receipt']);
    assert.deepEqual(result.pages.map((p) => p.type), ['content', 'content', 'checkout', 'upsell', 'receipt']);
    assert.equal(result.disposition_automated, 'ready', ids(result).join(', '));
    assert.equal(result.disposition, 'incomplete', 'manual checklist is still open');
    assert.equal(s.calls.length, 0, 'the source lane never posts');
  } finally { await s.close(); }
});

test('broken funnel: each planted fault is reported', async () => {
  const s = await server.start('broken');
  try {
    const out = tmp();
    const { result } = await cli.runCheck('funnel', `${s.base}/`, cli.parseArgs(['--page', `${s.base}/upsell/`, '--out', out]));
    const bad = ids(result);
    for (const want of ['http:/upsel', 'funnel-chain:/checkout:next-success-url', 'sdk:/upsell', 'sdk:version-consistency', 'tracking-consistency:gtm:differs', 'legal-links:privacy', 'legal-links:terms:new-tab', 'links:/landing:placeholder', 'links:dead', 'content-hygiene:/landing:placeholder', 'seo-meta:/landing:viewport', 'media-weight:heavy']) {
      assert.ok(bad.includes(want), `missing ${want}; got ${bad.join(', ')}`);
    }
    assert.equal(result.disposition, 'blocked');
    assert.ok(fs.readFileSync(path.join(out, 'report.md'), 'utf8').includes('## Blockers'));
  } finally { await s.close(); }
});

test('--mask-ids hides tracking ids in the report', async () => {
  const s = await server.start('broken');
  try {
    const out = tmp();
    await cli.runCheck('funnel', `${s.base}/`, cli.parseArgs(['--mask-ids', '--out', out]));
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
    const { result } = await cli.runCheck('funnel', `${s.base}/`, cli.parseArgs(['--rules', rules, '--out', dir]));
    const row = result.assertions.find((a) => a.id === 'tracking-consistency:gtm:differs');
    assert.equal(row.waived, 'two containers by agreement');
    assert.ok(result.manual.some((m) => m.text === 'Shipping insurance sits before payment.'));
  } finally { await s.close(); }
});

test('campaigns-os verdict: covered families defer to it', async () => {
  const s = await server.start('good');
  const dir = tmp();
  const v = path.join(dir, 'verdict.json');
  fs.writeFileSync(v, JSON.stringify({ disposition: 'blocked', run_id: 'RUN1', assertions: [] }));
  try {
    const { result } = await cli.runCheck('funnel', `${s.base}/`, cli.parseArgs(['--campaigns-os-verdict', v, '--out', dir]));
    assert.ok(result.assertions.filter((a) => a.family === 'funnel-chain').every((a) => a.deferred));
    assert.equal(result.campaigns_os.disposition, 'blocked');
    assert.equal(result.disposition_automated, 'blocked');
  } finally { await s.close(); }
});

test('finalize: answers close the manual checklist', async () => {
  const s = await server.start('good');
  const dir = tmp();
  try {
    const { result } = await cli.runCheck('funnel', `${s.base}/`, cli.parseArgs(['--out', dir]));
    const answers = Object.fromEntries(result.manual.map((m) => [m.id, 'pass']));
    answers['F-19'] = { answer: 'fail', note: 'Safari cuts the footer' };
    fs.writeFileSync(path.join(dir, 'answers.json'), JSON.stringify(answers));
    const done = cli.finalize(dir, path.join(dir, 'answers.json'));
    assert.equal(done.result.disposition, 'ready_with_exceptions');
    assert.match(fs.readFileSync(path.join(dir, 'report.md'), 'utf8'), /Safari cuts the footer/);
  } finally { await s.close(); }
});

test('storefront mode crawls header and footer links', async () => {
  const s = await server.start('good');
  try {
    const { result } = await cli.runCheck('storefront', `${s.base}/`, cli.parseArgs(['--out', tmp()]));
    assert.ok(result.pages.length > 1);
    assert.ok(result.assertions.some((a) => a.id === 'storefront-nav:footer'));
    assert.ok(result.manual.every((m) => m.id.startsWith('S-')));
  } finally { await s.close(); }
});

test('cli rejects unknown options and non-http URLs', async () => {
  assert.equal(await cli.main(['funnel', 'file:///etc/passwd', '--out', tmp()]), 2);
  assert.equal(await cli.main(['funnel', 'https://x.example.com', '--bogus']), 2);
});
