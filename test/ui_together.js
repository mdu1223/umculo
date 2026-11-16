// v2.8: listen together across two people, the Play on sheet, saving to the phone and playing offline.
// Usage: node test/ui_together.js OUT BASE   (after test/v28_together.py made accounts boss and jess)
const { chromium, devices } = require('playwright');
(async () => {
  const [out, base] = process.argv.slice(2);
  const b = await chromium.launch({args: ['--autoplay-policy=no-user-gesture-required']}); const probs = [];
  const mk = async (u, pw) => { const ctx = await b.newContext({ ...devices['Pixel 7'], colorScheme: 'dark' }); const p = await ctx.newPage();
    p.on('pageerror', e => probs.push(u + ' PAGE ERROR ' + e.message));
    await p.goto(base + '/'); await p.waitForTimeout(1200);
    if (await p.$('#login:not([hidden])')) { await p.fill('#lg-user', u); await p.fill('#lg-pass', pw); await p.click('#login .btn'); await p.waitForTimeout(2500); }
    return {ctx, p}; };
  const A = await mk('boss', 'bosspass'), J = await mk('jess', 'jesspw1');
  let n = 0; const shot = async (pg, name) => { await pg.waitForTimeout(600); await pg.screenshot({ path: `${out}/${String(++n).padStart(2, '0')}-${name}.png` }); };
  // boss plays an album and opens a room
  const list = await A.p.evaluate(async () => { const r = await api('/api/radio?q=hip%20hop&new=0'); return r.songs.filter(x => x.lib).map(x => ({...x, nd_id: x.id, cover: x.cover ? cover(x.cover) : ''})); });
  await A.p.evaluate(l => playList(l, 0, {id: 'x', name: 'Hip Hop'}), list); await A.p.waitForTimeout(1500);
  await A.p.evaluate(() => openPlayer()); await A.p.click('#np [aria-label="Listen together"]'); await A.p.click('text=Start a room with what\'s playing');
  await A.p.waitForSelector('.party-code', {timeout: 10000}); const code = (await A.p.textContent('.party-code')).trim(); await shot(A.p, 'dj-room');
  // jess joins and follows
  await A.p.evaluate(() => { const a = $('#player'); a.pause(); a.currentTime = 1; }); await A.p.waitForTimeout(800);   // test songs last 6 s: hold still
  await J.p.evaluate(c => partyJoin(c), code); await J.p.waitForTimeout(3500);
  const js = await J.p.evaluate(() => ({t: pq[pi]?.title, playing: !$('#player').paused, n: pq.length, err: $('#player').error?.code}));
  const as = await A.p.evaluate(() => ({t: pq[pi]?.title}));
  if (js.t !== as.t && !(await J.p.waitForFunction(t => pq[pi]?.title === t, as.t, {timeout: 5000}).catch(() => null)) || js.playing) probs.push('guest not following ' + JSON.stringify({js, as}));
  await shot(J.p, 'guest-player');
  // the DJ skips; she follows
  await A.p.evaluate(() => { $('#player').pause(); nextTrack(); setTimeout(() => $('#player').pause(), 300); }); await J.p.waitForTimeout(4500);
  const j2 = await J.p.evaluate(() => pq[pi]?.title), a2 = await A.p.evaluate(() => pq[pi]?.title);
  if (j2 !== a2) probs.push(`guest did not follow the skip: ${j2} vs ${a2}`);
  // she adds a song; the DJ gets it
  await J.p.evaluate(() => queueSong({deezer_id: 111, title: 'Machine Gun', artist: 'FreakSA'}, true)); await A.p.waitForTimeout(4000);
  if (!await A.p.evaluate(() => pq.some(x => x.deezer_id == 111))) probs.push('the DJ did not get her song ' + JSON.stringify(await A.p.evaluate(async () => ({party: !!party, pv: partyVer, ver: party && party.ver, srv: party && (await api('/api/party/' + party.code)).queue.map(x => x.title), mine: pq.map(x => x.title)}))));
  await J.p.evaluate(() => partySheet()); await shot(J.p, 'guest-room');
  await A.p.evaluate(() => partyLeave()); await J.p.waitForTimeout(3000);
  if (await J.p.evaluate(() => !!party)) probs.push('guest still in a closed room');
  // play on
  await A.p.evaluate(() => { openPlayer(); castSheet(); }); await A.p.waitForTimeout(1500); await shot(A.p, 'play-on');
  if (!await A.p.$('text=This phone')) probs.push('no Play on sheet');
  // save to phone, then offline
  await A.p.evaluate(() => closeSheet());
  await A.p.evaluate(async () => { LISTS.t = pq.filter(x => x.nd_id).slice(0, 2); await offSave('test-list', 'Road trip', LISTS.t, null); });
  const saved = await A.p.evaluate(async () => (await (await caches.open('umc-offline')).keys()).length);
  if (saved < 2) probs.push('songs not saved to the phone: ' + saved);
  await A.ctx.setOffline(true); await A.p.reload(); await A.p.waitForTimeout(2500); await shot(A.p, 'offline');
  if (!await A.p.$('text=On this phone')) probs.push('offline: no On this phone');
  await A.p.evaluate(() => playList(offItems(offStore().lists['test-list']), 0)); await A.p.waitForTimeout(2500);
  const off = await A.p.evaluate(() => ({playing: !$('#player').paused, t: $('#player').currentTime, err: $('#player').error?.code}));
  if (!off.playing || off.err) probs.push('offline song does not play ' + JSON.stringify(off));
  await A.ctx.setOffline(false);
  console.log(probs.length ? 'UI TOGETHER PROBLEMS\n' + probs.join('\n') : 'UI TOGETHER OK'); await b.close();
})();
