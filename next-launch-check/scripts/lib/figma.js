'use strict';
// Read CSS copied from Figma's Inspect panel. Figma copies either rule blocks
// (".name { … }") or bare declarations under "/* Layer name */" comments;
// both are accepted.
function parse(css) {
  const blocks = [];
  const text = String(css || '').replace(/\r/g, '');
  const ruleRe = /([^{}]*)\{([^}]*)\}/g;
  let m;
  let hadRules = false;
  while ((m = ruleRe.exec(text)) !== null) {
    hadRules = true;
    const name = m[1].replace(/\/\*|\*\//g, '').trim() || `block ${blocks.length + 1}`;
    blocks.push({ name, props: decls(m[2]) });
  }
  if (!hadRules) {
    let current = { name: 'block 1', lines: [] };
    for (const line of text.split('\n')) {
      const c = /^\s*\/\*\s*(.*?)\s*\*\/\s*$/.exec(line);
      if (c) {
        if (current.lines.length) blocks.push({ name: current.name, props: decls(current.lines.join('\n')) });
        current = { name: c[1] || `block ${blocks.length + 1}`, lines: [] };
      } else if (/:/.test(line)) current.lines.push(line);
    }
    if (current.lines.length) blocks.push({ name: current.name, props: decls(current.lines.join('\n')) });
  }
  return blocks.filter((b) => Object.keys(b.props).length);
}

function decls(src) {
  const props = {};
  for (const part of src.split(/;|\n/)) {
    const i = part.indexOf(':');
    if (i < 0) continue;
    const k = part.slice(0, i).trim().toLowerCase();
    const v = part.slice(i + 1).replace(/\/\*.*?\*\//g, '').trim();
    if (k && v && /^[a-z-]+$/.test(k)) props[k] = v;
  }
  return props;
}

function family(v) { return String(v || '').split(',')[0].replace(/["']/g, '').trim().toLowerCase(); }
function px(v) {
  const s = String(v || '').trim();
  let m = /^([\d.]+)px$/.exec(s); if (m) return parseFloat(m[1]);
  m = /^([\d.]+)rem$/.exec(s); if (m) return parseFloat(m[1]) * 16;
  m = /^([\d.]+)$/.exec(s); if (m) return parseFloat(m[1]);
  return null;
}
function weight(v) { const s = String(v || '').toLowerCase(); return s === 'bold' ? 700 : s === 'normal' ? 400 : parseInt(s, 10) || null; }

function hex(v) {
  const s = String(v || '').trim().toLowerCase();
  let m = /^#([0-9a-f]{3})$/.exec(s);
  if (m) return '#' + m[1].split('').map((c) => c + c).join('');
  m = /^#([0-9a-f]{6})([0-9a-f]{2})?$/.exec(s);
  if (m) return '#' + m[1];
  m = /^rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)/.exec(s);
  if (m) return '#' + [m[1], m[2], m[3]].map((n) => Number(n).toString(16).padStart(2, '0')).join('');
  return null;
}

function colors(blocks) {
  const out = new Set();
  for (const b of blocks) for (const k of ['color', 'background', 'background-color', 'border-color']) {
    for (const tok of String(b.props[k] || '').match(/#[0-9a-f]{3,8}\b|rgba?\([^)]*\)/gi) || []) { const h = hex(tok); if (h) out.add(h); }
  }
  return [...out];
}

function pngSize(buf) {
  if (buf.length < 24 || buf.toString('ascii', 1, 4) !== 'PNG') return null;
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}

module.exports = { parse, family, px, weight, hex, colors, pngSize };
