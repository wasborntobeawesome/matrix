import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig, type SpringConfig} from 'remotion';
import {useVoiceBars, useVoiceLevel} from './audio';
import {ARABIC_STACK, EMOJI_STACK} from './fonts';
import {LogoMark} from './LogoMark';
import {alpha} from './theme';
import {brand, sec, type Scene} from './timeline';

/* -------------------------------- helpers -------------------------------- */

const GRADIENT = `linear-gradient(100deg, ${brand.primary} 0%, #A78BFA 45%, ${brand.accent} 100%)`;
const SNAPPY: Partial<SpringConfig> = {damping: 14, stiffness: 160, mass: 0.8};
const SMOOTH: Partial<SpringConfig> = {damping: 200};
const POP: Partial<SpringConfig> = {damping: 10, stiffness: 180, mass: 0.7};

const useSpr = (f: number, delay: number, config: Partial<SpringConfig> = SNAPPY) => {
  const {fps} = useVideoConfig();
  return spring({frame: f - delay, fps, config});
};

const isArabic = (s: string) => /[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]/.test(s);
/** Badge for a language chip: known endonyms -> ISO code, otherwise a running number. */
const LANG_CODES: Record<string, string> = {
  'oʻzbekcha': 'UZ',
  "o'zbekcha": 'UZ',
  'o‘zbekcha': 'UZ',
  'ўзбекча': 'UZ',
  'русский': 'RU',
  'english': 'EN',
  'العربية': 'AR',
  'türkçe': 'TR',
  'қазақша': 'KZ',
  'deutsch': 'DE',
  'français': 'FR',
  'español': 'ES',
};
const langCode = (s: string, i: number) => LANG_CODES[s.trim().toLowerCase()] ?? String(i + 1).padStart(2, '0');
const isNumeric = (s: string) => /^\d+([.,]\d+)?$/.test(s);
const EMOJI_RE = /^(\p{Extended_Pictographic}(?:️|‍\p{Extended_Pictographic})*️?)\s*(.*)$/u;

const gradientText: React.CSSProperties = {
  backgroundImage: GRADIENT,
  WebkitBackgroundClip: 'text',
  backgroundClip: 'text',
  color: 'transparent',
};

/** Headline with a per-word masked slide-up. Numbers and the brand name get the gradient. */
const WordReveal: React.FC<{
  text: string;
  f: number;
  delay?: number;
  stagger?: number;
  style?: React.CSSProperties;
}> = ({text, f, delay = 0, stagger = 4, style}) => {
  const words = text.split(/\s+/).filter(Boolean);
  return (
    <div style={{display: 'flex', flexWrap: 'wrap', justifyContent: 'center', columnGap: '0.24em', ...style}}>
      {words.map((w, i) => (
        <Word key={i} w={w} f={f} delay={delay + i * stagger} />
      ))}
    </div>
  );
};

const Word: React.FC<{w: string; f: number; delay: number}> = ({w, f, delay}) => {
  const p = useSpr(f, delay, {damping: 16, stiffness: 140, mass: 0.9});
  // case-sensitive: "Ovoz" (brand) is highlighted, "ovoz" (the word "voice") is not
  const hl = isNumeric(w) || w.replace(/[^\p{L}]/gu, '') === brand.name;
  return (
    <span style={{display: 'inline-block', overflow: 'hidden', padding: '0.06em 0.04em 0.12em', margin: '-0.06em -0.04em -0.12em'}}>
      <span
        style={{
          display: 'inline-block',
          transform: `translateY(${(1 - p) * 110}%) rotate(${(1 - p) * 6}deg)`,
          transformOrigin: 'left bottom',
          ...(hl ? gradientText : {}),
        }}
      >
        {w}
      </span>
    </span>
  );
};

const FadeUp: React.FC<{f: number; delay: number; children: React.ReactNode; style?: React.CSSProperties; dist?: number}> = ({
  f,
  delay,
  children,
  style,
  dist = 40,
}) => {
  const p = useSpr(f, delay, SMOOTH);
  return <div style={{opacity: p, transform: `translateY(${(1 - p) * dist}px)`, ...style}}>{children}</div>;
};

