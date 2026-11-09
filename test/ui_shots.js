const { chromium, devices } = require('playwright');
(async () => {
  const out = process.argv[2];
  const b = await chromium.launch();
  for (const scheme of ['light', 'dark']) {
    const ctx = await b.newContext({ ...devices['iPhone 13'], colorScheme: scheme, httpCredentials: { username: 'admin', password: 'pw' } });
    const p = await ctx.newPage();
    p.on('pageerror', e => console.log('PAGE ERROR', e.message));
    await p.goto('http://localhost:8095/');
    await p.fill('#q', 'machine gun');
    await p.click('button[type=submit]');
    await p.waitForSelector('.card .btn');
    await p.screenshot({ path: `${out}/1-search-${scheme}.png` });
    await p.click('.card .btn >> nth=0');
    await p.waitForSelector('.row', { timeout: 15000 });
    await p.waitForFunction(() => !document.querySelector('.src .spin'), null, { timeout: 20000 });
    await p.screenshot({ path: `${out}/2-availability-${scheme}.png` });
    if (scheme === 'light') {
      await p.click('text=Get best available');
      await p.waitForTimeout(500);
      await p.click('#tab-requests');
      await p.waitForSelector('.req');
      await p.waitForTimeout(9000);
      await p.screenshot({ path: `${out}/3-requests-${scheme}.png` });
    }
    await ctx.close();
  }
  await b.close();
})();
