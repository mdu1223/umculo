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
    await p.click('#tab-foryou');
    await p.waitForSelector('.tile, .weekly', { timeout: 15000 });
    await p.waitForTimeout(800);
    await p.screenshot({ path: `${out}/f1-foryou-${scheme}.png` });
    if (scheme === 'light') { await ctx.close(); continue; }
    await p.screenshot({ path: `${out}/f1b-foryou-full.png`, fullPage: true });
    await p.click('button:text-is("Songs")');
    await p.waitForTimeout(500);
    await p.screenshot({ path: `${out}/f2-weekly.png` });
    await p.click('#sheet .icon >> nth=0');
    await p.click('.gear');
    await p.waitForSelector('.opt-card');
    await p.screenshot({ path: `${out}/f3-settings.png` });
    await p.screenshot({ path: `${out}/f3b-settings-full.png`, fullPage: false });
    await p.evaluate(() => document.querySelector('#panel').scrollTo(0, 2000));
    await p.screenshot({ path: `${out}/f3c-settings-bottom.png` });
    await p.click('button.free:has-text("Library care")');
    await p.waitForTimeout(800);
    await p.click('text=Check my library');
    await p.waitForTimeout(4000);
    await p.screenshot({ path: `${out}/f4-library.png` });
    await p.click('#sheet .icon >> nth=0');
    await p.click('#tab-playlists');
    await p.waitForTimeout(1000);
    await p.screenshot({ path: `${out}/f5-playlists.png` });
    await ctx.close();
  }
  await b.close();
})();
