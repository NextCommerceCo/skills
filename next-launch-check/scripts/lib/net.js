'use strict';
// Plain HTTP reads. GET and HEAD only: this module never sends a body.

const UA = 'Mozilla/5.0 (compatible; next-launch-check)';

async function getPage(url, { timeoutMs = 20000 } = {}) {
  const started = Date.now();
  try {
    const res = await fetch(url, { redirect: 'follow', headers: { 'user-agent': UA, accept: 'text/html,*/*' }, signal: AbortSignal.timeout(timeoutMs) });
    const body = await res.text();
    return { url, finalUrl: res.url || url, status: res.status, ok: res.ok, contentType: res.headers.get('content-type') || '', html: body, ms: Date.now() - started, redirected: res.redirected };
  } catch (error) {
    return { url, finalUrl: url, status: 0, ok: false, contentType: '', html: '', ms: Date.now() - started, error: String(error && error.message ? error.message : error) };
  }
}

async function status(url, { timeoutMs = 10000 } = {}) {
  for (const method of ['HEAD', 'GET']) {
    try {
      const res = await fetch(url, { method, redirect: 'follow', headers: { 'user-agent': UA }, signal: AbortSignal.timeout(timeoutMs) });
      if (method === 'GET' && res.body) res.body.cancel().catch(() => {});
      if (method === 'HEAD' && (res.status === 405 || res.status === 501 || res.status === 403)) continue;
      return { status: res.status, ok: res.ok, finalUrl: res.url || url };
    } catch (error) {
      if (method === 'GET') return { status: 0, ok: false, finalUrl: url, error: String(error.message || error) };
    }
  }
  return { status: 0, ok: false, finalUrl: url };
}

async function size(url, { timeoutMs = 10000 } = {}) {
  try {
    const head = await fetch(url, { method: 'HEAD', redirect: 'follow', headers: { 'user-agent': UA }, signal: AbortSignal.timeout(timeoutMs) });
    const len = head.headers.get('content-length');
    if (head.ok && len) return { bytes: parseInt(len, 10), type: head.headers.get('content-type') || '', status: head.status };
  } catch (_) { /* fall through to a ranged GET */ }
  try {
    const res = await fetch(url, { headers: { 'user-agent': UA, range: 'bytes=0-0' }, redirect: 'follow', signal: AbortSignal.timeout(timeoutMs) });
    const range = res.headers.get('content-range');
    const len = res.headers.get('content-length');
    if (res.body) res.body.cancel().catch(() => {});
    const total = range && /\/(\d+)$/.exec(range) ? parseInt(/\/(\d+)$/.exec(range)[1], 10) : res.status === 200 && len ? parseInt(len, 10) : null;
    return { bytes: total, type: res.headers.get('content-type') || '', status: res.status };
  } catch (error) {
    return { bytes: null, type: '', status: 0, error: String(error.message || error) };
  }
}

async function getJson(url, { timeoutMs = 60000 } = {}) {
  const res = await fetch(url, { headers: { 'user-agent': UA, accept: 'application/json' }, signal: AbortSignal.timeout(timeoutMs) });
  const body = await res.json().catch(() => null);
  return { status: res.status, ok: res.ok, body };
}

// Run async jobs with a small concurrency cap.
async function pool(items, limit, fn) {
  const results = new Array(items.length);
  let next = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length) {
      const i = next++;
      results[i] = await fn(items[i], i);
    }
  });
  await Promise.all(workers);
  return results;
}

module.exports = { getPage, status, size, getJson, pool, UA };
