// Headless render entry: bypasses the editor UI and drives Motion Canvas's own
// Renderer + the official FFmpeg exporter directly. Loaded by render.html,
// opened by render.mjs in headless Chromium.
import project from './project.ts?project';
import {Renderer, RendererResult, Vector2} from '@motion-canvas/core';
import timeline from '../assets/timeline.json';

const w = window as any;
const status = document.getElementById('status')!;
const renderer = new Renderer(project);
const total = Math.round(timeline.duration * timeline.fps);

renderer.onFrameChanged.subscribe(frame => {
  w.__frame = frame;
  status.textContent = `frame ${frame} / ${total}`;
});
renderer.onFinished.subscribe(result => {
  w.__result = RendererResult[result];
  status.textContent = `finished: ${w.__result}`;
});
project.logger.onLogged.subscribe((log: any) => {
  console.log(`[mc:${log.level}] ${log.message}${log.stack ? '\n' + log.stack : ''}`);
});

const base = project.meta.getFullRenderingSettings();
w.__ready = true;
await renderer.render({
  ...base,
  name: 'motion-canvas',
  size: new Vector2(timeline.width, timeline.height),
  fps: timeline.fps,
  resolutionScale: 1,
  range: [0, Infinity],
  exporter: {
    name: '@motion-canvas/ffmpeg',
    options: {fastStart: true, includeAudio: true},
  },
});
