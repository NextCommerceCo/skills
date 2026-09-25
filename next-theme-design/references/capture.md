# Capture Script Reference

`scripts/capture-page.js` runs inside a rendered page and records what the
page shows at the current viewport. It has no dependencies. It never
navigates, submits a form, or clicks. The skill bundles no browser
automation: run the script with whatever browser tool is available.

## Running it

1. Open the page at the exact viewport width and height and the device pixel
   ratio you want to record.
2. Wait for the page to settle (load event, then a short pause).
3. Assign the options object to `globalThis.NEXT_THEME_DESIGN_CAPTURE`.
4. Evaluate the file's contents. The result is a Promise that resolves to a
   JSON string. Save it as `<capture_id>.json`.
5. Take the screenshot immediately, in the same state, without resizing or
   scrolling: a full-page PNG at device-pixel resolution.

Example with Playwright (any tool with the same abilities works):

```js
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const page = await context.newPage();
await page.goto(url, { waitUntil: 'load' });
await page.waitForTimeout(800);
await page.evaluate((options) => { globalThis.NEXT_THEME_DESIGN_CAPTURE = options; }, {
  capture_id: 'home-desktop-static', route_id: 'home', viewport: 'desktop',
  state: 'static', preload_scroll: true, targets,
});
const raw = await page.evaluate(fs.readFileSync('<design-dir>/scripts/capture-page.js', 'utf8'));
fs.writeFileSync('home-desktop-static.json', raw);
await page.screenshot({ path: 'home-desktop-static.png', fullPage: true, scale: 'device' });
```

Headless Chromium blocks autoplay unless launched with
`--autoplay-policy=no-user-gesture-required`; say in `notes.md` which you
used. Check each screenshot's pixel width before filing it: some tools
downscale tall full-page screenshots, and `design-package.py record` refuses
a screenshot whose width is not viewport width times device pixel ratio.

The script starts with `(` and its comments contain no quote characters, so
tools that decide how to wrap evaluated code by scanning it for string
delimiters still return its result.

## Options

| Option | Default | Meaning |
|---|---|---|
| `capture_id` | `""` | Lowercase id; becomes the file names and the record id. |
| `route_id` | `""` | The route this page is in `routes.json`. |
| `viewport` | `""` | `desktop` (1440), `tablet` (768), or `mobile` (390). |
| `state` | `static` | `static`, `motion`, `interaction`, or `media-frame` (see below). |
| `state_detail` | `""` | What you did before an `interaction` or `media-frame` capture. |
| `targets` | `[]` | `{ "key", "selector" }` list. Keys are `<section_id>` or `<section_id>::<element_id>`. Add `"all": true` to record every match of a repeated element. |
| `styles` | common set | Computed style properties to record per target. |
| `wait_ms` | 5000 | Timeout for fonts, images, media metadata, and seeks. A timeout is recorded, not fatal. |
| `preload_scroll` | false | Scroll through the page and back first, so lazy images load. |
| `media_seek` | `[]` | `{ "selector", "time_s" }` list: pause and seek each media element before measuring. |
| `sample_count` | 0 | In `motion` state, how many timed samples of animated elements to take. |
| `sample_interval_ms` | 250 | Time between motion samples. |

States:

- `static`: finite animations are finished, infinite ones are held at their
  start, and media is paused. Use it for geometry and reference screenshots.
- `motion`: nothing is paused. Animations are recorded while they run.
- `interaction`: like static, after you scrolled, hovered, or opened
  something. Describe it in `state_detail`.
- `media-frame`: like static, after seeking the `media_seek` elements.

## Output

Schema `next-theme-design/capture-output/v1`:

| Field | Holds |
|---|---|
| `capture` | The options' id, route, viewport, state, and detail. |
| `page` | URL, title, viewport width and height, device pixel ratio, scroll offset, document size, browser (build numbers shortened to the major version), `captured_at`, and `html_sha256` (first 16 hex digits of the SHA-256 of the markup, taken before the script changes anything). |
| `readiness` | Fonts, images, and media metadata: `ready` or `timeout`, with counts. |
| `motion` | `paused`, `running`, `partly-paused`, or `none`, and the animation count. |
| `media_frame` | For each seek: the media URL, requested and actual time, and status. |
| `targets` | Per key: selector, match count, tag, page-relative box, visibility, visible text, accessible name, link, and computed styles. Visibility is a boolean `visible` on a single target and on each `matches[]` entry of a list target; `design-package.py geometry` reads it to explain a section with no box. |
| `boxes` | Page-relative boxes of every target that matched exactly one node. `record` copies these into the capture record. |
| `inventory` | Candidate page sections: selector hint, tag, classes, box, first heading, and text preview. |
| `animations` | Every running animation or transition: name, target, duration, delay, iterations, direction, easing, fill, play state, current time, and keyframes. |
| `keyframe_rules` | `@keyframes` rules from readable stylesheets. |
| `samples` | Timed transform and opacity of animated elements (motion state). |
| `media` | Video and audio: source, poster, box, duration, autoplay, loop, muted, plays-inline, controls, paused, current time, and dimensions. |
| `iframes`, `images`, `background_images`, `links`, `fixed_elements`, `fonts` | Embedded frames, images with natural sizes, CSS background images inside targets, visible links and buttons with destinations, fixed and sticky elements, and loaded font faces. |
| `gaps` | Every detail the script could not read. |
