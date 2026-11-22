// v3.0 desktop: sidebar, player bar, docked now playing, dialogs; nothing breaks on a phone either.
// Usage: node test/ui_desktop.js OUT BASE
const { chromium, devices } = require('playwright');
(async () => {
  const [out, base] = process.argv.slice(2);
  const b = await chromium.launch({args: ['--autoplay-policy=no-user-gesture-required']}); const probs = [];
  const ctx = await b.newContext({viewport: {width: 1440, height: 900}, colorScheme: 'dark'}); const p = await ctx.newPage();
  p.on('pageerror', e => probs.push('PAGE ERROR ' + e.message));
  let n = 0; const shot = async name => { await p.waitForTimeout(800); await p.screenshot({ path: `${out}/${String(++n).padStart(2, '0')}-${name}.png` }); };
  await p.goto(base + '/'); await p.waitForTimeout(1200);
  if (await p.$('#login:not([hidden])')) { await p.fill('#lg-user', 'mdu'); await p.fill('#lg-pass', 'ndpass'); await p.click('#login .btn'); await p.waitForTimeout(2500); }
  await shot('home');
  const list = await p.evaluate(async () => (await api('/api/radio?q=hip%20hop&new=0')).songs.filter(x => x.lib).map(x => ({...x, nd_id: x.id, cover: x.cover ? cover(x.cover) : ''})));
  await p.evaluate(l => playList(l, 0, {id: 'x', name: 'Hip Hop'}), list); await p.waitForTimeout(2500);
  const d = await p.evaluate(() => ({dock: !$('#dock').hidden, np: !!$('#dpanel #np'), bar: getComputedStyle($('#mini')).display, sheetOpen: $('#sheet').classList.contains('open')}));
  if (!d.dock || !d.np || d.bar !== 'grid' || d.sheetOpen) probs.push('desktop layout: ' + JSON.stringify(d));
  await shot('playing-docked');
  await p.evaluate(() => show('mixes')); await p.waitForTimeout(3000); await shot('mixes');
  await p.evaluate(() => show('library')); await p.waitForTimeout(2500); await shot('library');
  await p.evaluate(() => openSettings()); await p.waitForTimeout(1500); await shot('dialog-over-dock');
  if (!await p.$('#dpanel #np')) probs.push('opening a dialog replaced the player');
  await p.keyboard.press('Escape'); await p.waitForTimeout(400);
  if (await p.evaluate(() => $('#sheet').classList.contains('open'))) probs.push('Esc did not close the dialog');
  await p.evaluate(() => { const s = $('#mini-seek'); s.value = 500; s.dispatchEvent(new Event('change')); }); await p.waitForTimeout(600);
  const pos = await p.evaluate(() => ({t: $('#player').currentTime, d: $('#player').duration}));
  if (!(pos.t > pos.d * 0.4)) probs.push('seek bar did not seek ' + JSON.stringify(pos));
  await p.keyboard.press('m'); if (await p.evaluate(() => masterVol) !== 0) probs.push('m did not mute'); await p.keyboard.press('m');
  await p.evaluate(() => toggleDock()); await p.waitForTimeout(500); await shot('dock-closed');
  if (await p.evaluate(() => !$('#dock').hidden)) probs.push('dock did not close');
  await p.setViewportSize({width: 390, height: 844}); await p.waitForTimeout(800); await shot('phone');
  const m = await p.evaluate(() => ({dock: !$('#dock').hidden, nav: getComputedStyle(document.querySelector('nav.tabs')).flexDirection}));
  if (m.dock || m.nav !== 'row') probs.push('phone layout after resize ' + JSON.stringify(m));
  await p.evaluate(() => openPlayer()); await p.waitForTimeout(800); if (!await p.$('#panel #np')) probs.push('phone player did not open in the sheet');
  await shot('phone-player');
  console.log(probs.length ? 'UI DESKTOP PROBLEMS\n' + probs.join('\n') : 'UI DESKTOP OK'); await b.close();
})();
