// The shared timeline is copied into public/ by scripts/prepare.mjs and bundled
// at build time, so composition size/fps/duration always follow timeline.json.
import raw from '../public/timeline.json';

export type Layout = 'hero' | 'chips' | 'stat' | 'list' | 'cta';

export type Scene = {
  id: string;
  layout: Layout;
  kicker?: string;
  headline?: string;
  sub?: string;
  stat?: string;
  items?: string[];
  start: number;
  end: number;
  speech_end?: number;
};

export type Caption = {text: string; start: number; end: number};

export type Timeline = {
  title: string;
  width: number;
  height: number;
  fps: number;
  duration: number;
  frames?: number;
  brand: {
    name: string;
    background: string;
    surface: string;
    primary: string;
    accent: string;
    text: string;
    muted: string;
    font: string;
  };
  audio: string;
  scenes: Scene[];
  captions: Caption[];
};

export const timeline = raw as unknown as Timeline;
export const brand = timeline.brand;
export const FPS = timeline.fps;
export const DURATION_FRAMES =
  timeline.frames ?? Math.ceil(timeline.duration * timeline.fps);
export const sec = (s: number) => Math.round(s * FPS);
