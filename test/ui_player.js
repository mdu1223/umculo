// v2.7: chapters in the player, gapless/crossfade swap, karaoke + visualiser, radio. Usage: node test/ui_player.js OUT BASE MIX_ID user pass
const { chromium, devices } = require('playwright');
(async () => {
  const [out, base, mid, user, pass] = process.argv.slice(2);
  const b = await chromium.launch({args: ['--autoplay-policy=no-user-gesture-required']}); const probs = [];
  const ctx = await b.newContext({ ...devices['Pixel 7'], colorScheme: 'dark' }); const p = await ctx.newPage();
  p.on('pageerror', e => probs.push('PAGE ERROR ' + e.message));
  p.on('response', r => r.url().includes('/api/') && r.status() >= 500 && probs.push(`HTTP ${r.status()} ${r.url()}`));
  let n = 0; const shot = async name => { await p.waitForTimeout(700); await p.screenshot({ path: `${out}/${String(++n).padStart(2, '0')}-${name}.png` }); };
  await p.goto(base + '/'); await p.waitForTimeout(1200);
  if (user && await p.$('#login:not([hidden])')) { await p.fill('#lg-user', user); await p.fill('#lg-pass', pass); await p.click('#login .btn'); await p.waitForTimeout(2000); }
  // radio on the search home
  await p.evaluate(() => show('search')); await p.waitForTimeout(1500); await shot('radio-home');
  if (!await p.$('.rtile')) { probs.push('no radio tiles: ' + (await p.$eval('#results', e => e.innerText.slice(0, 300)))); }
  await p.fill('.radio-in .field', 'amapiano'); await p.click('.radio-in .btn'); await p.waitForTimeout(2500);
  if (!await p.evaluate(() => pq.length > 1 && nowSrc && /amapiano/i.test(nowSrc.name))) probs.push('radio did not start');
  // chapters
  await p.evaluate(mid => playList([{nd_id: mid, title: 'Deep Session 02', artist: 'DJ Test', duration: 3600}], 0, {id: 'm', name: 'DJ Test'}), mid);
  await p.waitForTimeout(1200); await p.evaluate(() => openPlayer());
  await p.waitForSelector('.chap', {timeout: 15000}); await shot('chapters');
  if (!await p.$('#np-chap span')) probs.push('no now-in-the-mix line');
  // gapless: two short library songs, the second preloaded and swapped in
  const ids = await p.evaluate(async () => (await api('/api/radio?q=amapiano&new=0')).songs.filter(x => x.lib).slice(0, 2).map(x => ({...x, nd_id: x.id})));
  await p.evaluate(list => { closeSheet(); playList(list, 0); }, ids);
  await p.waitForFunction(() => isFinite($('#player').duration), null, {timeout: 15000});
  await p.evaluate(() => { const a = $('#player'); a.currentTime = Math.max(0, a.duration - 3); });
  await p.waitForFunction(() => pi === 1, null, {timeout: 15000}).catch(() => probs.push('did not move to the next song'));
  await p.waitForTimeout(800);
  const st = await p.evaluate(() => ({playing: !$('#player').paused, spareIdle: !$('#player2').getAttribute('src'), title: pq[pi].title}));
  if (!st.playing) probs.push('next song not playing after the swap ' + JSON.stringify(st));
  // crossfade setting
  await p.evaluate(() => openPlayer()); await p.evaluate(() => moreSheet()); await p.waitForTimeout(500);
  if (!await p.$('#panel .seg button:has-text("6 s")')) probs.push('no crossfade setting'); else { await p.click('#panel .seg button:has-text("6 s")'); await shot('crossfade'); }
  await p.evaluate(() => setXfade(0));
  // karaoke + visualiser
  await p.evaluate(() => openStage('karaoke')); await p.waitForTimeout(1500); await shot('karaoke');
  if (await p.$eval('#stage', e => e.hidden)) probs.push('stage did not open');
  await p.evaluate(() => openStage('visual')); await p.waitForTimeout(1500); await shot('visualiser');
  await p.evaluate(() => closeStage());
  if (!await p.$eval('#stage', e => e.hidden)) probs.push('stage did not close');
  console.log(probs.length ? 'UI PLAYER PROBLEMS\n' + probs.join('\n') : 'UI PLAYER OK'); await b.close();
})();
