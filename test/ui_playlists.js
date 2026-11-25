// v3.9: Like and Add to playlist in the song menus (a mix's song list, search, the player). Usage: node test/ui_playlists.js OUT BASE user pass
const { chromium, devices } = require('playwright');
(async () => {
  const [out, base, user, pass] = process.argv.slice(2);
  const b = await chromium.launch(); const probs = [];
  const ctx = await b.newContext({ ...devices['Pixel 7'], colorScheme: 'dark' }); const p = await ctx.newPage();
  p.on('pageerror', e => probs.push('PAGE ERROR ' + e.message));
  p.on('response', r => r.url().includes('/api/') && r.status() >= 500 && probs.push(`HTTP ${r.status()} ${r.url()}`));
  let n = 0; const shot = async name => { await p.waitForTimeout(600); await p.screenshot({ path: `${out}/${String(++n).padStart(2, '0')}-${name}.png` }); };
  await p.goto(base + '/'); await p.waitForTimeout(1200);
  if (user && await p.$('#login:not([hidden])')) { await p.fill('#lg-user', user); await p.fill('#lg-pass', pass); await p.click('#login .btn'); await p.waitForTimeout(2000); }
  // a song heard in a mix that's in your library
  await p.evaluate(() => findMenu('Kwesta', 'Ngud', true)); await shot('mix-song-menu');
  const menu = await p.$eval('#panel', e => e.innerText);
  if (!/Like/.test(menu) || !/Add to playlist/.test(menu)) probs.push('mix song menu: ' + menu.slice(0, 200));
  await p.click('#panel button:has-text("Like")'); await p.waitForTimeout(1200);
  const t1 = await p.$eval('#toast', e => e.innerText).catch(() => '');
  if (!/Liked/.test(t1)) probs.push('like toast: ' + t1);
  await p.evaluate(() => findMenu('Kwesta', 'Ngud', true)); await p.waitForTimeout(500);
  if (!/Liked \(tap to unlike\)/.test(await p.$eval('#panel', e => e.innerText))) probs.push('the menu shows it liked');
  // add to a new playlist from the picker
  await p.click('#panel button:has-text("Add to playlist")'); await p.waitForTimeout(1500); await shot('picker');
  const pk = await p.$eval('#panel', e => e.innerText);
  if (!/Family Faves/.test(pk) || !/New playlist name/.test(await p.$eval('#panel input', e => e.placeholder))) probs.push('picker: ' + pk.slice(0, 200));
  await p.fill('#panel input[name=n]', 'Sunday Drive'); await p.click('#panel form button'); await p.waitForTimeout(2500);
  const t2 = await p.$eval('#toast', e => e.innerText).catch(() => '');
  if (!/Made Sunday Drive: 1 song added/.test(t2)) probs.push('create toast: ' + t2);
  // the next time, it's in the list; adding again says it's already there
  await p.evaluate(() => findMenu('Kwesta', 'Ngud', true)); await p.click('#panel button:has-text("Add to playlist")'); await p.waitForTimeout(1500);
  await p.click('#pl-pick button:has-text("Sunday Drive")'); await p.waitForTimeout(2000);
  const t3 = await p.$eval('#toast', e => e.innerText).catch(() => '');
  if (!/1 already there/.test(t3)) probs.push('again toast: ' + t3);
  // an unnamed new playlist is refused politely
  await p.evaluate(() => plPicker([{artist: 'Kwesta', title: 'Ngud'}])); await p.waitForTimeout(1200);
  await p.click('#panel form button'); await p.waitForTimeout(600);
  if (!/name/.test(await p.$eval('#toast', e => e.innerText).catch(() => ''))) probs.push('no name: no hint');
  await p.evaluate(() => closeSheet());
  // search results' menu has them too
  await p.fill('#q', 'Ngud'); await p.press('#q', 'Enter'); await p.waitForTimeout(2500);
  const ok = await p.evaluate(() => { try { songMenu('lib', 0); return true; } catch (e) { return String(e); } });
  await p.waitForTimeout(500); await shot('search-menu');
  if (ok !== true || !/Add to playlist/.test(await p.$eval('#panel', e => e.innerText).catch(() => ''))) probs.push('search menu: ' + ok);
  await b.close();
  console.log(probs.length ? probs.join('\n') + '\nUI PLAYLISTS FAILED' : 'UI PLAYLISTS OK');
})();