/* ------------------------------ scene shell ------------------------------ */

export const SceneView: React.FC<{scene: Scene; isLast: boolean}> = ({scene, isLast}) => {
  const frame = useCurrentFrame();
  const startF = sec(scene.start);
  const endF = sec(scene.end);
  // entrances start a few frames late so they land as the previous scene clears
  const f = frame - startF - (isFirstScene(scene) ? 0 : 4);
  const exit = isLast
    ? 0
    : interpolate(frame, [endF - 10, endF + 4], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const eased = exit * exit * (3 - 2 * exit);

  const body = (() => {
    switch (scene.layout) {
      case 'hero':
        return <Hero scene={scene} f={f} />;
      case 'chips':
        return <Chips scene={scene} f={f} />;
      case 'stat':
        return <Stat scene={scene} f={f} />;
      case 'list':
        return <List scene={scene} f={f} />;
      case 'cta':
        return <Cta scene={scene} f={f} />;
      default:
        return <Hero scene={scene} f={f} />;
    }
  })();

  return (
    <AbsoluteFill
      style={{
        // content zone: below the header (≈340px) and above the caption pill (1370px)
        top: 380,
        height: 960,
        bottom: 'auto',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '0 70px',
        opacity: 1 - eased,
        transform: `translateY(${-70 * eased}px) scale(${1 - 0.06 * eased})`,
        filter: eased > 0.001 ? `blur(${eased * 14}px)` : undefined,
      }}
    >
      {body}
    </AbsoluteFill>
  );
};

/* --------------------------------- hero --------------------------------- */

const Hero: React.FC<{scene: Scene; f: number}> = ({scene, f}) => {
  const k = useSpr(f, 2, POP);
  return (
    <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center'}}>
      {scene.kicker ? (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 16,
            padding: '16px 32px',
            borderRadius: 999,
            border: `2px solid ${alpha(brand.accent, 0.45)}`,
            background: alpha(brand.accent, 0.1),
            color: brand.accent,
            fontSize: 32,
            fontWeight: 700,
            letterSpacing: '0.22em',
            opacity: Math.min(1, k),
            transform: `scale(${0.6 + 0.4 * k})`,
            marginBottom: 48,
          }}
        >
          <div
            style={{
              width: 14,
              height: 14,
              borderRadius: '50%',
              background: brand.accent,
              boxShadow: `0 0 ${8 + 10 * Math.abs(Math.sin(f / 7))}px ${brand.accent}`,
            }}
          />
          {scene.kicker}
        </div>
      ) : null}
      {scene.headline ? (
        <WordReveal
          text={scene.headline}
          f={f}
          delay={8}
          stagger={6}
          style={{fontSize: 156, fontWeight: 800, lineHeight: 1.02, letterSpacing: '-0.035em', flexDirection: 'column', alignItems: 'center'}}
        />
      ) : null}
      {scene.sub ? (
        <FadeUp f={f} delay={22} style={{marginTop: 40, fontSize: 54, fontWeight: 500, color: brand.muted, letterSpacing: '-0.01em'}}>
          {scene.sub}
        </FadeUp>
      ) : null}
      <FadeUp f={f} delay={30} style={{marginTop: 70}}>
        <Waveform width={820} height={190} bars={34} />
      </FadeUp>
    </div>
  );
};

/** Audio-reactive mirrored bars driven by the actual voice-over. */
const Waveform: React.FC<{width: number; height: number; bars: number}> = ({width, height, bars}) => {
  const frame = useCurrentFrame();
  const values = useVoiceBars(Math.ceil(bars / 2));
  const barW = width / bars - 10;
  // mirror so the centre is loudest
  const half = values.slice().reverse();
  const all = [...half, ...values].slice(0, bars);
  return (
    <div style={{width, height, display: 'flex', alignItems: 'center', justifyContent: 'space-between'}}>
      {all.map((v, i) => {
        const idle = 0.08 + 0.05 * Math.sin(frame / 8 + i * 0.6);
        const h = Math.max(idle, v) * height;
        return (
          <div
            key={i}
            style={{
              width: barW,
              height: Math.max(barW, h),
              borderRadius: barW,
              background: `linear-gradient(180deg, ${brand.accent}, ${brand.primary})`,
              opacity: 0.55 + 0.45 * Math.min(1, v * 1.4),
              boxShadow: `0 0 ${18 * v}px ${alpha(brand.primary, 0.7)}`,
            }}
          />
        );
      })}
    </div>
  );
};

