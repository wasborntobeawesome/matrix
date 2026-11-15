// Headless render: `npm run render` -> ../out/revideo.mp4
import {renderVideo} from '@revideo/renderer';
import {spawnSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

process.env.DISABLE_TELEMETRY = 'true';

const workers = Number(process.env.WORKERS ?? 3);

// Revideo always appends `--single-process` to Chromium's args, and with the
// Chrome build Puppeteer 25 downloads on Windows that crashes the renderer
// ("Navigating frame was detached"). Pretend the flag is already present so it
// is not added.
const chromeArgs = ['--disable-dev-shm-usage'];
const includes = chromeArgs.includes.bind(chromeArgs);
Object.defineProperty(chromeArgs, 'includes', {
  value: (v, ...rest) => v === '--single-process' || includes(v, ...rest),
});

const t0 = performance.now();
// NB: Revideo resolves media for audio extraction as `<outDir>/../public/...`,
// so outDir must sit next to public/; the result is copied to ../out afterwards.
const file = await renderVideo({
  projectFile: './src/project.tsx',
  settings: {
    outFile: 'revideo.mp4',
    outDir: './output',
    workers,
    logProgress: true,
    puppeteer: {args: chromeArgs},
    // Pre-bundle deps up front: otherwise Vite discovers them on first page
    // load and force-reloads the page while Puppeteer is navigating.
    viteConfig: {
      logLevel: 'warn',
      optimizeDeps: {
        include: [
          '@revideo/core',
          '@revideo/2d',
          '@revideo/2d/lib/jsx-dev-runtime',
          '@revideo/renderer/lib/client/render',
        ],
      },
    },
    projectSettings: {
      exporter: {name: '@revideo/core/ffmpeg', options: {format: 'mp4'}},
    },
  },
});
const secs = ((performance.now() - t0) / 1000).toFixed(1);

// Revideo's own audio mix (from the <Audio> node) comes out ~3 dB quieter and
// ~30 ms early (amix + stereo upmix + a 2018 ffmpeg build). To keep the shared
// voice-over bit-for-bit as the source, re-mux the rendered H.264 stream with
// the original WAV, encoded once to AAC. Video is stream-copied (no re-encode).
const outDir = path.resolve('../out');
fs.mkdirSync(outDir, {recursive: true});
const dest = path.join(outDir, 'revideo.mp4');
const ffmpeg = fs.existsSync(path.resolve('../bin/ffmpeg.exe'))
  ? path.resolve('../bin/ffmpeg.exe')
  : (await import('@ffmpeg-installer/ffmpeg')).default.path;
const r = spawnSync(
  ffmpeg,
  ['-v', 'error', '-y', '-i', path.resolve(file), '-i', path.resolve('public/voiceover.wav'),
   '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
   '-movflags', '+faststart', dest],
  {stdio: 'inherit'},
);
if (r.status !== 0) throw new Error('ffmpeg remux failed');
const total = ((performance.now() - t0) / 1000).toFixed(1);
console.log(`Rendered ${dest}: Revideo ${secs}s, total ${total}s (${workers} worker(s))`);
