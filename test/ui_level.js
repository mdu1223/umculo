// v3.8: even volume in the web player, and the Queue screen. Usage: node test/ui_level.js OUT BASE LOUD_ID QUIET_ID user pass
const { chromium, devices } = require('playwright');
(async () => {
  const [out, base, loud, quiet, user, pass] = process.argv.slice(2);
  const b = await chromium.launch({args: ['--autoplay-policy=no-user-gesture-required']}); const probs = [];
  const ctx = await b.newContext({ ...devices['Pixel 7'], colorScheme: 'dark' }); const p = await ctx.newPage();
  p.on('pageerror', e => probs.push('PAGE ERROR ' + e.message));
  p.on('response', r => r.url().includes('/api/') && r.status() >= 500 && probs.push(`HTTP ${r.status()} ${r.url()}`));
  let n = 0; const shot = async name => { await p.waitForTimeout(700); await p.screenshot({ path: `${out}/${String(++n).padStart(2, '0')}-${name}.png` }); };
  await p.goto(base + '/'); await p.waitForTimeout(1200);
  if (user && await p.$('#login:not([hidden])')) { await p.fill('#lg-user', user); await p.fill('#lg-pass', pass); await p.click('#login .btn'); await p.waitForTimeout(2000); }
  await p.evaluate(([a, c]) => playList([{nd_id: a, title: 'Loud One', artist: 'Loudbeat', duration: 40}, {nd_id: c, title: 'Quiet One', artist: 'Loudbeat', duration: 40}], 0), [loud, quiet]);
  await p.waitForFunction(() => $('#player').volume < 0.99, null, {timeout: 30000}).catch(() => {});
  const v1 = await p.evaluate(() => ({vol: $('#player').volume, lv: {...LEVEL}}));
  const exp = Math.min(1, 10 ** (v1.lv[loud] / 20));
  if (!(v1.vol < 0.9 && Math.abs(v1.vol - exp) < 0.02)) probs.push('loud song not turned down ' + JSON.stringify(v1));
  await p.evaluate(() => setVol(0.5));
  const v2 = await p.evaluate(() => $('#player').volume);
  if (Math.abs(v2 - exp * 0.5) > 0.02) probs.push('volume slider and levelling do not combine ' + v2);
  await p.evaluate(() => setVol(1));
  await p.evaluate(() => { openPlayer(); moreSheet(); }); await shot('even-volume-setting');
  if (!await p.$('#panel input[onchange^="setLevel"]')) probs.push('no Even volume setting');
  await p.evaluate(() => setLevel(false)); await p.waitForTimeout(300);
  if (await p.evaluate(() => $('#player').volume) < 0.99) probs.push('turning it off did not restore the volume');
  await p.evaluate(() => setLevel(true)); await p.waitForTimeout(300);
  // the next song: the quiet one gets no boost past full volume
  await p.evaluate(() => { closeSheet(); nextTrack(); }); await p.waitForTimeout(2500);
  const v3 = await p.evaluate(() => ({t: pq[pi].title, vol: $('#player').volume}));
  if (v3.t !== 'Quiet One' || v3.vol < 0.99) probs.push('quiet song should play at full volume ' + JSON.stringify(v3));
  await p.evaluate(() => { closeSheet(); show('requests'); }); await p.waitForTimeout(1500); await shot('queue');
  const q = await p.$eval('#view-requests, main', e => e.innerText.slice(0, 400)).catch(() => '');
  if (/undefined|NaN|\[object/.test(q)) probs.push('odd text on the Queue screen ' + q);
  // an iPhone: no levelling (Safari ignores volume), and the setting says so
  const ip = await b.newContext({ ...devices['iPhone 13'], colorScheme: 'dark' }); const p2 = await ip.newPage();
  p2.on('pageerror', e => probs.push('IPHONE PAGE ERROR ' + e.message));
  await p2.addInitScript(() => { Object.defineProperty(HTMLMediaElement.prototype, 'volume', { get() { return 1; }, set(v) {} }); });
  await p2.goto(base + '/'); await p2.waitForTimeout(1200);
  if (await p2.$('#login:not([hidden])')) { await p2.fill('#lg-user', user); await p2.fill('#lg-pass', pass); await p2.click('#login .btn'); await p2.waitForTimeout(2000); }
  await p2.evaluate(a => { playList([{nd_id: a, title: 'Loud One', artist: 'Loudbeat', duration: 40}], 0); openPlayer(); moreSheet(); }, loud);
  await p2.waitForTimeout(1000); await p2.screenshot({ path: `${out}/${String(++n).padStart(2, '0')}-iphone-setting.png` });
  const t = await p2.$eval('#panel', e => e.innerText);
  if (!/iPhones don't let a web page change the volume/.test(t)) probs.push('iPhone note missing');
  await b.close();
  console.log(probs.length ? probs.join('\n') + '\nUI LEVEL FAILED' : 'UI LEVEL OK');
})();