/* --------------------------------- chips --------------------------------- */

const Chips: React.FC<{scene: Scene; f: number}> = ({scene, f}) => {
  const items = scene.items ?? [];
  return (
    <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', width: '100%'}}>
      {scene.headline ? (
        <WordReveal text={scene.headline} f={f} delay={0} stagger={5} style={{fontSize: 220, fontWeight: 800, lineHeight: 1, letterSpacing: '-0.045em'}} />
      ) : null}
      {scene.sub ? (
        <FadeUp f={f} delay={12} style={{marginTop: 24, fontSize: 50, color: brand.muted, fontWeight: 500}}>
          {scene.sub}
        </FadeUp>
      ) : null}
      <div
        style={{
          marginTop: 80,
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 30,
          width: 920,
        }}
      >
        {items.map((it, i) => (
          <Chip key={i} text={it} f={f} delay={16 + i * 11} index={i} />
        ))}
      </div>
    </div>
  );
};

const Chip: React.FC<{text: string; f: number; delay: number; index: number}> = ({text, f, delay, index}) => {
  const p = useSpr(f, delay, POP);
  const glow = interpolate(f - delay, [0, 6, 26], [0, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const ar = isArabic(text);
  const code = langCode(text, index);
  const tint = index % 2 === 0 ? brand.primary : brand.accent;
  return (
    <div
      style={{
        height: 150,
        borderRadius: 44,
        display: 'flex',
        alignItems: 'center',
        gap: 24,
        padding: '0 30px',
        background: `linear-gradient(160deg, ${alpha(brand.surface, 0.95)}, ${alpha(brand.surface, 0.7)})`,
        border: `2px solid ${alpha(tint, 0.25 + 0.6 * glow)}`,
        boxShadow: `0 24px 50px ${alpha('#000000', 0.35)}, 0 0 ${60 * glow}px ${alpha(tint, 0.6 * glow)}`,
        opacity: Math.min(1, p * 1.4),
        transform: `scale(${0.4 + 0.6 * p}) translateY(${(1 - p) * 60}px) rotate(${(1 - p) * (index % 2 ? 8 : -8)}deg)`,
      }}
    >
      <div
        style={{
          width: 84,
          height: 84,
          flexShrink: 0,
          borderRadius: 26,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: `linear-gradient(135deg, ${alpha(tint, 0.9)}, ${alpha(tint, 0.45)})`,
          fontSize: 34,
          fontWeight: 800,
          letterSpacing: "0.04em",
          color: '#FFFFFF',
        }}
      >
        {code}
      </div>
      <div
        dir={ar ? 'rtl' : 'ltr'}
        lang={ar ? 'ar' : undefined}
        style={{
          flex: 1,
          textAlign: 'left',
          fontSize: ar ? 58 : 52,
          fontWeight: 700,
          letterSpacing: ar ? 0 : '-0.015em',
          fontFamily: ar ? ARABIC_STACK : undefined,
          whiteSpace: 'nowrap',
        }}
      >
        {text}
      </div>
    </div>
  );
};

/* --------------------------------- stat --------------------------------- */

const Stat: React.FC<{scene: Scene; f: number}> = ({scene, f}) => {
  const raw = scene.stat ?? '';
  const target = parseFloat(raw.replace(',', '.'));
  const numeric = !Number.isNaN(target) && isNumeric(raw);
  const ring = useSpr(f, 0, SMOOTH);
  const count = interpolate(f, [4, 42], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: (t) => 1 - Math.pow(1 - t, 3)});
  const value = numeric ? Math.round(count * target) : 0;
  const segs = numeric ? Math.max(1, Math.min(24, Math.round(target))) : 12;
  const lit = numeric ? value : Math.round(count * segs);
  const bump = useSpr(f, 42, POP);
  const D = 560;
  const R = 250;
  const C = 2 * Math.PI * R;
  const gapLen = 40;
  const segLen = C / segs - gapLen;

  return (
    <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center'}}>
      <div
        style={{
          position: 'relative',
          width: D,
          height: D,
          opacity: ring,
          transform: `scale(${0.75 + 0.25 * ring}) rotate(${(1 - ring) * -40}deg)`,
        }}
      >
        <svg width={D} height={D} style={{position: 'absolute', inset: 0, transform: 'rotate(-90deg)'}}>
          <defs>
            <linearGradient id="ringGrad" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor={brand.primary} />
              <stop offset="100%" stopColor={brand.accent} />
            </linearGradient>
          </defs>
          {Array.from({length: segs}).map((_, i) => {
            const on = i < lit;
            return (
              <circle
                key={i}
                cx={D / 2}
                cy={D / 2}
                r={R}
                fill="none"
                stroke={on ? 'url(#ringGrad)' : alpha('#FFFFFF', 0.1)}
                strokeWidth={on ? 26 : 18}
                strokeLinecap="round"
                strokeDasharray={`${segLen} ${C - segLen}`}
                strokeDashoffset={-i * (segLen + gapLen) - gapLen / 2}
                style={{filter: on ? `drop-shadow(0 0 10px ${alpha(brand.primary, 0.8)})` : undefined}}
              />
            );
          })}
        </svg>
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 330,
            fontWeight: 800,
            letterSpacing: '-0.05em',
            fontVariantNumeric: 'tabular-nums',
            lineHeight: 1,
            transform: `scale(${1 + 0.08 * Math.sin(Math.min(1, bump) * Math.PI)})`,
          }}
        >
          <span style={{...gradientText, paddingBottom: 20}}>{numeric ? value : raw}</span>
        </div>
      </div>
      {scene.headline ? (
        <WordReveal text={scene.headline} f={f} delay={14} stagger={5} style={{marginTop: 30, fontSize: 104, fontWeight: 800, lineHeight: 1.05, letterSpacing: '-0.035em'}} />
      ) : null}
      {scene.sub ? (
        <FadeUp f={f} delay={26} style={{marginTop: 28, fontSize: 46, fontWeight: 500, color: brand.muted, maxWidth: 900, lineHeight: 1.3}}>
          {scene.sub}
        </FadeUp>
      ) : null}
    </div>
  );
};

