const { chromium, devices } = require('playwright');
(async () => {
  const out = process.argv[2], base = process.argv[3], scheme = process.argv[4] || 'dark', user = process.argv[5], pass = process.argv[6];
  const b = await chromium.launch({args: ['--autoplay-policy=no-user-gesture-required']}); const probs = [];
  const ctx = await b.newContext({ ...devices['iPhone 13'], colorScheme: scheme }); const p = await ctx.newPage();
  p.on('pageerror', e => probs.push('PAGE ERROR ' + e.message)); p.on('dialog', d => d.dismiss());
  p.on('response', r => r.url().includes('/api/') && r.status() >= 400 && !/ndcover|\/api\/me$|requests|recipes/.test(r.url()) && probs.push(`HTTP ${r.status()} ${r.url()}`));
  let n = 0; const shot = async name => { await p.waitForTimeout(900); await p.screenshot({ path: `${out}/${String(++n).padStart(2, '0')}-${name}.png` }); };
  await p.goto(base + '/'); await p.waitForTimeout(1000);
  if (user && await p.$('#login:not([hidden])')) { await p.fill('#lg-user', user); await p.fill('#lg-pass', pass); await p.click('#login .btn'); await p.waitForTimeout(2000); }
  // player: lyrics + instant mix
  await p.fill('#q', process.argv[7] || 'ngud'); await p.press('#q', 'Enter'); await p.waitForSelector('.srow', {timeout: 15000});
  await p.click('.srow .icon[title=Play] >> nth=0'); await p.waitForTimeout(2500); await p.click('#mini .meta');
  await p.click('#np button:has-text("Lyrics")'); await p.waitForTimeout(2500); await shot('lyrics');
  const lines = await p.$$eval('#np-lyr p', x => x.length); if (!lines) probs.push('no lyric lines');
  await p.click('#np button:has-text("Instant mix")'); await p.waitForTimeout(2500); await shot('instant-mix');
  await p.waitForFunction(() => isFinite($('#player').duration), null, {timeout: 20000}); await p.evaluate(() => { const a = $('#player'); a.currentTime = Math.max(0, a.duration - 1); }); await p.waitForTimeout(3000);
  await p.evaluate(() => closeSheet());
  // song menu
  await p.click('.srow .icon.more >> nth=0'); await shot('menu'); await p.evaluate(() => closeSheet());
  // wrapped
  await p.click('#tab-library'); await p.waitForSelector('.wrap-card', {timeout: 20000}); await shot('library-card');
  await p.click('.wrap-card'); await p.waitForTimeout(1500); await shot('wrapped');
  await p.evaluate(() => document.querySelector('#panel').scrollTo(0, 700)); await shot('wrapped-2');
  await p.evaluate(() => document.querySelector('#panel').scrollTo(0, 1600)); await shot('wrapped-3');
  await p.click('#panel .seg button:has-text("Week")'); await p.waitForTimeout(1200); await shot('wrapped-week');
  await p.evaluate(() => closeSheet());
  // favourite playlists
  await p.click('#tab-playlists'); await p.waitForSelector('#pl-list .sw', {timeout: 15000}); await p.click('#pl-list .icon.star >> nth=0'); await p.waitForTimeout(1500); await shot('playlist-fav');
  // move in sources
  await p.click('.gear'); await p.click('.stabs button:has-text("Move in")'); await p.waitForSelector('.mstep', {timeout: 20000});
  for (const t of ['Jellyfin', 'Plex', 'iTunes']) { await p.click(`.mstep .seg button:has-text("${t}")`); await p.waitForTimeout(400);
    await p.evaluate(() => document.querySelector('#mv-who')?.scrollIntoView({block: 'center'})); await shot('movein-' + t); }
  console.log(probs.join('\n') || 'no problems'); await b.close();
})();
