import {defineConfig} from 'vite';
import motionCanvasPlugin from '@motion-canvas/vite-plugin';
import ffmpegPlugin from '@motion-canvas/ffmpeg';

// The Motion Canvas plugins are CommonJS; unwrap `default` when loaded as ESM.
const motionCanvas = ((motionCanvasPlugin as any).default ?? motionCanvasPlugin) as typeof motionCanvasPlugin;
const ffmpeg = ((ffmpegPlugin as any).default ?? ffmpegPlugin) as typeof ffmpegPlugin;

export default defineConfig({
  plugins: [motionCanvas({project: './src/project.ts', output: './output'}), ffmpeg()],
  server: {port: 9177, strictPort: false},
});
