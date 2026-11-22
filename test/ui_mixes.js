// v3.0: Mixes for you -> a mix -> Download (subscribe / find songs / video) -> plays right away, video, songs as heard.
// Usage: node test/ui_mixes.js OUT BASE MOCK_PORT   (after v31_mixfeed.py; TEST_SHAZAM=1)
const { chromium, devices } = require('playwright');
(async () => {
  const [out, base, mock] = process.argv.slice(2);
  const b = await chromium.launch({args: ['--autoplay-policy=no-user-gesture-required']}); const probs = [];
  const ctx = await b.newContext({ ...devices['iPhone 13'], colorScheme: 'dark' }); const p = await ctx.newPage();
  p.on('pageerror', e => probs.push('PAGE ERROR ' + e.message));
  p.on('response', r => r.url().includes('/api/') && r.status() >= 500 && probs.push(`HTTP ${r.status()} ${r.url()}`));
  let n = 0; const shot = async name => { await p.waitForTimeout(700); await p.screenshot({ path: `${out}/${String(++n).padStart(2, '0')}-${name}.png` }); };
  await p.goto(base + '/'); await p.waitForTimeout(1200);
  if (await p.$('#login:not([hidden])')) { await p.fill('#lg-user', 'mdu'); await p.fill('#lg-pass', 'ndpass'); await p.click('#login .btn'); await p.waitForTimeout(2000); }
  await p.evaluate(() => show('mixes')); await p.waitForSelector('#mx-feed .mxc:not(.sk)', {timeout: 20000}).catch(() => probs.push('no mix cards'));
  await shot('mixes-for-you');
  await p.fill('#mx-q', 'soulful'); await p.click('.mx-search .btn'); await p.waitForSelector('#mx-results .mxc:not(.sk)', {timeout: 15000}).catch(() => probs.push('no search results'));
  await shot('search');
  // a mix that can really download here: the pretend podcast feed
  await p.evaluate(mock => { MIXES.t = {id: 't', url: `http://localhost:${mock}/feed.xml`, title: 'Deep Shizzol Episode 42', channel: 'Deep Shizzol', channel_url: '',
    duration: 3600, views: 1200, thumb: '', source: 'YouTube', why: 'Because you play DysFonik'}; mixSheet('t'); }, mock);
  await p.waitForTimeout(600); await shot('mix-sheet');
  await p.click('.mxs-acts .btn:has-text("Download")'); await p.waitForSelector('#mxs-opts:not([hidden])'); await shot('download-options');
  if (!await p.$('#mxo-find') || !await p.$('#mxo-video')) probs.push('missing find/video switches');
  await p.click('text=Download and play'); await p.waitForTimeout(2500);
  const st = await p.evaluate(() => ({it: pq[pi]?.title, mix: !!pq[pi]?.mix_url, find: pq[pi]?._find, open: npOpen(), playing: !$('#player').paused, err: $('#player').error?.code}));
  if (!st.mix || !st.find || !st.open) probs.push('did not start playing the downloading mix ' + JSON.stringify(st));
  await shot('playing-while-downloading');
  if (!await p.$('.vid-tg')) probs.push('no video toggle for the mix');
  await p.waitForSelector('.chap', {timeout: 40000}).catch(() => probs.push('songs never appeared'));
  await p.evaluate(() => document.querySelector('.np-up')?.scrollIntoView()); await shot('songs-as-heard');
  console.log(probs.length ? 'UI MIXES PROBLEMS\n' + probs.join('\n') : 'UI MIXES OK'); await b.close();
})();
