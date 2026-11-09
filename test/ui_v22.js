// 2.2: sign-in form survives background checks; Library access on a person's page.
const { chromium } = require('playwright');
(async () => {
  const B = process.argv[3], OUT = process.argv[2], problems = [];
  const b = await chromium.launch(); const p = await b.newPage({viewport: {width: 390, height: 844}});
  p.on('pageerror', e => problems.push('JS ' + e.message)); p.on('dialog', d => d.accept());
  await p.goto(B); await p.waitForSelector('#lg-user');
  let draws = 0; await p.exposeFunction('drew', () => draws++);
  await p.evaluate(() => new MutationObserver(() => window.drew()).observe(document.querySelector('#login'), {childList: true}));
  await p.click('#lg-user'); await p.keyboard.type('bo', {delay: 400});
  await p.evaluate(async () => { for (let i = 0; i < 5; i++) await fetch('/api/requests').then(r => r.status === 401 && showLogin()); });   // what the timers do
  await p.keyboard.type('ss', {delay: 400}); await p.click('#lg-pass'); await p.keyboard.type('bossp', {delay: 300});
  await p.evaluate(() => { showLogin(); showLogin(); });
  await p.keyboard.type('ass', {delay: 300});
  const vals = await p.evaluate(() => [document.querySelector('#lg-user').value, document.querySelector('#lg-pass').value, document.activeElement.id]);
  if (vals[0] !== 'boss' || vals[1] !== 'bosspass' || vals[2] !== 'lg-pass') problems.push('typing lost: ' + JSON.stringify(vals));
  if (draws) problems.push('form redrawn ' + draws + ' times');
  await p.fill('#lg-pass', 'wrong'); await p.keyboard.press('Enter'); await p.waitForTimeout(1200);
  const err = await p.textContent('#lg-err'); if (!/Wrong/.test(err)) problems.push('wrong password message: ' + err);
  await p.screenshot({path: OUT + '/login-wrong.png'});
  await p.fill('#lg-pass', 'bosspass'); await p.keyboard.press('Enter'); await p.waitForTimeout(2500);
  if (await p.isVisible('#lg-user')) problems.push('did not sign in');
  const id = await p.evaluate(async () => { await openAccount(); await loadPeopleData(); const j = people.users.find(u => u.username === 'jess'); await openPerson(j.id); return j.id; });
  await p.waitForTimeout(800); await p.locator('text=Library access').scrollIntoViewIfNeeded(); await p.screenshot({path: OUT + '/access-together.png'});
  await p.click('text=Give each person their own library'); await p.waitForTimeout(2000);
  if (!(await p.isVisible('text=All of my music'))) problems.push('separate controls missing');
  await p.locator('text=Library access').scrollIntoViewIfNeeded(); await p.screenshot({path: OUT + '/access-separate.png'});
  await p.click('text=Go back to one shared library'); await p.waitForTimeout(2000);
  if (!(await p.isVisible('text=Give each person their own library'))) problems.push('back to together failed');
  console.log(problems.length ? 'PROBLEMS\n' + problems.join('\n') : 'no problems');
  await b.close();
})();
