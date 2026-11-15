import {
  AbsoluteFill,
  Audio,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import {VoiceProvider, useVoiceLevel} from './audio';
import {FONT_STACK, loadFonts} from './fonts';
import {LogoMark} from './LogoMark';
import {SceneView} from './scenes';
import {alpha, SAFE} from './theme';
import {brand, sec, timeline, type Caption} from './timeline';

loadFonts();

export const Promo: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <VoiceProvider>
      <AbsoluteFill
        style={{
          backgroundColor: brand.background,
          fontFamily: FONT_STACK,
          color: brand.text,
          overflow: 'hidden',
        }}
      >
        <Background />
        {timeline.scenes.map((scene, i) => {
          const startF = sec(scene.start);
          const endF = sec(scene.end);
          const isLast = i === timeline.scenes.length - 1;
          // render a little past the scene end so the exit overlaps the next entry
          if (frame < startF || (!isLast && frame > endF + 5)) return null;
          return <SceneView key={scene.id} scene={scene} isLast={isLast} />;
        })}
        <Header />
        <Captions />
        <Audio src={staticFile('voiceover.wav')} />
      </AbsoluteFill>
    </VoiceProvider>
  );
};

/* ----------------------------- background ----------------------------- */

const ANCHORS: [number, number][] = [
  [0.25, 0.3],
  [0.78, 0.36],
  [0.5, 0.4],
  [0.2, 0.52],
  [0.5, 0.48],
];

