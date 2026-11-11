/* Measure the real header in a real browser, and print the numbers as JSON.
 *
 * test_header_alignment.py boots the app and runs this against it. Keeping the harness
 * in the repository rather than in /tmp matters: the sandbox's /tmp is wiped between
 * sessions, so a copy stored there disappears and the layout tests silently fall back
 * to SKIP -- which reads as "not proven", not "proven".
 *
 * Usage: node tests/chrome_harness.js <base-url> <chromium-executable>
 * Extra widths can be added to WIDTHS; every number the Python test asserts on is in
 * the JSON, so a failure message can quote the measurement rather than a guess.
 */
const path = require('path');

function puppeteer() {
  // puppeteer-core is installed beside the harness (npm) or in a scratch directory.
  const candidates = [
    process.env.STORYROOM_PUPPETEER,
    'puppeteer-core',
    '/tmp/node_modules/puppeteer-core',
    path.join(__dirname, '..', 'node_modules', 'puppeteer-core'),
  ].filter(Boolean);
  for (const c of candidates) {
    try {
      return require(c);
    } catch (e) {
      /* try the next one */
    }
  }
  throw new Error('puppeteer-core is not installed; run npm i puppeteer-core');
}

const WIDTHS = [1900, 1600, 1500, 1400, 1300, 1200, 1100, 1000, 960, 940, 900, 861, 860, 760];

(async () => {
  const base = process.argv[2];
  const chrome = process.argv[3];
  if (!base || !chrome) {
    console.error('usage: node tests/chrome_harness.js <base-url> <chromium>');
    process.exit(2);
  }
  const pptr = puppeteer();
  const browser = await pptr.launch({
    executablePath: chrome,
    args: ['--no-sandbox', '--disable-gpu', '--font-render-hinting=none'],
    headless: 'shell',
  });
  const report = [];

  for (const width of WIDTHS) {
    const page = await browser.newPage();
    await page.setViewport({ width, height: 900 });
    await page.goto(base + '/?production=demo', { waitUntil: 'networkidle2' });
    await new Promise((r) => setTimeout(r, 700));

    const row = await page.evaluate(() => {
      const R = (el) => el.getBoundingClientRect();
      const q = (s) => document.querySelector(s);
      const bar = q('.topbar');
      const head = q('.topbar>.project-heading');
      const name = q('#project-name');
      const play = q('#play-button');
      const monitor = q('#monitor');
      const tools = q('.head-tools');
      const actions = q('.topbar>.top-actions');
      const brand = q('.brand');

      // The name is shown only if its container is both rendered and visible; hiding
      // it is the documented last resort below 940px, not a failure.
      const nameShown = !!head && getComputedStyle(head).visibility !== 'hidden' &&
        R(head).width > 0;
      const hr = head ? R(head) : { left: 0, width: 0 };
      const buttons = {};
      const buttonRows = [];
      let rightEdge = 0;
      let leftmost = Infinity;
      if (actions) {
        for (const b of actions.querySelectorAll('.button')) {
          const r = R(b);
          if (r.width === 0 && r.height === 0) continue; // hidden: documentary tools
          buttons[b.textContent.trim()] = true;
          rightEdge = Math.max(rightEdge, r.right);
          leftmost = Math.min(leftmost, r.left);
          buttonRows.push(+((r.top + r.height / 2).toFixed(1)));
        }
      }
      const centre = (el) => {
        const r = R(el);
        return +((r.top + r.height / 2).toFixed(1));
      };
      // Only these two are grid children; brand and tools are flex children inside
      // .head-left, so "auto" is their correct grid-row.
      const gridRow = (el) => (el ? getComputedStyle(el).gridRowStart : null);

      const toolRight = tools ? R(tools).right : 0;
      const nameLeft = nameShown ? R(name).left : null;
      const nameBox = name ? R(name) : { width: 0 };
      return {
        width: innerWidth,
        layout: nameShown ? 'centred' : 'compact',
        bar_display: getComputedStyle(bar).display,
        grid_template_rows: getComputedStyle(bar).gridTemplateRows,
        name_grid_row: gridRow(head),
        actions_grid_row: gridRow(actions),
        name_centre: +(hr.left + hr.width / 2).toFixed(1),
        play_centre: +(R(play).left + R(play).width / 2).toFixed(1),
        monitor_centre: +(R(monitor).left + R(monitor).width / 2).toFixed(1),
        // Every header group must share one vertical centre: that is the "some are up,
        // some down, another in the middle" complaint, expressed as a number.
        group_centres: [brand, tools, head, actions].filter(Boolean).map(centre),
        // Every visible action button must share one centre line too.
        button_centres: buttonRows,
        button_y: buttonRows.length ? Math.min(...buttonRows) : null,
        button_rows: new Set(buttonRows).size,
        tools_left: +toolRight.toFixed(1),
        gap_after_tools: nameShown ? +(nameLeft - toolRight).toFixed(1) : 9999,
        clearance: nameShown ? +(leftmost - (nameLeft + nameBox.width)).toFixed(1) : 9999,
        name_width: +nameBox.width.toFixed(1),
        name_visible: nameShown,
        overflow: document.documentElement.scrollWidth > innerWidth + 1 ||
          rightEdge > innerWidth + 1,
        right_edge: +rightEdge.toFixed(1),
        buttons,
        // Media pool: how many tiles share a row. Reported as null when the production
        // has no media, so the Python test can say "not measurable here" instead of
        // reading an empty pool as a layout failure. The tiles are rendered by
        // poolTileHTML in editor.js as .pool-tile.
        pool: (() => {
          const tiles = [...document.querySelectorAll('.pool-tile')];
          const tops = {};
          for (const t of tiles) {
            const r = R(t);
            if (!r.width) continue;
            const key = Math.round(r.top);
            tops[key] = (tops[key] || 0) + 1;
          }
          const perRow = Object.keys(tops).sort((a, b) => a - b).map((k) => tops[k]);
          if (!perRow.length) return { columns: null, per_row: [], rows: 0, tiles: 0 };
          return {
            columns: perRow[0],
            per_row: perRow,
            rows: perRow.length,
            tiles: perRow.reduce((a, b) => a + b, 0),
          };
        })(),
      };
    });
    report.push(row);
    await page.close();
  }

  await browser.close();
  process.stdout.write(JSON.stringify(report));
})().catch((err) => {
  console.error(String((err && err.stack) || err));
  process.exit(1);
});
