(async (options) => {
  // next-theme-design capture script, version 0.1.0.
  //
  // Runs inside a rendered page and records what the page shows at the current
  // viewport: page identity, element boxes, computed styles, visible text, CSS
  // animations and their keyframes, media state, images, links, and fixed or
  // sticky elements. It has no dependencies and never navigates, submits a form,
  // or clicks anything, so any browser tool can run it: Playwright, the Chrome
  // DevTools protocol, or the built-in browser of an agent.
  //
  // How to run it:
  //   1. Load the page at the viewport width, height, and device pixel ratio
  //      to record.
  //   2. Assign the options object to globalThis.NEXT_THEME_DESIGN_CAPTURE.
  //      The fields are listed in references/capture.md of next-theme-design.
  //   3. Evaluate this file. It evaluates to a Promise that resolves to a JSON
  //      string with schema next-theme-design/capture-output/v1. Save that
  //      string as the raw output file of the capture.
  //   4. Take the screenshot right after, in the same state, without resizing
  //      or scrolling. A scroll or interaction makes a new capture record.
  //
  // Boxes are page-relative: bounding rect plus scroll offset. A target key is
  // the section id for a section and section_id::element_id for an element
  // inside it, the same keys the assert-geometry.mjs script of next-theme-dev
  // uses.
  //
  // States:
  //   static       finite animations are finished, infinite ones are paused at
  //                their start, and media is paused. Use this for geometry and
  //                screenshots.
  //   motion       nothing is paused; animations are recorded while they run,
  //                with optional timed samples.
  //   interaction  like static, after a scroll, hover, or other interaction the
  //                runner performed and describes in state_detail.
  //   media-frame  like static, after seeking each media_seek entry to its time.
  //
  // A detail the script cannot read, such as a cross-origin stylesheet, motion
  // driven by JavaScript, or media metadata that never loads, is listed in the
  // gaps array. One unavailable detail never stops the rest of the capture.
  //
  // Comments in this file avoid quote characters on purpose: some browser
  // tools scan the source for string delimiters to decide how to wrap it.
  const SCHEMA = 'next-theme-design/capture-output/v1';
  const SCRIPT_VERSION = '0.1.0';
  const DEFAULT_STYLES = [
    'display', 'position', 'top', 'right', 'bottom', 'left', 'z-index',
    'width', 'height', 'max-width',
    'margin-top', 'margin-right', 'margin-bottom', 'margin-left',
    'padding-top', 'padding-right', 'padding-bottom', 'padding-left',
    'gap', 'row-gap', 'column-gap', 'flex-direction', 'justify-content',
    'align-items', 'order', 'grid-template-columns',
    'font-family', 'font-size', 'font-weight', 'font-style', 'line-height',
    'letter-spacing', 'text-transform', 'text-align', 'color',
    'background-color', 'background-image',
    'border-top-width', 'border-top-style', 'border-top-color', 'border-radius',
    'box-shadow', 'opacity', 'transform', 'visibility',
    'animation-name', 'animation-duration', 'animation-delay',
    'animation-iteration-count', 'animation-timing-function',
  ];
  const opts = Object.assign({
    capture_id: '',
    route_id: '',
    viewport: '',
    state: 'static',
    state_detail: '',
    targets: [],
    styles: DEFAULT_STYLES,
    wait_ms: 5000,
    preload_scroll: false,
    media_seek: [],
    sample_count: 0,
    sample_interval_ms: 250,
    text_limit: 2000,
    value_limit: 500,
    inventory: true,
    max_links: 300,
    max_images: 200,
    max_elements_scanned: 6000,
  }, options || {});
  const STATES = ['static', 'motion', 'interaction', 'media-frame'];
  const gaps = [];
  const started = Date.now();

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const round = (value) => Math.round(value * 100) / 100;
  const clip = (value, limit) => {
    const text = String(value == null ? '' : value);
    return text.length > limit ? `${text.slice(0, limit)}…` : text;
  };
  const withTimeout = (promise, ms) => Promise.race([
    promise.then(() => 'ready', () => 'error'),
    sleep(ms).then(() => 'timeout'),
  ]);

  if (!STATES.includes(opts.state)) {
    gaps.push(`unknown state "${opts.state}"; recorded as given`);
  }

  // SHA-256 of the rendered markup, computed before this script changes
  // anything, so the static and motion records of one load share a hash.
  // Kept to the first 16 hex digits (64 bits): enough to tell whether the
  // markup changed between runs. It says nothing about external styles or
  // media, which can change without the markup changing.
  function sha256Hex(text) {
    const bytes = new TextEncoder().encode(text);
    const K = new Uint32Array([
      0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
      0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
      0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
      0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
      0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
      0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
      0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
      0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    ]);
    const H = new Uint32Array([
      0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ]);
    const bitLength = bytes.length * 8;
    const paddedLength = Math.ceil((bytes.length + 9) / 64) * 64;
    const data = new Uint8Array(paddedLength);
    data.set(bytes);
    data[bytes.length] = 0x80;
    const view = new DataView(data.buffer);
    view.setUint32(paddedLength - 8, Math.floor(bitLength / 0x100000000));
    view.setUint32(paddedLength - 4, bitLength >>> 0);
    const W = new Uint32Array(64);
    const rotr = (x, n) => (x >>> n) | (x << (32 - n));
    for (let offset = 0; offset < paddedLength; offset += 64) {
      for (let i = 0; i < 16; i += 1) W[i] = view.getUint32(offset + i * 4);
      for (let i = 16; i < 64; i += 1) {
        const s0 = rotr(W[i - 15], 7) ^ rotr(W[i - 15], 18) ^ (W[i - 15] >>> 3);
        const s1 = rotr(W[i - 2], 17) ^ rotr(W[i - 2], 19) ^ (W[i - 2] >>> 10);
        W[i] = (W[i - 16] + s0 + W[i - 7] + s1) >>> 0;
      }
      let [a, b, c, d, e, f, g, h] = H;
      for (let i = 0; i < 64; i += 1) {
        const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        const ch = (e & f) ^ (~e & g);
        const t1 = (h + S1 + ch + K[i] + W[i]) >>> 0;
        const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const t2 = (S0 + maj) >>> 0;
        h = g; g = f; f = e; e = (d + t1) >>> 0;
        d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      H[0] = (H[0] + a) >>> 0; H[1] = (H[1] + b) >>> 0; H[2] = (H[2] + c) >>> 0; H[3] = (H[3] + d) >>> 0;
      H[4] = (H[4] + e) >>> 0; H[5] = (H[5] + f) >>> 0; H[6] = (H[6] + g) >>> 0; H[7] = (H[7] + h) >>> 0;
    }
    return Array.from(H, (word) => word.toString(16).padStart(8, '0')).join('');
  }

  const htmlHash = sha256Hex(document.documentElement.outerHTML).slice(0, 16);

  function box(el) {
    const rect = el.getBoundingClientRect();
    return {
      x: round(rect.left + window.scrollX),
      y: round(rect.top + window.scrollY),
      width: round(rect.width),
      height: round(rect.height),
    };
  }

  function cssEscape(value) {
    return typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(value) : String(value).replace(/[^\w-]/g, '\\$&');
  }

  // A readable selector path for an element. It is a hint for the package
  // author, not a guaranteed-unique selector.
  function hint(el) {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return `#${cssEscape(el.id)}`;
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.documentElement && parts.length < 6) {
      if (node.id) {
        parts.unshift(`#${cssEscape(node.id)}`);
        break;
      }
      let part = node.tagName.toLowerCase();
      const classes = Array.from(node.classList)
        .filter((name) => /^[A-Za-z_-][\w-]*$/.test(name))
        .slice(0, 2);
      if (classes.length) part += `.${classes.map(cssEscape).join('.')}`;
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((child) => child.tagName === node.tagName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(' > ');
  }

  function isVisible(el) {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none';
  }

  function styleValues(el, properties) {
    const computed = getComputedStyle(el);
    const values = {};
    for (const property of properties) {
      values[property] = clip(computed.getPropertyValue(property), opts.value_limit);
    }
    return values;
  }

  function accessibleName(el) {
    const label = el.getAttribute('aria-label');
    if (label) return label.trim();
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const text = labelledBy.split(/\s+/)
        .map((id) => document.getElementById(id))
        .filter(Boolean)
        .map((node) => node.textContent.trim())
        .join(' ');
      if (text) return text;
    }
    return (el.getAttribute('alt') || el.getAttribute('title') || '').trim();
  }

  // 1. Readiness: fonts, images, and media metadata, each with a timeout that
  // is recorded rather than treated as a failure.
  if (opts.preload_scroll) {
    const startX = window.scrollX;
    const startY = window.scrollY;
    const step = Math.max(200, Math.floor(window.innerHeight * 0.8));
    for (let y = 0; y < document.documentElement.scrollHeight; y += step) {
      window.scrollTo(0, y);
      await sleep(120);
    }
    window.scrollTo(startX, startY);
    await sleep(200);
  }

  const readiness = {};
  const fontsStart = Date.now();
  if (document.fonts && document.fonts.ready) {
    readiness.fonts = { status: await withTimeout(document.fonts.ready, opts.wait_ms), waited_ms: Date.now() - fontsStart };
  } else {
    readiness.fonts = { status: 'unsupported', waited_ms: 0 };
  }
  if (readiness.fonts.status !== 'ready') gaps.push(`fonts: ${readiness.fonts.status} after ${readiness.fonts.waited_ms} ms`);

  const images = Array.from(document.images);
  const imageStart = Date.now();
  const imagePending = images.filter((img) => !img.complete);
  const imageStatus = await withTimeout(Promise.all(imagePending.map((img) => new Promise((resolve) => {
    img.addEventListener('load', resolve, { once: true });
    img.addEventListener('error', resolve, { once: true });
  }))), opts.wait_ms);
  const stillPending = images.filter((img) => !img.complete).length;
  readiness.images = {
    status: stillPending ? 'timeout' : (imageStatus === 'timeout' ? 'timeout' : 'ready'),
    total: images.length,
    pending: stillPending,
    waited_ms: Date.now() - imageStart,
  };
  if (stillPending) gaps.push(`images: ${stillPending} of ${images.length} not loaded (lazy or blocked)`);

  const mediaElements = Array.from(document.querySelectorAll('video, audio'));
  const mediaStart = Date.now();
  const mediaPending = mediaElements.filter((media) => media.readyState < 1);
  const mediaStatus = mediaElements.length
    ? await withTimeout(Promise.all(mediaPending.map((media) => new Promise((resolve) => {
      media.addEventListener('loadedmetadata', resolve, { once: true });
      media.addEventListener('error', resolve, { once: true });
    }))), opts.wait_ms)
    : 'none';
  const mediaStillPending = mediaElements.filter((media) => media.readyState < 1).length;
  readiness.media_metadata = {
    status: mediaElements.length ? (mediaStillPending ? 'timeout' : mediaStatus) : 'none',
    total: mediaElements.length,
    pending: mediaStillPending,
    waited_ms: Date.now() - mediaStart,
  };
  if (mediaStillPending) gaps.push(`media metadata: ${mediaStillPending} of ${mediaElements.length} not loaded`);

  // 2. Animations, recorded before anything is paused so their running
  // timing is what the record shows.
  const targetElements = [];
  const targets = {};
  for (const target of opts.targets || []) {
    const entry = { selector: target.selector, count: 0, found: false };
    targets[target.key] = entry;
    let nodes = [];
    try {
      nodes = Array.from(document.querySelectorAll(target.selector));
    } catch (error) {
      entry.error = String(error && error.message || error);
      gaps.push(`target ${target.key}: invalid selector`);
      continue;
    }
    entry.count = nodes.length;
    if (target.all) {
      entry.found = nodes.length > 0;
      entry.nodes = nodes;
    } else if (nodes.length === 1) {
      entry.found = true;
      entry.nodes = nodes;
    } else {
      gaps.push(`target ${target.key}: selector matched ${nodes.length} elements; it must match exactly one`);
      entry.nodes = [];
    }
    for (const node of entry.nodes) targetElements.push({ key: target.key, node });
  }

  function targetKeyFor(el) {
    let best = null;
    for (const candidate of targetElements) {
      if (candidate.node === el || candidate.node.contains(el)) {
        if (!best || best.node.contains(candidate.node)) best = candidate;
      }
    }
    return best ? best.key : null;
  }

  function animationRecord(animation) {
    const effect = animation.effect;
    const target = effect && effect.target;
    const timing = effect && effect.getTiming ? effect.getTiming() : {};
    const computed = effect && effect.getComputedTiming ? effect.getComputedTiming() : {};
    let keyframes = [];
    try {
      keyframes = effect && effect.getKeyframes ? effect.getKeyframes().slice(0, 24) : [];
    } catch (error) {
      gaps.push(`animation ${animation.animationName || animation.id || ''}: keyframes unreadable`);
    }
    return {
      type: animation.constructor ? animation.constructor.name : 'Animation',
      name: animation.animationName || animation.transitionProperty || animation.id || '',
      target_hint: target ? hint(target) : '',
      pseudo_element: effect && effect.pseudoElement ? effect.pseudoElement : null,
      target_key: target ? targetKeyFor(target) : null,
      duration_ms: typeof timing.duration === 'number' ? round(timing.duration) : timing.duration,
      delay_ms: round(timing.delay || 0),
      iterations: timing.iterations === Infinity ? 'infinite' : timing.iterations,
      direction: timing.direction,
      easing: timing.easing,
      fill: timing.fill,
      play_state: animation.playState,
      current_time_ms: animation.currentTime == null ? null : round(Number(animation.currentTime)),
      end_time_ms: computed.endTime === Infinity ? 'infinite' : computed.endTime,
      keyframes,
    };
  }

  let animations = [];
  if (document.getAnimations) {
    animations = document.getAnimations();
  } else {
    gaps.push('document.getAnimations is unavailable; animation timing not recorded');
  }
  const animationRecords = animations.map(animationRecord);
  if (!animations.length) {
    const animatedByStyle = Array.from(document.querySelectorAll('*'))
      .slice(0, opts.max_elements_scanned)
      .filter((el) => getComputedStyle(el).animationName !== 'none');
    if (animatedByStyle.length) gaps.push('elements declare animation-name but no running animation was found');
  }

  // Keyframe rules from readable stylesheets, so animations that are not
  // running right now (hover or scroll triggered) are still on record.
  const keyframeRules = {};
  const unreadableStylesheets = [];
  function collectRules(rules) {
    for (const rule of Array.from(rules || [])) {
      if (typeof CSSKeyframesRule !== 'undefined' && rule instanceof CSSKeyframesRule) {
        keyframeRules[rule.name] = Array.from(rule.cssRules).map((frame) => ({
          key: frame.keyText,
          style: clip(frame.style.cssText, opts.value_limit),
        }));
      } else if (rule.cssRules) {
        collectRules(rule.cssRules);
      }
    }
  }
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      collectRules(sheet.cssRules);
    } catch (error) {
      unreadableStylesheets.push(sheet.href || '(inline)');
    }
  }
  if (unreadableStylesheets.length) {
    gaps.push(`${unreadableStylesheets.length} stylesheet(s) not readable from the page (cross-origin); running animations still report keyframes`);
  }

  // Motion samples: computed transform and opacity of animated elements at
  // timed intervals while motion runs.
  const samples = [];
  if (opts.state === 'motion' && opts.sample_count > 0) {
    const sampled = animations
      .map((animation) => animation.effect && animation.effect.target)
      .filter(Boolean)
      .slice(0, 24);
    for (let index = 0; index < opts.sample_count; index += 1) {
      samples.push({
        t_ms: Date.now() - started,
        elements: sampled.map((el) => {
          const computed = getComputedStyle(el);
          return { hint: hint(el), transform: computed.transform, opacity: computed.opacity };
        }),
      });
      if (index < opts.sample_count - 1) await sleep(opts.sample_interval_ms);
    }
  }

  // 3. Settle the page for a static record: finish finite animations, hold
  // infinite ones at their start, pause media. Motion records leave it alone.
  let motionMode = animations.length ? 'running' : 'none';
  const mediaFrame = [];
  if (opts.state !== 'motion') {
    let settled = 0;
    for (const animation of animations) {
      try {
        const computed = animation.effect && animation.effect.getComputedTiming
          ? animation.effect.getComputedTiming()
          : null;
        if (computed && Number.isFinite(computed.endTime)) {
          animation.finish();
        } else {
          animation.pause();
          animation.currentTime = 0;
        }
        settled += 1;
      } catch (error) {
        gaps.push(`animation ${animation.animationName || ''}: could not be paused`);
      }
    }
    if (animations.length) motionMode = settled === animations.length ? 'paused' : 'partly-paused';
    for (const media of mediaElements) {
      try { media.pause(); } catch (error) { /* recorded below as paused=false */ }
    }
    for (const seek of opts.media_seek || []) {
      const media = document.querySelector(seek.selector);
      if (!media || typeof media.currentTime !== 'number') {
        gaps.push(`media_seek ${seek.selector}: no media element`);
        continue;
      }
      const seeked = new Promise((resolve) => media.addEventListener('seeked', resolve, { once: true }));
      media.pause();
      media.currentTime = Number(seek.time_s);
      const status = await withTimeout(seeked, opts.wait_ms);
      if (status !== 'ready') gaps.push(`media_seek ${seek.selector}: seek to ${seek.time_s}s ${status}`);
      mediaFrame.push({
        selector: seek.selector,
        media_url: media.currentSrc || media.src || '',
        requested_time_s: Number(seek.time_s),
        time_s: round(media.currentTime),
        status,
      });
    }
    // Two frames so the paused state is what gets painted.
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }

  // 4. Measurements, all in the settled state the screenshot will show.
  const boxes = {};
  const styleList = Array.isArray(opts.styles) ? opts.styles : DEFAULT_STYLES;
  for (const [key, entry] of Object.entries(targets)) {
    const nodes = entry.nodes || [];
    delete entry.nodes;
    const describe = (node) => ({
      tag: node.tagName.toLowerCase(),
      hint: hint(node),
      box: box(node),
      visible: isVisible(node),
      text: clip((node.innerText || '').replace(/[ \t]+\n/g, '\n').trim(), opts.text_limit),
      accessible_name: clip(accessibleName(node), opts.value_limit),
      href: node.closest && node.closest('a[href]') ? node.closest('a[href]').href : null,
      styles: styleValues(node, styleList),
    });
    if (!entry.found) continue;
    if (nodes.length === 1 && !((opts.targets || []).find((target) => target.key === key) || {}).all) {
      Object.assign(entry, describe(nodes[0]));
      // A hidden match (display none, zero size) has no layout at this width;
      // leaving it out of boxes keeps a zero box out of the geometry.
      if (entry.visible) {
        boxes[key] = entry.box;
      } else {
        gaps.push(`target ${key}: matched an element that is hidden at this viewport`);
      }
    } else {
      entry.matches = nodes.slice(0, 50).map(describe);
      // A list target with no visible match has no layout at this width either.
      if (!nodes.some((node) => isVisible(node))) {
        gaps.push(`target ${key}: all ${nodes.length} matches are hidden at this viewport`);
      }
    }
  }

  const inventory = [];
  if (opts.inventory) {
    const seen = new Set();
    const candidates = Array.from(document.querySelectorAll(
      'body > *, main > *, header, footer, section, [data-section], [class*="section"], .e-con.e-parent',
    ));
    for (const el of candidates) {
      if (seen.has(el) || !isVisible(el)) continue;
      seen.add(el);
      const b = box(el);
      if (b.height < 30 || b.width < window.innerWidth * 0.5) continue;
      const heading = el.querySelector('h1, h2, h3, h4');
      inventory.push({
        hint: hint(el),
        tag: el.tagName.toLowerCase(),
        id: el.id || '',
        classes: Array.from(el.classList).slice(0, 4),
        box: b,
        heading: heading ? clip(heading.innerText.trim(), 120) : '',
        text_preview: clip((el.innerText || '').trim().replace(/\s+/g, ' '), 160),
        target_key: targetKeyFor(el),
      });
      if (inventory.length >= 120) break;
    }
    inventory.sort((a, b) => a.box.y - b.box.y || a.box.x - b.box.x);
  }

  const media = mediaElements.map((el) => ({
    tag: el.tagName.toLowerCase(),
    hint: hint(el),
    target_key: targetKeyFor(el),
    current_src: el.currentSrc || el.src || '',
    sources: Array.from(el.querySelectorAll('source')).map((source) => ({ src: source.src, type: source.type })),
    poster: el.poster || '',
    box: box(el),
    duration_s: Number.isFinite(el.duration) ? round(el.duration) : (el.duration === Infinity ? 'infinite' : null),
    autoplay: el.autoplay,
    loop: el.loop,
    muted: el.muted,
    default_muted: el.defaultMuted,
    plays_inline: el.playsInline === undefined ? null : el.playsInline,
    controls: el.controls,
    paused: el.paused,
    current_time_s: round(el.currentTime || 0),
    ready_state: el.readyState,
    video_width: el.videoWidth || null,
    video_height: el.videoHeight || null,
  }));
  const frames = Array.from(document.querySelectorAll('iframe')).map((el) => ({
    hint: hint(el),
    target_key: targetKeyFor(el),
    src: el.src || '',
    title: el.title || '',
    box: box(el),
  }));
  if (frames.length) gaps.push(`${frames.length} iframe(s): embedded content (for example a hosted video player) is not measured inside the frame`);

  const imageRecords = images.slice(0, opts.max_images).map((img) => ({
    hint: hint(img),
    target_key: targetKeyFor(img),
    src: img.getAttribute('src') || '',
    current_src: img.currentSrc || img.src || '',
    alt: img.getAttribute('alt'),
    natural_width: img.naturalWidth,
    natural_height: img.naturalHeight,
    box: box(img),
    loading: img.loading || '',
    complete: img.complete,
    visible: isVisible(img),
  }));

  const backgroundImages = [];
  for (const { key, node } of targetElements) {
    const scope = [node, ...Array.from(node.querySelectorAll('*')).slice(0, 400)];
    for (const el of scope) {
      const value = getComputedStyle(el).backgroundImage;
      if (value && value !== 'none') {
        backgroundImages.push({ hint: hint(el), target_key: key, value: clip(value, opts.value_limit), box: box(el) });
      }
    }
  }

  const links = Array.from(document.querySelectorAll('a[href], button, [role="button"], input[type="submit"]'))
    .filter(isVisible)
    .slice(0, opts.max_links)
    .map((el) => ({
      tag: el.tagName.toLowerCase(),
      hint: hint(el),
      target_key: targetKeyFor(el),
      text: clip((el.innerText || el.value || '').trim().replace(/\s+/g, ' '), 200),
      accessible_name: clip(accessibleName(el), 200),
      href: el.href || (el.closest('a[href]') ? el.closest('a[href]').href : null),
      box: box(el),
    }));

  const fixedElements = [];
  const allElements = Array.from(document.body ? document.body.querySelectorAll('*') : []);
  if (allElements.length > opts.max_elements_scanned) {
    gaps.push(`fixed/sticky scan stopped after ${opts.max_elements_scanned} of ${allElements.length} elements`);
  }
  for (const el of allElements.slice(0, opts.max_elements_scanned)) {
    const computed = getComputedStyle(el);
    if (computed.position === 'fixed' || computed.position === 'sticky') {
      fixedElements.push({
        hint: hint(el),
        target_key: targetKeyFor(el),
        position: computed.position,
        top: computed.top,
        bottom: computed.bottom,
        z_index: computed.zIndex,
        box: box(el),
        visible: isVisible(el),
      });
    }
  }

  const fonts = [];
  if (document.fonts) {
    document.fonts.forEach((face) => {
      fonts.push({ family: face.family, weight: face.weight, style: face.style, status: face.status });
    });
  }

  return JSON.stringify({
    schema_version: SCHEMA,
    script_version: SCRIPT_VERSION,
    capture: {
      capture_id: opts.capture_id,
      route_id: opts.route_id,
      viewport: opts.viewport,
      state: opts.state,
      state_detail: opts.state_detail,
    },
    page: {
      url: location.href,
      title: document.title,
      lang: document.documentElement.lang || '',
      viewport_width: window.innerWidth,
      viewport_height: window.innerHeight,
      device_pixel_ratio: window.devicePixelRatio,
      scroll_x: round(window.scrollX),
      scroll_y: round(window.scrollY),
      document_width: document.documentElement.scrollWidth,
      document_height: document.documentElement.scrollHeight,
      // Four-part build numbers are shortened to the major version, which is
      // enough to identify the browser and keeps records free of long digit runs.
      browser: navigator.userAgent.replace(/(\d+)\.\d+\.\d+\.\d+/g, '$1'),
      captured_at: new Date().toISOString(),
      html_sha256: htmlHash,
    },
    readiness,
    motion: { mode: motionMode, animation_count: animations.length },
    media_frame: mediaFrame,
    targets,
    boxes,
    inventory,
    animations: animationRecords,
    keyframe_rules: keyframeRules,
    unreadable_stylesheets: unreadableStylesheets,
    samples,
    media,
    iframes: frames,
    images: imageRecords,
    background_images: backgroundImages,
    links,
    fixed_elements: fixedElements,
    fonts,
    gaps,
    elapsed_ms: Date.now() - started,
  });
})(globalThis.NEXT_THEME_DESIGN_CAPTURE);