const Background: React.FC = () => {
  const frame = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const level = useVoiceLevel();
  const t = frame / 30;

  // the main glow travels to a new anchor at every scene change
  const starts = timeline.scenes.map((s) => sec(s.start));
  const pick = (k: 0 | 1) =>
    starts.length > 1
      ? interpolate(
          frame,
          starts.flatMap((s, i) => (i === 0 ? [s] : [s - 8, s + 22])),
          starts.flatMap((_, i) =>
            i === 0 ? [ANCHORS[0][k]] : [ANCHORS[(i - 1) % ANCHORS.length][k], ANCHORS[i % ANCHORS.length][k]],
          ),
          {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
        )
      : ANCHORS[0][k];
  const ax = pick(0);
  const ay = pick(1);

  const blob = (x: number, y: number, size: number, color: string, opacity: number): React.CSSProperties => ({
    position: 'absolute',
    left: x * width - size / 2,
    top: y * height - size / 2,
    width: size,
    height: size,
    borderRadius: '50%',
    background: `radial-gradient(circle, ${alpha(color, opacity)} 0%, ${alpha(color, 0)} 68%)`,
  });

  return (
    <AbsoluteFill>
      <div style={blob(ax + Math.sin(t * 0.7) * 0.05, ay + Math.cos(t * 0.5) * 0.04, 1500, brand.primary, 0.4 + level * 0.15)} />
      <div
        style={blob(1 - ax + Math.cos(t * 0.6) * 0.06, 0.95 - ay * 0.6 + Math.sin(t * 0.8) * 0.04, 1300, brand.accent, 0.2 + level * 0.1)}
      />
      <div style={blob(0.5 + Math.sin(t * 0.4) * 0.25, 0.05, 900, '#3B82F6', 0.14)} />
      {/* drifting dot grid, masked to fade towards the edges */}
      <AbsoluteFill
        style={{
          backgroundImage: `radial-gradient(${alpha('#FFFFFF', 0.08)} 1.6px, transparent 1.9px)`,
          backgroundSize: '44px 44px',
          backgroundPosition: `0px ${-((frame * 0.6) % 44)}px`,
          maskImage: 'radial-gradient(ellipse 70% 50% at 50% 42%, black 15%, transparent 78%)',
          WebkitMaskImage: 'radial-gradient(ellipse 70% 50% at 50% 42%, black 15%, transparent 78%)',
        }}
      />
      {/* vignette */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(ellipse 95% 75% at 50% 45%, transparent 55%, ${alpha(brand.background, 0.9)} 100%)`,
        }}
      />
    </AbsoluteFill>
  );
};

/* ------------------------- header + progress bar ------------------------- */

const Header: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();
  const level = useVoiceLevel();
  const enter = spring({frame, fps, config: {damping: 200}, durationInFrames: 20});
  const scenes = timeline.scenes;
  const found = scenes.findIndex((s) => frame < sec(s.end));
  const idx = found === -1 ? scenes.length - 1 : found;
  const pad = 72;
  const gap = 10;
  const usable = width - pad * 2 - gap * (scenes.length - 1);
  const ph = (i: number) => (Math.sin(frame / 3 + i * 1.7) + 1) / 2;
  const logoBars = [0, 1, 2, 3, 4].map((i) => 0.3 + 0.7 * Math.min(1, level * (0.4 + ph(i))));

  return (
    <div
      style={{
        position: 'absolute',
        top: SAFE.top + 16,
        left: pad,
        right: pad,
        opacity: enter,
        transform: `translateY(${(1 - enter) * -30}px)`,
      }}
    >
      {/* segmented, story-style progress bar (segment width = scene length) */}
      <div style={{display: 'flex', gap}}>
        {scenes.map((s) => {
          const w = (usable * (s.end - s.start)) / timeline.duration;
          const p = interpolate(frame, [sec(s.start), sec(s.end)], [0, 1], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
          });
          return (
            <div key={s.id} style={{width: w, height: 6, borderRadius: 3, background: alpha('#FFFFFF', 0.16), overflow: 'hidden'}}>
              <div
                style={{
                  width: `${p * 100}%`,
                  height: '100%',
                  borderRadius: 3,
                  background: `linear-gradient(90deg, ${brand.primary}, ${brand.accent})`,
                }}
              />
            </div>
          );
        })}
      </div>
      <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 30}}>
        <div style={{display: 'flex', alignItems: 'center', gap: 18}}>
          <LogoMark size={64} bars={logoBars} />
          <div style={{fontSize: 42, fontWeight: 800, letterSpacing: '-0.02em'}}>{brand.name}</div>
        </div>
        <div style={{fontSize: 30, fontWeight: 600, color: brand.muted, fontVariantNumeric: 'tabular-nums', letterSpacing: '0.08em'}}>
          <span style={{color: brand.text}}>{String(idx + 1).padStart(2, '0')}</span> / {String(scenes.length).padStart(2, '0')}
        </div>
      </div>
    </div>
  );
};

/* ------------------------------- captions ------------------------------- */

const Captions: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const caps = timeline.captions;
  const i = caps.findIndex((c) => frame >= sec(c.start) && frame < sec(c.end));
  if (i === -1) return null;
  const cap: Caption = caps[i];

  // the pill "opens" at the first caption after a pause, then only the text swaps
  let first = i;
  while (first > 0 && sec(caps[first - 1].end) >= sec(caps[first].start) - 1) first--;
  let last = i;
  while (last < caps.length - 1 && sec(caps[last + 1].start) <= sec(caps[last].end) + 1) last++;

  const open = spring({frame: frame - sec(caps[first].start), fps, config: {damping: 14, stiffness: 180}});
  const close = interpolate(frame, [sec(caps[last].end) - 5, sec(caps[last].end)], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const word = spring({frame: frame - sec(cap.start), fps, config: {damping: 16, stiffness: 220}});

  return (
    <div
      style={{
        position: 'absolute',
        left: 60,
        right: 60,
        top: SAFE.captionTop,
        display: 'flex',
        justifyContent: 'center',
        opacity: Math.min(open, close),
        transform: `scale(${0.85 + 0.15 * Math.min(open, 1.05)})`,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 22,
          maxWidth: 940,
          padding: '24px 46px',
          borderRadius: 999,
          background: alpha('#0A0E1C', 0.8),
          border: `2px solid ${alpha('#FFFFFF', 0.12)}`,
          boxShadow: `0 20px 60px ${alpha('#000000', 0.45)}, inset 0 1px 0 ${alpha('#FFFFFF', 0.08)}`,
        }}
      >
        <div
          style={{
            width: 16,
            height: 16,
            flexShrink: 0,
            borderRadius: '50%',
            background: brand.accent,
            boxShadow: `0 0 ${10 + 14 * Math.abs(Math.sin(frame / 6))}px ${brand.accent}`,
          }}
        />
        <div
          key={i}
          style={{
            fontSize: 50,
            fontWeight: 700,
            lineHeight: 1.15,
            letterSpacing: '-0.01em',
            textAlign: 'center',
            opacity: word,
            transform: `translateY(${(1 - word) * 18}px)`,
          }}
        >
          {cap.text}
        </div>
      </div>
    </div>
  );
};
