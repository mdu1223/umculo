// v3.9.1: the Mixes section end to end: recommended mixes, download, progress in the mix window, stop, finished,
// see the songs, find again, play from the library, delete; a failed download; save for later; not interested;
// a DJ's page; search; the song finder list (find again, find again in the few).
// Usage: node test/ui_mixes3.js OUT BASE MOCK_PORT   (TEST_SHAZAM=1, TEST_MIXES=test/mixes_fixture.json)
const { chromium, devices } = require('playwright');
(async () => { let b0; const probs0 = []; try { await main(); } catch (e) { console.log('CRASH ' + e.message.split('\n')[0]); } })();
async function main() {
  const [out, base, mock] = process.argv.slice(2);
  const b = await chromium.launch({args: ['--autoplay-policy=no-user-gesture-required']}); const probs = [];
  const ctx = await b.newContext({ ...devices['Pixel 7'], colorScheme: 'dark' }); const p = await ctx.newPage();
  p.on('pageerror', e => probs.push('PAGE ERROR ' + e.message));
  p.on('response', r => r.url().includes('/api/') && r.status() >= 500 && probs.push(`HTTP ${r.status()} ${r.url()}`));
  p.on('dialog', d => d.accept());
  let n = 0; const shot = async name => { await p.waitForTimeout(600); await p.screenshot({ path: `${out}/${String(++n).padStart(2, '0')}-${name}.png` }); };
  const tap = async sel => { try { await p.click(sel, {timeout: 5000}); return true; } catch (e) { probs.push('could not tap ' + sel); return false; } };
  const panel = () => p.$eval('#panel', e => e.innerText).catch(() => '');
  const toastText = () => p.$eval('#toast', e => e.innerText).catch(() => '');
  const lib = id => p.evaluate(async id => { const m = MIXES[id]; return api(`/api/mixlib?u=${encodeURIComponent(m.url)}&t=${encodeURIComponent(m.title)}`); }, id);
  const waitLib = async (id, ok, secs, what) => { for (let i = 0; i < secs; i++) { const r = await lib(id); if (ok(r)) return r; await p.waitForTimeout(1000); }
    probs.push('timed out waiting: ' + what); return lib(id); };
  await p.goto(base + '/'); await p.waitForTimeout(1200);
  if (await p.$('#login:not([hidden])')) { await p.fill('#lg-user', 'mdu'); await p.fill('#lg-pass', 'ndpass'); await tap('#login .btn'); await p.waitForTimeout(2000); }
  await p.evaluate(() => show('mixes')); await p.waitForSelector('#mx-feed .mxc:not(.sk)', {timeout: 20000}).catch(() => probs.push('no recommended mixes'));
  await shot('mixes');

  // 1. a recommended mix: its window, then a download that can't work here (no YouTube in tests): it must say so
  const card = await p.$('#mx-feed .mx-shelf .mxc:not(.sk)');
  const recId = await card.evaluate(e => (e.getAttribute('onclick') || '').match(/mixSheet\('([^']+)'/)?.[1]);
  await card.click(); await p.waitForTimeout(800); await shot('recommended-window');
  if (!/Play now/.test(await panel()) || !await p.$('#mxs-dl:not([hidden])')) probs.push('recommended window: no Play now / Download');
  await tap('#mxs-dl'); await p.waitForSelector('#mxs-opts:not([hidden])').catch(() => probs.push('download options did not open'));
  await tap('text=Download and play'); await p.waitForTimeout(2000);
  if (!await p.evaluate(() => npOpen())) probs.push('the player did not open after Download and play');
  await p.evaluate(() => { closeSheet(); if (desk()) closeNp(); });
  await p.waitForTimeout(3000);
  const rec = await lib(recId);                  // here YouTube can't even be read: either no record (a message said so) or a failed one
  await p.evaluate(id => mixSheet(id), recId); await p.waitForTimeout(1500); await shot('failed-download');
  if (rec.find && rec.find.state === 'failed' && !/Couldn't download it last time/.test(await panel())) probs.push('a failed download is not shown: ' + (await panel()).slice(0, 200));
  if (rec.find && ['queued','downloading','listening'].includes(rec.find.state)) probs.push('the YouTube download is still going in tests?');
  if (!await p.$('#mxs-dl:not([hidden])')) probs.push('after a failed download, Download should be offered again');
  await p.evaluate(() => closeSheet());

  // 2. a mix that really downloads here (the pretend podcast): progress in its window, Download hidden, Stop offered
  await p.evaluate(mock => { MIXES.t = {id: 't', url: `http://localhost:${mock}/feed.xml`, title: 'Deep Sessions 042', channel: 'Deep Shizzol',
    channel_url: '', duration: 3600, views: 1200, thumb: '', source: 'YouTube', why: 'test'}; mixSheet('t', true); }, mock);
  await p.waitForTimeout(800); await tap('text=Download and play'); await p.waitForTimeout(1500);
  await p.evaluate(() => { closeSheet(); if (desk()) closeNp(); mixSheet('t'); }); await p.waitForTimeout(1800); await shot('downloading-window');
  const t1 = await panel();
  if (!/Downloading the mix|Finding the songs|Waiting to start/.test(t1) || !/Stop/.test(t1)) probs.push('no progress in the mix window: ' + t1.slice(0, 200));
  if (await p.$('#mxs-dl:not([hidden])')) probs.push('Download offered while it is downloading');
  // 3. finished: in your library, with Play / See the songs / Find again / Delete
  const done1 = await waitLib('t', r => r.nd_id && r.find && r.find.state === 'done', 90, 'the mix to download and be listened to');
  await p.evaluate(() => mixSheet('t')); await p.waitForTimeout(1800); await shot('in-library');
  const t2 = await panel();
  for (const w of ['In your library', 'Play from your library', 'See the songs', 'Find the songs again', 'Delete'])
    if (!t2.includes(w)) probs.push(`finished window lacks "${w}"`);
  if (await p.$('#mxs-dl:not([hidden])')) probs.push('Download offered for a mix you have');
  const count1 = done1.find?.count || 0;
  if (!count1) probs.push('no songs found in the test mix');
  await tap('#panel button:has-text("See the songs")'); await p.waitForTimeout(1200);
  if ((await p.$$('#panel .chk')).length !== count1) probs.push(`See the songs lists ${(await p.$$('#panel .chk')).length}, expected ${count1}`);
  await shot('songs');
  // 4. find the songs again: the earlier songs stay, and it ends with at least as many
  await p.evaluate(() => mixSheet('t')); await p.waitForTimeout(1500);
  await tap('#panel button:has-text("Find the songs again")'); await p.waitForTimeout(1500);
  if (!/Listening to the mix again/.test(await toastText())) probs.push('find again: no word');
  const again = await waitLib('t', r => r.find && r.find.state === 'done' && r.find.id !== done1.find.id, 90, 'finding the songs again');
  if ((again.find?.count || 0) < count1) probs.push(`find again lost songs: ${again.find?.count} < ${count1}`);
  const finds = await p.evaluate(() => api('/api/mixes').then(r => r.finds.filter(f => /Deep Sessions 042/.test(f.title)).length));
  if (finds !== 1) probs.push(`after finding again there are ${finds} entries for the mix (the old one should go)`);
  // 5. play it from the library: the player lists its songs, with Find again
  await p.evaluate(() => mixSheet('t')); await p.waitForTimeout(1500);
  await tap('#panel button:has-text("Play from your library")'); await p.waitForTimeout(3000);
  const pl = await p.evaluate(() => ({nd: pq[pi]?.nd_id, title: pq[pi]?.title}));
  if (!pl.nd) probs.push('Play from your library did not play the library copy ' + JSON.stringify(pl));
  await p.evaluate(() => { npView = 'next'; renderPlayer(); }); await p.waitForTimeout(2500); await shot('player-songs');
  const host = await p.evaluate(() => npHost().innerText);
  if (!/in this mix/i.test(host) || !/Find again/.test(host)) probs.push('the player lacks In this mix / Find again: ' + host.slice(0, 200));
  await p.evaluate(() => { $('#player').pause(); closeSheet(); if (desk()) closeNp(); });
  // 6. delete it: gone from the library, the song finder list and the window
  await p.evaluate(() => mixSheet('t')); await p.waitForTimeout(1500);
  await tap('#panel button:has-text("Delete")'); await p.waitForTimeout(2500);
  if (!/Mix deleted/.test(await toastText())) probs.push('delete: no word ' + await toastText());
  const gone = await lib('t');
  if (gone.nd_id || (gone.find && gone.find.state === 'done')) probs.push('the mix is still there after Delete ' + JSON.stringify(gone));
  await p.evaluate(() => mixSheet('t')); await p.waitForTimeout(1500); await shot('after-delete');
  if (/In your library/.test(await panel()) || !await p.$('#mxs-dl:not([hidden])')) probs.push('the window still shows it as yours after Delete');
  await p.evaluate(() => closeSheet());
  // 7. stop a download part way
  await p.evaluate(() => mixSheet('t', true)); await p.waitForTimeout(800); await tap('text=Download and play'); await p.waitForTimeout(800);
  await p.evaluate(() => { closeSheet(); if (desk()) closeNp(); mixSheet('t'); });
  const stopBtn = await p.waitForSelector('#panel button:has-text("Stop")', {timeout: 6000}).catch(() => null);
  if (stopBtn) { await stopBtn.click(); await p.waitForTimeout(1500); }
  else if (!((await lib('t')).find?.state === 'done')) probs.push('no Stop while downloading');
  const after = await p.evaluate(() => api('/api/mixes').then(r => r.finds.filter(f => /Deep Sessions 042/.test(f.title) && ['queued','downloading','listening'].includes(f.state)).length));
  if (after) probs.push('Stop did not stop it');
  await p.evaluate(() => closeSheet()); await p.waitForTimeout(1500);
  // 8. save for later and back; not interested; a DJ's page; search
  await p.evaluate(id => mixLater(id), recId); await p.waitForTimeout(1800);
  if (!/Saved for later/.test(await p.$eval('#mx-feed', e => e.innerText))) probs.push('Saved for later shelf missing');
  await p.evaluate(id => mixLater(id), recId); await p.waitForTimeout(1800);
  if (/Saved for later/.test(await p.$eval('#mx-feed', e => e.innerText))) probs.push('Saved for later did not go');
  const second = await p.$$eval('#mx-feed .mx-shelf .mxc:not(.sk)', els => els.map(e => (e.getAttribute('onclick') || '').match(/mixSheet\('([^']+)'/)?.[1]).filter(Boolean));
  const hideId = second.find(x => x !== recId);
  if (hideId) { await p.evaluate(id => mixHide(id), hideId); await p.waitForTimeout(2500);
    if ((await p.$eval('#mx-feed', e => e.innerHTML)).includes(`mixSheet('${hideId}')`)) probs.push('Not interested: the mix is still there'); }
  const djLink = await p.$('#mx-feed .mxc-s a');
  if (djLink) { await djLink.click(); await p.waitForTimeout(2500); await shot('dj');
    if (await p.$eval('#mx-dj', e => e.hidden)) probs.push("a DJ's page did not open");
    await tap('.dj-back'); await p.waitForTimeout(600);
    if (await p.$eval('#mx-feed', e => e.hidden)) probs.push("back from a DJ's page: the feed is hidden"); }
  await p.fill('#mx-q', 'soulful'); await tap('.mx-search .btn'); await p.waitForSelector('#mx-results .mxc:not(.sk)', {timeout: 15000}).catch(() => probs.push('no search results'));
  await tap('#mx-results button:has-text("Clear")'); await p.waitForTimeout(400);
  if ((await p.$eval('#mx-results', e => e.innerHTML)).trim()) probs.push('Clear did not clear the results');
  // 9. the song finder list: Find again on a finished one, and the few
  await p.evaluate(() => loadMixes()); await p.waitForTimeout(1500); await shot('finder-list');
  const finderText = await p.$eval('#mx-finds', e => e.innerText).catch(() => '');
  if (/mixs\b/.test(finderText)) probs.push('"mixs" in the song finder list');
  // the hero's Download opens the mix window with its options
  const heroDl = await p.$('.mx-hero-acts button:has-text("Download")');
  if (heroDl) { await heroDl.click(); await p.waitForTimeout(1200);
    if (!await p.$('#mxs-opts:not([hidden])') && !/In your library|Downloading/.test(await panel())) probs.push("the hero's Download did not open the options");
    await p.evaluate(() => closeSheet()); }
  await b.close();
  console.log(probs.length ? probs.join('\n') + '\nUI MIXES3 FAILED' : 'UI MIXES3 OK');
}
