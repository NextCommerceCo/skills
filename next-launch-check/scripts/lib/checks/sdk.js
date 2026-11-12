'use strict';
const { assertion, maskKey } = require('../util');
const D = require('../detect');
const net = require('../net');
const { resolve, sameOrigin } = require('../util');

module.exports = async function sdk(ctx) {
  const out = [];
  const loaded = ctx.pages.filter((p) => p.ok);
  const commerce = loaded.filter((p) => ['checkout', 'upsell', 'receipt'].includes(p.kind.type));
  const versions = new Map();
  const keys = new Map();
  for (const p of loaded) {
    const s = p.sdk;
    const base = { family: 'sdk', page: p.label, url: p.url };
    const isCommerce = commerce.includes(p);
    if (!s.present) {
      if (isCommerce) out.push(assertion({ ...base, id: `sdk:${p.label}`, status: 'fail', severity: 'blocker', message: `No Campaign Cart SDK script on this ${p.kind.type} page.` }));
      continue;
    }
    versions.set(p.label, s.version || 'unversioned');
    out.push(assertion({ ...base, id: `sdk:${p.label}`, status: s.pinned ? 'pass' : 'warn', severity: 'warn', message: s.pinned ? `SDK ${s.version} loaded.` : `SDK script is not pinned to an exact version (${s.version || 'no version in the URL'}). An unpinned SDK can change under a live funnel.`, actual: s.src }));
    if (s.count > 1) out.push(assertion({ ...base, id: `sdk:${p.label}:duplicate`, status: 'warn', severity: 'warn', message: `SDK script is included ${s.count} times.` }));
    // Config scripts loaded before the SDK often carry the campaign key.
    const extra = [];
    if (isCommerce || p.sdk.present) {
      const sdkIdx = p.scripts.findIndex((x) => x.src && D.SDK_SRC_RE.test(x.src));
      const candidates = p.scripts.slice(0, sdkIdx < 0 ? undefined : sdkIdx).map((x) => resolve(x.src, p.finalUrl)).filter((u) => u && sameOrigin(u, p.finalUrl)).slice(0, 4);
      for (const u of candidates) {
        const r = await net.getPage(u, { timeoutMs: 8000 });
        if (r.ok) extra.push({ body: r.html });
      }
    }
    const key = D.apiKey(p, extra);
    if (key) keys.set(p.label, key.key);
    else if (isCommerce) out.push(assertion({ ...base, id: `sdk:${p.label}:api-key`, status: 'manual_review', severity: 'info', message: 'Campaign API key not found in the page or its config scripts. Confirm the SDK config loads (the browser lane checks that the SDK reaches ready).' }));
  }
  const distinctVersions = [...new Set(versions.values())];
  if (distinctVersions.length > 1) {
    out.push(assertion({ id: 'sdk:version-consistency', family: 'sdk', status: 'warn', severity: 'warn', message: 'Pages load different SDK versions.', actual: Object.fromEntries(versions) }));
  }
  const distinctKeys = [...new Set(keys.values())];
  if (distinctKeys.length > 1) {
    out.push(assertion({ id: 'sdk:api-key-consistency', family: 'sdk', status: 'fail', severity: 'blocker', message: 'Pages use different campaign API keys, so they point at different campaigns.', actual: Object.fromEntries([...keys].map(([k, v]) => [k, maskKey(v)])) }));
  } else if (distinctKeys.length === 1) {
    out.push(assertion({ id: 'sdk:api-key', family: 'sdk', status: 'pass', severity: 'info', message: `One campaign API key across ${keys.size} page(s): ${maskKey(distinctKeys[0])}.` }));
  }
  if (!versions.size && !commerce.length) {
    out.push(assertion({ id: 'sdk:none', family: 'sdk', status: 'skipped', severity: 'info', message: 'No SDK and no commerce pages found. Fine for a pure content page.' }));
  }
  return out;
};
