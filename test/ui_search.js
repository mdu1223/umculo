const { chromium, devices } = require('playwright');
(async () => {
  const out = process.argv[2], base = process.argv[3], scheme = process.argv[4] || 'dark', user = process.argv[5], pass = process.argv[6], term = process.argv[7] || 'kwesta';
  const b = await chromium.launch(); const probs = [];
  const ctx = await b.newContext({ ...devices['iPhone 13'], colorScheme: scheme }); const p = await ctx.newPage();
  p.on('pageerror', e => probs.push('PAGE ERROR ' + e.message)); p.on('dialog', d => d.dismiss());
  p.on('response', r => r.url().includes('/api/') && r.status() >= 400 && !/ndcover|\/api\/me$|requests/.test(r.url()) && probs.push(`HTTP ${r.status()} ${r.url()}`));
  let n = 0; const shot = async name => { await p.waitForTimeout(700); await p.screenshot({ path: `${out}/${String(++n).padStart(2, '0')}-${name}.png` }); };
  await p.goto(base + '/'); await p.waitForTimeout(1000);
  if (user && await p.$('#login:not([hidden])')) { await p.fill('#lg-user', user); await p.fill('#lg-pass', pass); await p.click('#login .btn'); await p.waitForTimeout(2000); }
  await p.evaluate(() => { try { localStorage.setItem('umc_recent', JSON.stringify(['kabza de small', 'lil wayne'])); } catch (e) {} renderSearchHome(); });
  await p.waitForTimeout(1500); await shot('home');
  await p.click('#q'); await p.type('#q', term.slice(0, 3), {delay: 80}); await p.waitForTimeout(500); await shot('typing-library-first');
  await p.type('#q', term.slice(3), {delay: 80}); await p.waitForTimeout(400); await shot('typing-more');
  await p.waitForSelector('text=From the internet', {timeout: 5000}); await p.waitForFunction(() => !document.querySelector('#results h2 .spin'), null, {timeout: 30000}).catch(() => probs.push('internet results never came'));
  await shot('all'); await p.evaluate(() => window.scrollTo(0, 700)); await shot('all-2'); await p.evaluate(() => window.scrollTo(0, 1500)); await shot('all-3'); await p.evaluate(() => window.scrollTo(0, 0));
  for (const f of ['Your library', 'Songs', 'Artists', 'Albums']) { await p.click(`.sfilters button:has-text("${f}")`); await shot('filter-' + f.replace(' ', '-')); }
  await p.click('.sfilters button:has-text("All")');
  const more = await p.$('.srow .icon.more'); if (more) { await more.click(); await shot('song-menu'); await p.evaluate(() => closeSheet()); }
  const sw = await p.$('#results .sw'); if (sw) { await p.evaluate(() => swSet(document.querySelector('#results .sw'), true)); await shot('swipe-delete'); await p.click('#results .sw.open .sa.del'); await p.waitForTimeout(1200); await shot('delete-ask'); await p.evaluate(() => closeSheet()); }
  await p.fill('#q', ''); await p.dispatchEvent('#q', 'input'); await p.waitForTimeout(600); await shot('back-home');
  console.log(probs.join('\n') || 'no problems'); await b.close();
})();
