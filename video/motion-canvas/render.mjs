// Headless, repeatable Motion Canvas render.
//
//   npm run render            -> ../out/motion-canvas.mp4
//   npm run render -- --preview  also extracts mid-scene PNGs into ./preview
//
// 1. copies the shared inputs (timeline, voice-over, fonts) into ./assets
// 2. starts the Vite dev server (Motion Canvas only renders via its Vite plugin)
// 3. opens render.html in headless Chromium (Playwright); that page runs the
//    official Motion Canvas `Renderer` with the official `@motion-canvas/ffmpeg`
//    exporter - frames stream over the Vite websocket to ffmpeg on the server,
//    which also muxes the voice-over
// 4. copies output/motion-canvas.mp4 to ../out/motion-canvas.mp4 and verifies it
import {createServer} from 'vite';
import {chromium} from 'playwright';
import {execFileSync, spawnSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const videoDir = path.resolve(here, '..');
const shared = path.join(videoDir, 'shared');
const ffmpegExe = path.join(videoDir, 'bin', process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg');
const outFile = path.join(videoDir, 'out', 'motion-canvas.mp4');
const wantPreview = process.argv.includes('--preview');

process.chdir(here); // the ffmpeg exporter resolves audio/output relative to cwd
const t0 = performance.now();
const secs = () => ((performance.now() - t0) / 1000).toFixed(1);

// 1. shared inputs ----------------------------------------------------------
fs.mkdirSync(path.join(here, 'assets'), {recursive: true});
for (const [src, dst] of [
  [path.join(shared, 'build', 'timeline.json'), 'timeline.json'],
  [path.join(shared, 'build', 'voiceover.wav'), 'voiceover.wav'],
  [path.join(shared, 'fonts', 'Inter.ttf'), 'Inter.ttf'],
  [path.join(shared, 'fonts', 'NotoSansArabic.ttf'), 'NotoSansArabic.ttf'],
]) {
  fs.copyFileSync(src, path.join(here, 'assets', dst));
}
const timeline = JSON.parse(fs.readFileSync(path.join(here, 'assets', 'timeline.json'), 'utf8'));
const totalFrames = Math.round(timeline.duration * timeline.fps);
const produced = path.join(here, 'output', 'motion-canvas.mp4');
fs.rmSync(produced, {force: true});

// 2. vite -------------------------------------------------------------------
const server = await createServer({
  root: here,
  configFile: path.join(here, 'vite.config.ts'),
  logLevel: 'warn',
  server: {port: 9177, strictPort: false, open: false},
});
await server.listen();
const base = server.resolvedUrls.local[0];
console.log(`[${secs()}s] vite ready at ${base}`);

// 3. headless chromium ------------------------------------------------------
const browser = await chromium.launch({headless: true});
let failed = null;
try {
  const page = await browser.newPage({viewport: {width: 800, height: 600}});
  page.on('console', m => {
    const t = m.text();
    if (/error|warn|\[mc:/i.test(t) && !/\[vite\]/.test(t)) console.log('  [page]', t);
  });
  page.on('pageerror', e => console.log('  [pageerror]', e.message));
  await page.goto(new URL('render.html', base).href);
  await page.waitForFunction(() => (window).__ready === true, null, {timeout: 120_000});
  const tRender = performance.now();
  console.log(`[${secs()}s] rendering ${totalFrames} frames…`);
  let last = -1;
  for (;;) {
    const {frame, result} = await page.evaluate(() => ({
      frame: (window).__frame ?? 0,
      result: (window).__result,
    }));
    if (result) {
      if (result !== 'Success') failed = `renderer finished with ${result}`;
      break;
    }
    if (frame - last >= 60) {
      last = frame;
      console.log(`  frame ${frame}/${totalFrames}`);
    }
    await new Promise(r => setTimeout(r, 500));
  }
  console.log(`[${secs()}s] browser render done in ${((performance.now() - tRender) / 1000).toFixed(1)}s`);
} finally {
  await browser.close();
  // give ffmpeg (spawned by the vite server) a moment to finalise the file
  for (let i = 0; i < 60 && !fs.existsSync(produced); i++) await new Promise(r => setTimeout(r, 250));
  await new Promise(r => setTimeout(r, 500));
  await server.close();
}
if (failed) throw new Error(failed);
if (!fs.existsSync(produced)) throw new Error(`no output at ${produced}`);

// 4. publish + verify ---------------------------------------------------------
fs.mkdirSync(path.dirname(outFile), {recursive: true});
fs.copyFileSync(produced, outFile);
const info = spawnSync(ffmpegExe, ['-hide_banner', '-i', outFile], {encoding: 'utf8'}).stderr;
console.log(info.split('\n').filter(l => /Duration|Stream/.test(l)).join('\n'));
console.log(`[${secs()}s] wrote ${outFile} (${(fs.statSync(outFile).size / 1e6).toFixed(2)} MB)`);

if (wantPreview) {
  const dir = path.join(here, 'preview');
  fs.mkdirSync(dir, {recursive: true});
  for (const s of timeline.scenes) {
    const t = (s.start + s.end) / 2;
    execFileSync(ffmpegExe, ['-v', 'error', '-y', '-ss', t.toFixed(3), '-i', outFile, '-frames:v', '1', path.join(dir, `${s.id}.png`)]);
  }
  console.log(`preview frames in ${dir}`);
}
