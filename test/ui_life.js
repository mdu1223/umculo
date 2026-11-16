// v2.9: messages and gigs settings, gigs in For You, alarms. Usage: node test/ui_life.js OUT BASE (after v29_life.py)
const { chromium, devices } = require('playwright');
(async () => {
  const [out, base] = process.argv.slice(2);
  const b = await chromium.launch(); const probs = [];
  const ctx = await b.newContext({ ...devices['iPhone 13'], colorScheme: 'light' }); const p = await ctx.newPage();
  p.on('pageerror', e => probs.push('PAGE ERROR ' + e.message));
  p.on('response', r => r.url().includes('/api/') && r.status() >= 500 && probs.push(`HTTP ${r.status()} ${r.url()}`));
  let n = 0; const shot = async name => { await p.waitForTimeout(800); await p.screenshot({ path: `${out}/${String(++n).padStart(2, '0')}-${name}.png` }); };
  await p.goto(base + '/'); await p.waitForTimeout(1200);
  if (await p.$('#login:not([hidden])')) { await p.fill('#lg-user', 'boss'); await p.fill('#lg-pass', 'bosspass'); await p.click('#login .btn'); await p.waitForTimeout(2500); }
  await p.evaluate(() => show('foryou')); await p.waitForSelector('.gig', {timeout: 20000}).catch(() => probs.push('no gig cards in For You'));
  await p.evaluate(() => document.querySelector('#fy-gigs')?.scrollIntoView()); await shot('gigs');
  await p.evaluate(() => openAccount()); await p.waitForTimeout(1500); await p.click('text=Messages and gigs'); await p.waitForTimeout(1200); await shot('messages');
  if (!await p.$('#nf-wa')) probs.push('no WhatsApp number field');
  await p.evaluate(() => alarmSheet()); await p.waitForTimeout(1500); await shot('alarms');
  if (!await p.$('#al-time')) probs.push('no new alarm form');
  await p.click('text=Make a new hook address'); await p.waitForTimeout(1000); await p.evaluate(() => document.querySelector('#hook-out')?.scrollIntoView()); await shot('hook');
  if (!await p.$('#hook-out pre')) probs.push('no hook example');
  console.log(probs.length ? 'UI LIFE PROBLEMS\n' + probs.join('\n') : 'UI LIFE OK'); await b.close();
})();