/* --------------------------------- list --------------------------------- */

const List: React.FC<{scene: Scene; f: number}> = ({scene, f}) => {
  const items = scene.items ?? [];
  return (
    <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', width: '100%'}}>
      {scene.headline ? (
        <WordReveal
          text={scene.headline}
          f={f}
          delay={0}
          stagger={5}
          style={{fontSize: 112, fontWeight: 800, lineHeight: 1.05, letterSpacing: '-0.035em', textAlign: 'center', maxWidth: 940}}
        />
      ) : null}
      <div style={{marginTop: 64, display: 'flex', flexDirection: 'column', gap: 24, width: 880}}>
        {items.map((it, i) => (
          <ListItem key={i} text={it} f={f} delay={12 + i * 9} index={i} />
        ))}
      </div>
    </div>
  );
};

const ListItem: React.FC<{text: string; f: number; delay: number; index: number}> = ({text, f, delay, index}) => {
  const p = useSpr(f, delay, {damping: 15, stiffness: 150, mass: 0.8});
  const m = text.match(EMOJI_RE);
  const emoji = m ? m[1] : null;
  const label = m ? m[2] : text;
  const tint = [brand.primary, brand.accent, '#60A5FA', '#F472B6'][index % 4];
  const level = useVoiceLevel();
  return (
    <div
      style={{
        height: 128,
        borderRadius: 36,
        display: 'flex',
        alignItems: 'center',
        gap: 28,
        padding: '0 28px',
        background: `linear-gradient(100deg, ${alpha(brand.surface, 0.95)}, ${alpha(brand.surface, 0.6)})`,
        border: `2px solid ${alpha('#FFFFFF', 0.08)}`,
        boxShadow: `0 20px 40px ${alpha('#000000', 0.3)}`,
        opacity: Math.min(1, p * 1.3),
        transform: `translateX(${(1 - p) * (index % 2 ? -160 : 160)}px) scale(${0.9 + 0.1 * p})`,
      }}
    >
      <div
        style={{
          width: 84,
          height: 84,
          flexShrink: 0,
          borderRadius: '50%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: alpha(tint, 0.18),
          border: `2px solid ${alpha(tint, 0.5)}`,
          fontSize: 48,
          fontFamily: EMOJI_STACK,
          lineHeight: 1,
        }}
      >
        {emoji ?? '•'}
      </div>
      <div style={{flex: 1, fontSize: 54, fontWeight: 700, letterSpacing: '-0.015em'}}>{label}</div>
      <div style={{display: 'flex', gap: 6, alignItems: 'center', opacity: 0.8}}>
        {[0, 1, 2].map((b) => (
          <div
            key={b}
            style={{
              width: 8,
              height: 14 + 26 * Math.abs(Math.sin(f / 5 + b * 1.3 + index)) * (0.4 + level),
              borderRadius: 4,
              background: tint,
            }}
          />
        ))}
      </div>
    </div>
  );
};

