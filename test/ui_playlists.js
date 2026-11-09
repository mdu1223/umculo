const { chromium, devices } = require('playwright');
(async () => {
  const out = process.argv[2], port = process.argv[3];
  const b = await chromium.launch();
  for (const scheme of ['dark', 'light']) {
    const ctx = await b.newContext({ ...devices['iPhone 13'], colorScheme: scheme });
    const p = await ctx.newPage();
    p.on('pageerror', e => console.log('PAGE ERROR', e.message));
    p.on('dialog', d => d.accept());
    await p.goto(`http://localhost:${port}/`);
    await p.click('#tab-playlists');
    await p.waitForSelector('#pl-local .box');
    await p.screenshot({ path: `${out}/pl-1-${scheme}.png` });
    if (scheme === 'dark') {
      await p.click('text=Upgrade all');
      await p.waitForTimeout(25000);
      await p.screenshot({ path: `${out}/pl-2-jobs.png` });
      await p.click('text=Details >> nth=0');
      await p.waitForSelector('.trk');
      await p.screenshot({ path: `${out}/pl-3-details.png` });
    }
    await ctx.close();
  }
  await b.close();
})();
