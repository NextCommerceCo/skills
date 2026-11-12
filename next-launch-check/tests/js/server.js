'use strict';
// Local server for the offline tests: serves a fixture funnel, a fake SDK
// and a fake order API. Nothing leaves the machine.
const http = require('http');
const fs = require('fs');
const path = require('path');

const FIXTURES = path.join(__dirname, '..', 'fixtures');
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'application/javascript', '.jpg': 'image/jpeg', '.ico': 'image/x-icon' };

function start(site, { port = 0 } = {}) {
  const calls = [];
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    let p = decodeURIComponent(url.pathname);
    if (req.method === 'POST' && /^\/api\/v1\/orders\/?$/.test(p)) {
      let body = '';
      req.on('data', (c) => { body += c; });
      req.on('end', () => { calls.push({ kind: 'order', body }); res.writeHead(201, { 'content-type': 'application/json' }); res.end(JSON.stringify({ ref_id: 'TESTREF1', number: 1001 })); });
      return;
    }
    if (req.method === 'POST' && /\/upsells\/?$/.test(p)) {
      calls.push({ kind: 'upsell' });
      res.writeHead(201, { 'content-type': 'application/json' });
      res.end('{"ok":true}');
      return;
    }
    if (p === '/assets/big.jpg') {
      const size = 600 * 1024;
      res.writeHead(200, { 'content-type': 'image/jpeg', 'content-length': size });
      res.end(req.method === 'HEAD' ? undefined : Buffer.alloc(size));
      return;
    }
    let file;
    if (/^\/vendor\/campaign-cart@[^/]+\/loader\.js$/.test(p)) file = path.join(FIXTURES, 'shared', 'vendor', 'campaign-cart-fake.js');
    else if (p.startsWith('/assets/')) file = path.join(FIXTURES, 'shared', p);
    else {
      if (p.endsWith('/')) p += 'index.html';
      else if (!path.extname(p)) p += '/index.html';
      file = path.join(FIXTURES, site, p);
    }
    if (!file.startsWith(FIXTURES) || !fs.existsSync(file)) { res.writeHead(404, { 'content-type': 'text/html' }); res.end('<h1>Not found</h1>'); return; }
    const data = fs.readFileSync(file);
    res.writeHead(200, { 'content-type': TYPES[path.extname(file)] || 'application/octet-stream', 'content-length': data.length });
    res.end(req.method === 'HEAD' ? undefined : data);
  });
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => {
    const base = `http://127.0.0.1:${server.address().port}`;
    resolve({ base, calls, close: () => new Promise((r) => server.close(r)) });
  }));
}

module.exports = { start };

if (require.main === module) {
  start(process.argv[2] || 'good', { port: parseInt(process.argv[3] || '0', 10) }).then((s) => process.stdout.write(`${s.base}\n`));
}