/* ---------------------------------- cta ---------------------------------- */

const Cta: React.FC<{scene: Scene; f: number}> = ({scene, f}) => {
  const level = useVoiceLevel();
  const logo = useSpr(f, 0, POP);
  const btn = useSpr(f, 18, POP);
  const pulse = f > 40 ? 1 + 0.035 * Math.sin(((f - 40) / 30) * Math.PI * 2 * 1.2) : 1;
  const ph = (i: number) => (Math.sin(f / 3 + i * 1.7) + 1) / 2;
  const bars = [0, 1, 2, 3, 4].map((i) => 0.35 + 0.65 * Math.min(1, level * (0.5 + ph(i)) + 0.15 * ph(i + 2)));
  const ring = (offset: number) => {
    const t = ((f - 18 + offset) % 45) / 45;
    return {scale: 1 + t * 0.5, opacity: (1 - t) * 0.6 * Math.min(1, btn)};
  };

  return (
    <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center'}}>
      <div style={{transform: `scale(${logo}) rotate(${(1 - logo) * -25}deg)`, marginBottom: 56}}>
        <LogoMark size={170} bars={bars} />
      </div>
      {scene.headline ? (
        <WordReveal
          text={scene.headline}
          f={f}
          delay={4}
          stagger={4}
          style={{fontSize: 118, fontWeight: 800, lineHeight: 1.04, letterSpacing: '-0.035em', maxWidth: 900}}
        />
      ) : null}
      {scene.sub ? (
        <FadeUp f={f} delay={12} style={{marginTop: 32, fontSize: 48, fontWeight: 500, color: brand.muted}}>
          {scene.sub}
        </FadeUp>
      ) : null}
      <div style={{position: 'relative', marginTop: 76}}>
        {[0, 22].map((o) => {
          const r = ring(o);
          return f >= 18 ? (
            <div
              key={o}
              style={{
                position: 'absolute',
                inset: 0,
                borderRadius: 999,
                border: `3px solid ${brand.accent}`,
                opacity: r.opacity,
                transform: `scale(${r.scale}, ${1 + (r.scale - 1) * 1.8})`,
              }}
            />
          ) : null;
        })}
        <div
          style={{
            position: 'relative',
            display: 'flex',
            alignItems: 'center',
            gap: 26,
            padding: '34px 84px',
            borderRadius: 999,
            background: GRADIENT,
            fontSize: 72,
            fontWeight: 800,
            letterSpacing: '-0.02em',
            color: '#FFFFFF',
            boxShadow: `0 24px 70px ${alpha(brand.primary, 0.6)}, inset 0 2px 0 ${alpha('#FFFFFF', 0.35)}`,
            opacity: Math.min(1, btn * 1.5),
            transform: `scale(${(0.5 + 0.5 * btn) * pulse})`,
          }}
        >
          {brand.name}
          <span style={{fontSize: 64, transform: `translateX(${Math.sin(f / 6) * 6}px)`, display: 'inline-block'}}>→</span>
        </div>
      </div>
    </div>
  );
};

function isFirstScene(scene: Scene) {
  return scene.start <= 0;
}
