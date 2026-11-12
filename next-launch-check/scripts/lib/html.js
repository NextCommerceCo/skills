'use strict';
// Small, dependency-free HTML reader. It is not a full parser: it finds tags
// and attributes well enough for launch checks on server-rendered pages.

const ENTITY = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', '#39': "'" };

function decode(text) {
  return String(text || '').replace(/&(#x?[0-9a-f]+|[a-z]+);/gi, (m, code) => {
    const c = code.toLowerCase();
    if (c[0] === '#') {
      const n = c[1] === 'x' ? parseInt(c.slice(2), 16) : parseInt(c.slice(1), 10);
      return Number.isFinite(n) ? String.fromCodePoint(n) : m;
    }
    return ENTITY[c] !== undefined ? ENTITY[c] : m;
  });
}

function parseAttrs(src) {
  const attrs = {};
  const re = /([^\s=/>"']+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    const name = m[1].toLowerCase();
    const value = m[2] !== undefined ? m[2] : m[3] !== undefined ? m[3] : m[4] !== undefined ? m[4] : '';
    if (!(name in attrs)) attrs[name] = decode(value);
  }
  return attrs;
}

function stripNoise(html) {
  return String(html || '')
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<script\b[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style\b[\s\S]*?<\/style>/gi, ' ')
    .replace(/<noscript\b[\s\S]*?<\/noscript>/gi, ' ')
    .replace(/<template\b[\s\S]*?<\/template>/gi, ' ');
}

// Every opening tag of the given names, with attributes and (for paired tags)
// the inner HTML up to the matching close tag.
function tags(html, name, { inner = false } = {}) {
  const out = [];
  const re = new RegExp(`<${name}\\b([^>]*)>`, 'gi');
  let m;
  while ((m = re.exec(html)) !== null) {
    const item = { attrs: parseAttrs(m[1]), index: m.index };
    if (inner) {
      const close = new RegExp(`</${name}\\s*>`, 'i');
      const rest = html.slice(re.lastIndex);
      const c = close.exec(rest);
      item.inner = c ? rest.slice(0, c.index) : '';
    }
    out.push(item);
  }
  return out;
}

function text(html) {
  return decode(stripNoise(html).replace(/<[^>]+>/g, ' ')).replace(/\s+/g, ' ').trim();
}

function meta(html) {
  const byName = {};
  for (const t of tags(html, 'meta')) {
    const key = (t.attrs.name || t.attrs.property || t.attrs['http-equiv'] || '').toLowerCase();
    if (key && !(key in byName)) byName[key] = t.attrs.content || '';
  }
  return byName;
}

function title(html) {
  const t = tags(html, 'title', { inner: true })[0];
  return t ? text(t.inner) : '';
}

function scripts(html) {
  return tags(html, 'script', { inner: true }).map((t) => ({ src: t.attrs.src || '', attrs: t.attrs, body: t.attrs.src ? '' : t.inner }));
}

function anchors(html) {
  return tags(html, 'a', { inner: true }).map((t) => ({
    href: t.attrs.href,
    target: t.attrs.target || '',
    rel: t.attrs.rel || '',
    cls: t.attrs.class || '',
    attrs: t.attrs,
    text: text(t.inner),
    hasImage: /<img\b|<svg\b/i.test(t.inner),
    index: t.index,
  }));
}

// Region of the page a node sits in, by the nearest preceding landmark tag.
function regionAt(html, index) {
  const before = html.slice(0, index).toLowerCase();
  const last = (tag) => Math.max(before.lastIndexOf(`<${tag}`), -1);
  const lastClose = (tag) => before.lastIndexOf(`</${tag}`);
  for (const tag of ['footer', 'nav', 'header']) {
    const open = last(tag);
    if (open >= 0 && open > lastClose(tag)) return tag;
  }
  return 'main';
}

module.exports = { decode, parseAttrs, stripNoise, tags, text, meta, title, scripts, anchors, regionAt };
