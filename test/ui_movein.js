const { chromium, devices } = require('playwright');
(async () => {
  const out = process.argv[2], base = process.argv[3], scheme = process.argv[4] || 'dark', user = process.argv[5], pass = process.argv[6];
  const b = await chromium.launch(); const probs = [];
  const ctx = await b.newContext({ ...devices['iPhone 13'], colorScheme: scheme }); const p = await ctx.newPage();
  p.on('pageerror', e => probs.push('PAGE ERROR ' + e.message)); p.on('dialog', d => d.dismiss());
  p.on('response', r => r.url().includes('/api/') && r.status() >= 400 && !/ndcover|\/api\/me$|requests|recipes/.test(r.url()) && probs.push(`HTTP ${r.status()} ${r.url()}`));
  let n = 0; const shot = async name => { await p.waitForTimeout(800); await p.screenshot({ path: `${out}/${String(++n).padStart(2, '0')}-${name}.png` }); };
  await p.goto(base + '/'); await p.waitForTimeout(1000);
  if (user && await p.$('#login:not([hidden])')) { await p.fill('#lg-user', user); await p.fill('#lg-pass', pass); await p.click('#login .btn'); await p.waitForTimeout(2000); }
  await p.click('.gear'); await p.waitForSelector('.stabs'); await shot('tabs');
  await p.click('.stabs button:has-text("Folders")'); await p.waitForSelector('#conn-list'); await shot('folders');
  await p.evaluate(() => { connOpen = 'f_music'; renderConnectors(); }); await p.waitForTimeout(400); await shot('folder-music');
  await p.click('#cb-f_music button:has-text("Browse")'); await p.waitForSelector('.picker'); await shot('picker');
  await p.click('.picker .pk >> nth=0'); await p.waitForTimeout(800); await shot('picker-up');
  await p.click('.stabs button:has-text("Move in")'); await p.waitForSelector('.mstep', {timeout: 20000}); await shot('movein');
  for (const y of [700, 1400, 2400]) { await p.evaluate(y => document.querySelector('#panel').scrollTo(0, y), y); await shot('movein-' + y); }
  console.log(probs.join('\n') || 'no problems'); await b.close();
})();
