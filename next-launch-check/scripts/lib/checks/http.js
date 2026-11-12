'use strict';
const { assertion, sameOrigin } = require('../util');

module.exports = async function http(ctx) {
  const out = [];
  for (const p of ctx.pages) {
    const base = { family: 'http', page: p.label, url: p.url };
    if (!p.ok) {
      out.push(assertion({ ...base, id: `http:${p.label}`, status: 'fail', severity: 'blocker', message: p.status ? `Page answered HTTP ${p.status}.` : `Page did not load: ${p.error || 'no response'}.`, expected: '2xx', actual: p.status || p.error }));
      continue;
    }
    out.push(assertion({ ...base, id: `http:${p.label}`, status: 'pass', severity: 'blocker', message: `HTTP ${p.status} in ${p.ms} ms.`, actual: p.status }));
    if (!sameOrigin(p.finalUrl, p.url)) {
      out.push(assertion({ ...base, id: `http:${p.label}:redirect`, status: 'warn', severity: 'info', message: `Redirects to another site: ${p.finalUrl}.`, actual: p.finalUrl }));
    }
    if (p.ms > 3000) {
      out.push(assertion({ ...base, id: `http:${p.label}:slow`, status: 'warn', severity: 'info', message: `Server took ${p.ms} ms to send the HTML.`, actual: p.ms }));
    }
  }
  return out;
};
