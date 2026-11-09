const { chromium, devices } = require('playwright');
(async () => {
  const out = process.argv[2], port = process.argv[3];
  const b = await chromium.launch(); const ctx = await b.newContext({ ...devices['iPhone 13'], colorScheme: 'dark' }); const p = await ctx.newPage();
  const probs = []; p.on('pageerror', e => probs.push('PAGE ERROR ' + e.message));
  p.on('response', r => r.url().includes('/api/') && !r.url().includes('/api/stream') && r.status() >= 400 && probs.push(`HTTP ${r.status()} ${r.url()}`));
  p.on('dialog', d => d.accept());
  const shot = async n => { await p.waitForTimeout(800); await p.screenshot({ path: `${out}/${n}.png` }); };
  await p.goto(`http://localhost:${port}/`);
  await p.fill('#q', 'freaksa'); await p.press('#q', 'Enter'); await p.waitForSelector('.achip', {timeout: 15000}); await shot('a1-search');
  await p.click('.achip'); await p.waitForSelector('.ahead'); await shot('a2-artist');
  await p.click('#panel button:has-text("Follow")'); await p.waitForTimeout(1000); await shot('a3-followed');
  await p.evaluate(() => $('#panel').scrollTo(0, 2000)); await shot('a4-albums');
  await p.click('#panel button:has-text("Get the album")'); await p.waitForTimeout(1500);
  await p.evaluate(() => closeSheet()); await p.click('#tab-requests'); await p.waitForTimeout(1500); await shot('a5-queue');
  await p.click('#tab-foryou'); await p.waitForSelector('.rcard', {timeout: 20000}); await p.waitForTimeout(1000);
  await p.evaluate(() => [...document.querySelectorAll('h2')].find(h => /follow/i.test(h.innerText))?.scrollIntoView()); await shot('a6-foryou');
  await p.click('#tab-playlists'); await p.waitForSelector('#pl-list .sw'); await shot('a7-playlists');
  console.log(probs.join('\n') || 'no problems'); await b.close(); })();
