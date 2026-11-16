// v2.6: a mix shows "Watch the video"; turning it on plays a muted video in step with the audio; Save keeps a copy.
// Usage: node test/ui_video.js OUT BASE SONG_ID [user pass]
const { chromium, devices } = require('playwright');
(async () => {
  const [out, base, sid, user, pass] = process.argv.slice(2);
  const b = await chromium.launch({args: ['--autoplay-policy=no-user-gesture-required']}); const probs = [];
  const ctx = await b.newContext({ ...devices['iPhone 13'], colorScheme: 'dark' }); const p = await ctx.newPage();
  p.on('pageerror', e => probs.push('PAGE ERROR ' + e.message));
  await p.goto(base + '/'); await p.waitForTimeout(1200);
  if (user && await p.$('#login:not([hidden])')) { await p.fill('#lg-user', user); await p.fill('#lg-pass', pass); await p.click('#login .btn'); await p.waitForTimeout(2000); }
  await p.evaluate(sid => playList([{nd_id: sid, title: 'Deep Session 01', artist: 'DJ Test', duration: 3600}], 0, {id: 'mx', name: 'DJ Test'}), sid);
  await p.waitForTimeout(1500); await p.evaluate(() => openPlayer());
  await p.waitForSelector('.vid-tg:has-text("Watch the video")', {timeout: 15000});
  await p.screenshot({path: out + '/01-mix-player.png'});
  await p.click('.vid-tg:has-text("Watch the video")'); await p.waitForTimeout(2500);
  const st = await p.evaluate(() => ({inside: !!document.querySelector('.np-art.vid video'), src: vidEl.getAttribute('src'), t: vidEl.currentTime, ready: vidEl.readyState, muted: vidEl.muted}));
  if (!st.inside || !st.src || st.ready < 1 || !st.muted) probs.push('video not showing: ' + JSON.stringify(st));
  await p.screenshot({path: out + '/02-video-on.png'});
  await p.evaluate(() => $('#player').pause()); await p.waitForTimeout(600);
  if (!await p.evaluate(() => vidEl.paused)) probs.push('video kept playing after pause');
  await p.click('.vid-tg:has-text("Save it")'); await p.waitForTimeout(7000);
  if (!await p.$('.vid-tg:has-text("Saved")')) probs.push('save did not finish');
  await p.screenshot({path: out + '/03-saved.png'});
  await p.click('.vid-tg:has-text("Video on")'); await p.waitForTimeout(500);
  if (await p.$('.np-art.vid')) probs.push('video still showing after turning it off');
  console.log(probs.length ? 'UI VIDEO PROBLEMS\n' + probs.join('\n') : 'UI VIDEO OK'); await b.close();
})();
