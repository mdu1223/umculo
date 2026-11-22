// v3.2 on a computer: hero, saved for later, DJ page, right-click menu, continue, song marks, quick picks, sidebar playlists.
// Usage: node test/ui_mixes2.js OUT BASE MOCK_PORT   (after v31_mixfeed.py; TEST_SHAZAM=1)
const { chromium } = require('playwright');
(async () => {
  const [out, base, mock] = process.argv.slice(2);
  const b = await chromium.launch({args: ['--autoplay-policy=no-user-gesture-required']}); const probs = [];
  const ctx = await b.newContext({viewport: {width: 1440, height: 900}, colorScheme: 'dark'}); const p = await ctx.newPage();
  p.on('pageerror', e => probs.push('PAGE ERROR ' + e.message));
  p.on('response', r => r.url().includes('/api/') && r.status() >= 500 && probs.push(`HTTP ${r.status()} ${r.url()}`));
  let n = 0; const shot = async name => { await p.waitForTimeout(700); await p.screenshot({ path: `${out}/${String(++n).padStart(2, '0')}-${name}.png` }); };
  await p.goto(base + '/'); await p.waitForTimeout(1200);
  if (await p.$('#login:not([hidden])')) { await p.fill('#lg-user', 'mdu'); await p.fill('#lg-pass', 'ndpass'); await p.click('#login .btn'); await p.waitForTimeout(2500); }
  if (!await p.$('#side-lib .side-pl')) probs.push('no playlists in the sidebar');
  await p.evaluate(() => show('mixes')); await p.waitForSelector('.mx-hero', {timeout: 20000}).catch(() => probs.push('no hero'));
  await shot('mixes-hero');
  // save for later from the hero
  await p.click('.mx-hero-acts .btn:has-text("Save")'); await p.waitForTimeout(1500);
  if (!await p.$('.mx-rowh b:has-text("Saved for later")')) probs.push('saved for later shelf missing');
  // right-click menu
  const card = await p.$('#mx-feed .mx-row .mxc'); await card.click({button: 'right'}); await p.waitForSelector('#ctx', {timeout: 3000}).catch(() => probs.push('no right-click menu'));
  await shot('context-menu');
  await p.click('#ctx button:has-text("Go to")').catch(() => probs.push('no Go to DJ in the menu'));
  await p.waitForSelector('.dj-head .dj-t', {timeout: 15000}).catch(() => probs.push('DJ page did not open'));
  await p.waitForTimeout(1500); await shot('dj-page');
  await p.click('.dj-back'); await p.waitForTimeout(500);
  if (await p.$eval('#mx-dj', e => !e.hidden)) probs.push('DJ page did not close');
  // play a downloadable "mix" with songs, check the seek-bar marks, leave it half way for "continue"
  await p.evaluate(mock => { MIXES.t = {id: 't', url: `http://localhost:${mock}/feed.xml`, title: 'Deep Shizzol Episode 42', channel: 'Deep Shizzol', channel_url: '',
    duration: 3600, views: 1200, thumb: '', source: 'YouTube', why: 'Test'}; }, mock);
  await p.evaluate(() => { store.set('umc_mx_auto', false); repeatMode = 'one'; mixSheet('t', true); }); await p.waitForTimeout(500); await p.click('text=Download and play'); await p.waitForTimeout(2000);
  // the test clip is only seconds long; give the player an hour so the songs (30 s apart) land on the bar
  await p.waitForFunction(() => { const c = chapCache[chapKey(pq[pi])]; return c && c.tracks.some(x => x.t != null); }, null, {timeout: 40000}).catch(() => {});
  await p.evaluate(() => { const a = $('#player'); a.pause(); Object.defineProperty(a, 'duration', {configurable: true, get: () => 3600}); renderMarks(); });
  await p.waitForFunction(() => document.querySelectorAll('#mini-marks i').length > 0, null, {timeout: 40000}).catch(async () => probs.push('no song marks on the seek bar ' + JSON.stringify(await p.evaluate(() => { const it = pq[pi]; const c = chapCache[chapKey(it)]; return {find: it._find, key: chapKey(it), state: c && c.state, n: c && c.tracks.length, ts: c && c.tracks.map(x => x.t), d: $('#player').duration, marks: !!$('#mini-marks')}; }))));
  await shot('song-marks');
  await p.evaluate(() => { const it = pq[pi]; contSave({...it, mix_url: (it.mix_url || '') + '#half', nd_id: null, title: 'Half done mix', artist: 'Test DJ'}, 600, 3600); });
  await p.evaluate(() => { show('search'); renderSearchHome(); }); await p.waitForTimeout(2500); await shot('home');
  await p.evaluate(() => show('mixes')); await p.waitForTimeout(2500);
  if (!await p.$('.mx-rowh b:has-text("Pick up where you left off")')) probs.push('no continue shelf');
  await p.evaluate(() => document.querySelector('.mx-rowh')?.scrollIntoView()); await shot('continue');
  console.log(probs.length ? 'UI MIXES2 PROBLEMS\n' + probs.join('\n') : 'UI MIXES2 OK'); await b.close();
})();
