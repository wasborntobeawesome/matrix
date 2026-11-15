// Copies the shared inputs (timeline, voice-over, fonts) into this project and
// pre-computes a per-frame loudness envelope of the voice-over that drives the
// on-screen waveform. Run automatically by `npm run render`.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const shared = path.resolve(root, '../shared');

const copy = (from, to) => {
  fs.mkdirSync(path.dirname(to), {recursive: true});
  fs.copyFileSync(from, to);
};

const timeline = JSON.parse(
  fs.readFileSync(path.join(shared, 'build/timeline.json'), 'utf8'),
);
fs.writeFileSync(
  path.join(root, 'src/timeline.json'),
  JSON.stringify(timeline, null, 2),
);
copy(path.join(shared, 'build', timeline.audio), path.join(root, 'public/voiceover.wav'));
copy(path.join(shared, 'fonts/Inter.ttf'), path.join(root, 'public/fonts/Inter.ttf'));
copy(
  path.join(shared, 'fonts/NotoSansArabic.ttf'),
  path.join(root, 'public/fonts/NotoSansArabic.ttf'),
);

// --- loudness envelope (RMS per video frame, normalised 0..1) -------------
const wav = fs.readFileSync(path.join(shared, 'build', timeline.audio));
let off = 12;
let fmt = null;
let data = null;
while (off < wav.length - 8) {
  const id = wav.toString('ascii', off, off + 4);
  const size = wav.readUInt32LE(off + 4);
  if (id === 'fmt ') {
    fmt = {
      format: wav.readUInt16LE(off + 8),
      channels: wav.readUInt16LE(off + 10),
      rate: wav.readUInt32LE(off + 12),
      bits: wav.readUInt16LE(off + 22),
    };
  } else if (id === 'data') {
    data = wav.subarray(off + 8, off + 8 + size);
  }
  off += 8 + size + (size % 2);
}
if (!fmt || !data) throw new Error('Could not parse voice-over WAV');
const bytes = fmt.bits / 8;
const frameSize = bytes * fmt.channels;
const total = Math.floor(data.length / frameSize);
const sample = i => {
  const p = i * frameSize;
  if (fmt.format === 3) return data.readFloatLE(p);
  if (fmt.bits === 16) return data.readInt16LE(p) / 32768;
  if (fmt.bits === 24) return data.readIntLE(p, 3) / 8388608;
  if (fmt.bits === 32) return data.readInt32LE(p) / 2147483648;
  throw new Error(`Unsupported WAV bit depth ${fmt.bits}`);
};
const perFrame = fmt.rate / timeline.fps;
const env = [];
for (let f = 0; f * perFrame < total; f++) {
  let sum = 0;
  let n = 0;
  for (let i = Math.floor(f * perFrame); i < Math.min(total, (f + 1) * perFrame); i++) {
    const s = sample(i);
    sum += s * s;
    n++;
  }
  env.push(Math.sqrt(sum / Math.max(1, n)));
}
const max = Math.max(...env) || 1;
fs.writeFileSync(
  path.join(root, 'src/envelope.json'),
  JSON.stringify(env.map(v => +(v / max).toFixed(3))),
);
console.log(
  `prepared assets: ${timeline.scenes.length} scenes, ${env.length} envelope frames, ` +
    `${(total / fmt.rate).toFixed(3)}s audio`,
);
