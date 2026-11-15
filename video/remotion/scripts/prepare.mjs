// Copies the shared inputs (timeline, voice-over, fonts) into public/ so that
// Remotion can serve them via staticFile(). Run automatically by npm scripts.
import {copyFileSync, mkdirSync, existsSync} from 'node:fs';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const shared = resolve(root, '..', 'shared');
const pub = join(root, 'public');
mkdirSync(join(pub, 'fonts'), {recursive: true});
mkdirSync(resolve(root, '..', 'out'), {recursive: true});

const files = [
  [join(shared, 'build', 'timeline.json'), join(pub, 'timeline.json')],
  [join(shared, 'build', 'voiceover.wav'), join(pub, 'voiceover.wav')],
  [join(shared, 'fonts', 'Inter.ttf'), join(pub, 'fonts', 'Inter.ttf')],
  [join(shared, 'fonts', 'NotoSansArabic.ttf'), join(pub, 'fonts', 'NotoSansArabic.ttf')],
];
for (const [from, to] of files) {
  if (!existsSync(from)) throw new Error(`Missing shared input: ${from}`);
  copyFileSync(from, to);
  console.log('copied', from, '->', to);
}
