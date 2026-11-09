// 2.3 screens: 2-step sign-in setup, sign-in with a code, public address setting, devices, app passwords, streaming report.
const { chromium } = require('playwright');
const crypto = require('crypto');
function totp(secret) {
  const a = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"; let bits = "";
  for (const ch of secret) bits += a.indexOf(ch).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g).map(b => parseInt(b, 2)));
  const c = Buffer.alloc(8); c.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const h = crypto.createHmac("sha1", key).update(c).digest(); const o = h[19] & 15;
  return String(((h.readUInt32BE(o) & 0x7fffffff) % 1000000)).padStart(6, "0");
}
(async () => {
  const [OUT, B] = process.argv.slice(2); const problems = []; let n = 0;
  const b = await chromium.launch(); const ctx = await b.newContext({viewport: {width: 390, height: 844}, colorScheme: 'dark'}); const p = await ctx.newPage();
  p.on('pageerror', e => problems.push('JS ' + e.message)); p.on('dialog', d => d.accept());
  const shot = async name => { await p.waitForTimeout(700); await p.screenshot({path: `${OUT}/${String(++n).padStart(2, '0')}-${name}.png`}); };
  await p.goto(B); await p.waitForSelector('#lg-user');
  await p.fill('#lg-user', 'boss'); await p.fill('#lg-pass', 'bosspass'); await p.keyboard.press('Enter'); await p.waitForTimeout(2500);
  await p.evaluate(() => openAccount()); await p.waitForSelector('#tfa-box', {timeout: 10000}); await p.locator('#tfa-box').scrollIntoViewIfNeeded(); await shot('account-security');
  await p.click('#tfa-box button:has-text("Turn on")'); await p.waitForSelector('#tfa-code'); await p.locator('#tfa-box').scrollIntoViewIfNeeded(); await shot('2fa-setup');
  const key = (await p.textContent('#tfa-box b.sel')).replace(/\s/g, '');
  if (!(await p.$('#tfa-box svg'))) problems.push('no QR code');
  await p.fill('#tfa-code', totp(key)); await p.click('#tfa-box button:has-text("Turn on")'); await p.waitForTimeout(1500);
  if (!/recovery codes/.test(await p.textContent('#tfa-box'))) problems.push('recovery codes not shown'); await shot('2fa-recovery');
  await p.fill('#ap-name', 'Mdu iPhone'); await p.click('button:has-text("New app password")'); await p.waitForTimeout(1200);
  if (!/shown once/.test(await p.textContent('#ap-out'))) problems.push('app password not shown'); await p.locator('#ap-out').scrollIntoViewIfNeeded(); await shot('app-password');
  await p.locator('text=Public address').scrollIntoViewIfNeeded(); await shot('public-address');
  await p.click('text=Only devices you approve'); await p.waitForTimeout(1500); await p.locator('text=Public address').scrollIntoViewIfNeeded(); await shot('public-approved');
  const jid = await p.evaluate(async () => { await loadPeopleData(); return people.users.find(u => u.username === 'jess').id; });
  await p.evaluate(id => openPerson(id), jid); await p.waitForSelector('#pdev .box', {timeout: 10000}); await p.locator('#pdev').scrollIntoViewIfNeeded(); await shot('person-devices');
  // sign in again: needs the code
  await p.evaluate(() => signOut()); await p.waitForSelector('#lg-user');
  await p.fill('#lg-user', 'boss'); await p.fill('#lg-pass', 'bosspass'); await p.keyboard.press('Enter'); await p.waitForTimeout(1500);
  if (await p.isHidden('#lg-code')) problems.push('code box not shown'); await shot('login-code');
  await p.waitForTimeout(31000 - Date.now() % 30000);                       // a fresh code (the last one was used)
  await p.fill('#lg-code', totp(key)); await p.keyboard.press('Enter'); await p.waitForTimeout(2500);
  if (await p.isVisible('#lg-user')) problems.push('did not sign in with the code');
  await p.evaluate(() => openStatus()); await p.waitForTimeout(3000); await shot('status');
  console.log(problems.length ? 'PROBLEMS\n' + problems.join('\n') : 'no problems'); await b.close();
})();
