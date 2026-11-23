// v2.6: a mix shows "Watch the video"; turning it on plays a muted video in step with the audio; Save keeps a copy.
// Usage: node test/ui_video.js OUT BASE MIX_ID user pass SONG_ID
const { chromium, devices } = require('playwright');
(async () => {
  const [out, base, sid, user, pass, songId] = process.argv.slice(2);
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
  // music videos for songs: off by default (data); on, a song shows its official video, lined up (+2 s in the test)
  await p.evaluate(() => { videoOn = false; store.set('umc_video', false); });
  const ngud = {id: songId, title: 'Ngud', artist: 'Kwesta', duration: 6};
  await p.evaluate(s => playList([{nd_id: s.id, title: s.title, artist: s.artist, duration: s.duration}], 0, {id: 'x', name: 'x'}), ngud);
  await p.evaluate(() => openPlayer()); await p.waitForTimeout(2500);
  if (await p.$('.vid-tg')) probs.push('a song offers a video with music videos off');
  await p.evaluate(() => setSongVid(true)); await p.waitForTimeout(500);
  await p.waitForSelector('.vid-tg:has-text("Watch the video")', {timeout: 20000}).catch(() => probs.push('no video button on a song with music videos on'));
  let failed = 0;                                       // the first try at the video fails upstream: it tries again
  await p.route(u => u.pathname === '/api/video/' + songId, r => { if (!failed++) return r.fulfill({status: 502, body: 'upstream'}); return r.continue(); });
  if (await p.$('.vid-tg:has-text("Watch the video")')) {
    await p.click('.vid-tg:has-text("Watch the video")');
    await p.evaluate(() => { $('#player').currentTime = 1; $('#player').play().catch(() => {}); }); await p.waitForTimeout(2500);
    const sy = await p.evaluate(() => ({a: $('#player').currentTime, v: vidEl.currentTime, off: vidInfo[pq[pi].nd_id]?.offset}));
    if (sy.off !== 2 || Math.abs(sy.v - (sy.a + 2)) > 0.6) probs.push('music video not lined up with the song: ' + JSON.stringify(sy));
    if (failed < 2) probs.push('the video was not tried again after a failed first load (' + failed + ')');
    if (!await p.$('.np-art.vid video')) probs.push('a failed first load gave up on the video');
    await p.screenshot({path: out + '/04-song-video.png'});
    // full screen on an iPhone: inside the page, silent, in step, and a way out
    await p.click('button[aria-label="Full screen"]'); await p.evaluate(() => { $('#player').currentTime = 0.5; }); await p.waitForTimeout(1500);
    const fs = await p.evaluate(() => { const r = document.querySelector('.np-art.vid').getBoundingClientRect();
      return {page: document.body.classList.contains('vfull'), native: !!document.fullscreenElement, w: r.width, h: r.height, vw: innerWidth, vh: innerHeight, muted: vidEl.muted,
              d: vidEl.currentTime - $('#player').currentTime}; });
    if (!fs.page || fs.native) probs.push('iPhone full screen should fill the page, not the phone player: ' + JSON.stringify(fs));
    if (fs.w < fs.vw - 2 || fs.h < fs.vh - 2) probs.push('full screen video does not fill the screen: ' + JSON.stringify(fs));
    if (!fs.muted) probs.push('video has its own sound in full screen');
    if (Math.abs(fs.d - 2) > 0.6) probs.push('out of step in full screen: ' + JSON.stringify(fs));
    await p.screenshot({path: out + '/05-iphone-full.png'});
    await p.evaluate(() => { vidEl.muted = false; }); await p.waitForTimeout(300);
    if (!await p.evaluate(() => vidEl.muted)) probs.push('the video could be unmuted');
    await p.click('#vfull-x'); await p.waitForTimeout(500);
    if (await p.evaluate(() => document.body.classList.contains('vfull'))) probs.push('could not leave full screen');
  }
  await p.evaluate(() => setSongVid(false));
  console.log(probs.length ? 'UI VIDEO PROBLEMS\n' + probs.join('\n') : 'UI VIDEO OK'); await b.close();
})();
