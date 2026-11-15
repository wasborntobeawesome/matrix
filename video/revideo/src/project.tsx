/**
 * Ovoz promo reel — Revideo implementation.
 *
 * Everything is driven by ../shared/build/timeline.json (copied to
 * src/timeline.json by scripts/prepare-assets.mjs): scene timing, texts,
 * captions and brand colours. The voice-over is played with <Audio>.
 */
import {
  Audio,
  Circle,
  Gradient,
  Grid,
  Layout,
  Node,
  Polygon,
  Rect,
  Txt,
  makeScene2D,
} from '@revideo/2d';
import {
  all,
  createRef,
  createSignal,
  easeInCubic,
  easeOutBack,
  easeOutCubic,
  linear,
  makeProject,
  tween,
  useThread,
  waitFor,
} from '@revideo/core';
import type {ThreadGenerator} from '@revideo/core';
import envelope from './envelope.json';
import timeline from './timeline.json';

// ---------------------------------------------------------------- fonts ----
// Load the shared fonts before the first frame is measured (top-level await is
// fine: the project module is imported by Vite as native ESM in Chromium).
const fontFaces = [
  new FontFace('Inter', 'url(/fonts/Inter.ttf)', {weight: '100 900'}),
  new FontFace('Noto Sans Arabic', 'url(/fonts/NotoSansArabic.ttf)', {
    weight: '100 900',
  }),
];
await Promise.all(
  fontFaces.map(async f => {
    await f.load();
    document.fonts.add(f);
  }),
);
await document.fonts.ready;

// --------------------------------------------------------------- tokens ----
type Scene = (typeof timeline.scenes)[number] & {
  kicker?: string;
  sub?: string;
  items?: string[];
  stat?: string;
};
type Caption = (typeof timeline.captions)[number];

const B = timeline.brand;
const W = timeline.width;
const H = timeline.height;
const FPS = timeline.fps;
const DUR = timeline.duration;
const SCENES = timeline.scenes as Scene[];
const CAPTIONS = timeline.captions as Caption[];
const ENV = envelope as number[];

const FONT = `Inter, "Noto Sans Arabic", "Segoe UI Emoji", "Segoe UI Symbol", sans-serif`;
const EMOJI = `"Segoe UI Emoji", "Noto Color Emoji", "Apple Color Emoji", sans-serif`;

const rgba = (hex: string, a: number) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
};
const brandGradient = (w: number, h = 0) =>
  new Gradient({
    type: 'linear',
    from: [-w / 2, -h / 2],
    to: [w / 2, h / 2],
    stops: [
      {offset: 0, color: B.primary},
      {offset: 1, color: B.accent},
    ],
  });
const textGradient = (w: number) =>
  new Gradient({
    type: 'linear',
    from: [-w / 2, 0],
    to: [w / 2, 0],
    stops: [
      {offset: 0, color: '#B9A8FF'},
      {offset: 0.55, color: B.primary},
      {offset: 1, color: B.accent},
    ],
  });

// Safe zone for 9:16 reels (scene origin is the frame centre).
const SAFE_TOP = -H / 2 + 220; // -740
const SAFE_BOTTOM = H / 2 - 380; // 580
const CAPTION_Y = 440;
const WAVE_Y = 292;
const PROGRESS_Y = SAFE_BOTTOM - 14;

// ----------------------------------------------------------- utilities ----
/** Waits until an absolute time (seconds since the start of the video). */
function* at(t: number): ThreadGenerator {
  const now = useThread().time();
  if (t > now + 1e-4) yield* waitFor(t - now);
}

/** Fade + rise + scale an element into its resting position. */
function* rise(
  node: Node,
  {y = node.y(), dy = 60, s0 = 0.94, d = 0.6} = {},
): ThreadGenerator {
  node.y(y + dy);
  node.scale(s0);
  yield* all(
    node.opacity(1, d * 0.6, easeOutCubic),
    node.y(y, d, easeOutCubic),
    node.scale(1, d, easeOutBack),
  );
}

/** Pop with overshoot (chips, buttons). */
function* pop(node: Node, d = 0.5): ThreadGenerator {
  node.scale(0.6);
  yield* all(node.opacity(1, d * 0.5, easeOutCubic), node.scale(1, d, easeOutBack));
}

/** Slide in from the right (list rows). */
function* slideIn(node: Node, x = 0, d = 0.55): ThreadGenerator {
  node.x(x + 120);
  yield* all(node.opacity(1, d * 0.6, easeOutCubic), node.x(x, d, easeOutCubic));
}

/**
 * Finds when a keyword is spoken, using the caption chunks. If a chunk holds
 * several keywords, the time is interpolated by character position.
 */
function cue(keyword: string, fallback: number): number {
  const norm = (s: string) =>
    s.toLowerCase().replace(/[ʻ‘’']/g, '').normalize('NFC');
  const k = norm(keyword);
  for (const c of CAPTIONS) {
    const text = norm(c.text);
    const idx = text.indexOf(k);
    if (idx >= 0) return c.start + (c.end - c.start) * (idx / text.length) * 0.9;
  }
  return fallback;
}

/** Dashed placeholder slot shown before a chip / row is spoken. */
const ghost = (y: number, width: number, height: number, radius: number) => (
  <Rect
    y={y}
    width={width}
    height={height}
    radius={radius}
    fill={'rgba(255,255,255,0.025)'}
    stroke={'rgba(255,255,255,0.12)'}
    lineWidth={2}
    lineDash={[12, 10]}
  />
);

/** Pseudo-random but deterministic. */
const rand = (i: number) => {
  const x = Math.sin(i * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
};

// ---------------------------------------------------------------- scene ----
const promo = makeScene2D('promo', function* (view) {
  const T = createSignal(0); // global clock, 0..DUR
  const env = () => {
    const f = Math.min(ENV.length - 1, Math.max(0, Math.floor(T() * FPS)));
    const a = ENV[Math.max(0, f - 1)] ?? 0;
    const b = ENV[f] ?? 0;
    const c = ENV[Math.min(ENV.length - 1, f + 1)] ?? 0;
    return (a + 2 * b + c) / 4;
  };

  view.fill(B.background);

  // ---------------------------------------------------------- backdrop --
  view.add(
    <Node>
      <Grid
        width={W + 400}
        height={H + 400}
        spacing={96}
        stroke={'rgba(255,255,255,0.035)'}
        lineWidth={2}
        y={() => ((T() * 24) % 96) - 48}
      />
      <Circle
        size={1500}
        x={() => -300 + Math.sin(T() * 0.35) * 140}
        y={() => -560 + Math.cos(T() * 0.28) * 110}
        opacity={() => 0.85 + env() * 0.15}
        fill={
          new Gradient({
            type: 'radial',
            fromRadius: 0,
            toRadius: 750,
            stops: [
              {offset: 0, color: rgba(B.primary, 0.42)},
              {offset: 1, color: rgba(B.primary, 0)},
            ],
          })
        }
      />
      <Circle
        size={1400}
        x={() => 340 + Math.cos(T() * 0.3) * 120}
        y={() => 420 + Math.sin(T() * 0.25) * 140}
        fill={
          new Gradient({
            type: 'radial',
            fromRadius: 0,
            toRadius: 700,
            stops: [
              {offset: 0, color: rgba(B.accent, 0.26)},
              {offset: 1, color: rgba(B.accent, 0)},
            ],
          })
        }
      />
      <Circle
        size={1100}
        x={() => Math.sin(T() * 0.22 + 1) * 200}
        y={() => -80 + Math.cos(T() * 0.2) * 160}
        fill={
          new Gradient({
            type: 'radial',
            fromRadius: 0,
            toRadius: 550,
            stops: [
              {offset: 0, color: 'rgba(56,80,255,0.20)'},
              {offset: 1, color: 'rgba(56,80,255,0)'},
            ],
          })
        }
      />
      {Array.from({length: 28}, (_, i) => {
        const speed = 18 + rand(i) * 40;
        const x0 = (rand(i + 50) - 0.5) * W;
        const y0 = rand(i + 99) * H;
        return (
          <Circle
            size={3 + rand(i + 7) * 6}
            fill={i % 3 === 0 ? B.accent : '#ffffff'}
            x={() => x0 + Math.sin(T() * 0.6 + i) * 18}
            y={() => (((y0 - T() * speed) % H) + H) % H - H / 2}
            opacity={0.05 + rand(i + 3) * 0.2}
          />
        );
      })}
      {/* vignette */}
      <Rect
        width={W}
        height={H}
        fill={
          new Gradient({
            type: 'radial',
            fromRadius: 300,
            toRadius: 1150,
            stops: [
              {offset: 0, color: rgba(B.background, 0)},
              {offset: 1, color: rgba(B.background, 0.85)},
            ],
          })
        }
      />
    </Node>,
  );

  // ------------------------------------------------------------ header --
  const header = createRef<Layout>();
  view.add(
    <Layout
      ref={header}
      layout
      direction={'row'}
      alignItems={'center'}
      gap={18}
      y={SAFE_TOP + 70}
      opacity={0}
    >
      <Rect
        size={62}
        radius={18}
        fill={brandGradient(62, 62)}
        layout
        direction={'row'}
        alignItems={'center'}
        justifyContent={'center'}
        gap={5}
      >
        {[18, 32, 24, 14].map((h, i) => (
          <Rect
            width={6}
            radius={3}
            fill={'#ffffff'}
            height={() => h * (0.75 + 0.45 * env() * (0.6 + 0.4 * Math.sin(T() * 12 + i * 1.7)))}
          />
        ))}
      </Rect>
      <Txt
        text={B.name}
        fontFamily={FONT}
        fontWeight={800}
        fontSize={42}
        fill={B.text}
        letterSpacing={0.5}
      />
    </Layout>,
  );

  // ------------------------------------------------------- scene groups --
  const groups = SCENES.map(() => createRef<Node>());
  const builders: Record<string, (s: Scene, i: number) => ThreadGenerator> = {};

  // hero ------------------------------------------------------------------
  builders.hero = function* (s, i) {
    const kicker = createRef<Rect>();
    const l1 = createRef<Txt>();
    const l2 = createRef<Txt>();
    const sub = createRef<Txt>();
    const [first, ...rest] = s.headline.split(' ');
    groups[i]().add(
      <>
        <Rect
          ref={kicker}
          y={-380}
          opacity={0}
          layout
          direction={'row'}
          alignItems={'center'}
          gap={14}
          padding={[14, 30]}
          radius={999}
          fill={rgba(B.accent, 0.1)}
          stroke={rgba(B.accent, 0.45)}
          lineWidth={2}
        >
          <Circle size={12} fill={B.accent} />
          <Txt
            text={s.kicker ?? ''}
            fontFamily={FONT}
            fontWeight={700}
            fontSize={30}
            letterSpacing={6}
            fill={B.accent}
          />
        </Rect>
        <Txt
          ref={l1}
          y={-215}
          opacity={0}
          text={first}
          fontFamily={FONT}
          fontWeight={800}
          fontSize={210}
          letterSpacing={-6}
          fill={textGradient(520)}
          shadowColor={rgba(B.primary, 0.55)}
          shadowBlur={60}
        />
        <Txt
          ref={l2}
          y={-30}
          opacity={0}
          text={rest.join(' ')}
          fontFamily={FONT}
          fontWeight={800}
          fontSize={140}
          letterSpacing={-3}
          fill={B.text}
        />
        <Txt
          ref={sub}
          y={110}
          opacity={0}
          text={s.sub ?? ''}
          fontFamily={FONT}
          fontWeight={500}
          fontSize={50}
          fill={B.muted}
        />
      </>,
    );
    yield* all(
      rise(kicker(), {d: 0.5}),
      (function* () {
        yield* waitFor(0.15);
        yield* rise(l1(), {dy: 80, s0: 0.85, d: 0.75});
      })(),
      (function* () {
        yield* waitFor(0.4);
        yield* rise(l2(), {dy: 70, d: 0.7});
      })(),
      (function* () {
        yield* at(s.start + 1.2);
        yield* rise(sub(), {dy: 40, d: 0.6});
      })(),
    );
  };

  // chips -----------------------------------------------------------------
  builders.chips = function* (s, i) {
    const title = createRef<Txt>();
    const chips = (s.items ?? []).map(() => createRef<Rect>());
    const codes = ['UZ', 'RU', 'EN', 'AR'];
    const keys = ['ozbek', 'rus', 'ingliz', 'arab'];
    const isRtl = (t: string) => /[\p{Script=Arabic}\p{Script=Hebrew}]/u.test(t);
    const ghosts = createRef<Node>();
    const y0 = -250;
    const step = 140;
    groups[i]().add(
      <>
        <Txt
          ref={title}
          y={-470}
          opacity={0}
          text={s.headline}
          fontFamily={FONT}
          fontWeight={800}
          fontSize={170}
          letterSpacing={-4}
          fill={textGradient(400)}
          shadowColor={rgba(B.primary, 0.5)}
          shadowBlur={50}
        />
        <Node ref={ghosts} opacity={0}>
          {(s.items ?? []).map((_, k) => ghost(y0 + k * step, 720, 112, 56))}
        </Node>
        {(s.items ?? []).map((item, k) => (
          <Rect
            ref={chips[k]}
            y={y0 + k * step}
            opacity={0}
            width={720}
            height={112}
            radius={56}
            fill={rgba(B.surface, 0.92)}
            stroke={rgba(B.primary, 0.4)}
            lineWidth={2}
            shadowColor={'rgba(0,0,0,0.35)'}
            shadowBlur={30}
            shadowOffsetY={10}
            layout
            direction={isRtl(item) ? 'row-reverse' : 'row'}
            alignItems={'center'}
            gap={28}
            padding={[0, 22]}
          >
            <Circle
              size={76}
              fill={brandGradient(76, 76)}
              layout
              alignItems={'center'}
              justifyContent={'center'}
            >
              <Txt
                text={codes[k] ?? String(k + 1)}
                fontFamily={FONT}
                fontWeight={800}
                fontSize={27}
                letterSpacing={1}
                fill={'#ffffff'}
              />
            </Circle>
            <Txt
              text={item}
              textWrap={false}
              fontFamily={FONT}
              fontWeight={600}
              fontSize={54}
              fill={B.text}
            />
            <Layout grow={1} />
            <Circle size={14} fill={B.accent} />
          </Rect>
        ))}
      </>,
    );
    yield* all(
      rise(title(), {dy: 70, s0: 0.85, d: 0.7}),
      ghosts().opacity(1, 0.8, easeOutCubic),
      ...chips.map((chip, k) =>
        (function* () {
          yield* at(cue(keys[k] ?? '', s.start + 0.8 + k * 0.4));
          yield* pop(chip(), 0.55);
        })(),
      ),
    );
  };

  // stat ------------------------------------------------------------------
  builders.stat = function* (s, i) {
    const target = parseFloat(s.stat ?? '0') || 0;
    const count = createSignal(0);
    const ringY = -300;
    const ring = createRef<Node>();
    const head = createRef<Txt>();
    const sub = createRef<Rect>();
    const arc = createSignal(0);
    const num = createRef<Txt>();
    const dots = Math.max(1, Math.round(target));
    groups[i]().add(
      <>
        <Node ref={ring} y={ringY} opacity={0}>
          <Circle size={500} fill={rgba(B.surface, 0.6)} />
          <Circle size={500} stroke={'rgba(255,255,255,0.07)'} lineWidth={22} />
          <Circle
            size={500}
            stroke={brandGradient(500, 500)}
            lineWidth={22}
            lineCap={'round'}
            startAngle={-90}
            endAngle={() => -90 + 360 * arc()}
            closed={false}
            shadowColor={rgba(B.primary, 0.8)}
            shadowBlur={30}
          />
          {Array.from({length: dots}, (_, k) => {
            const a = ((-90 + (360 / dots) * (k + 1)) * Math.PI) / 180;
            return (
              <Circle
                size={22}
                x={Math.cos(a) * 310}
                y={Math.sin(a) * 310}
                fill={k % 2 ? B.accent : B.primary}
                scale={() => Math.min(1, Math.max(0, (count() - k) * 1.5))}
              />
            );
          })}
          <Txt
            ref={num}
            text={'0'}
            fontFamily={FONT}
            fontWeight={800}
            fontSize={300}
            letterSpacing={-8}
            fill={textGradient(240)}
            y={10}
            shadowColor={rgba(B.primary, 0.6)}
            shadowBlur={50}
          />
        </Node>
        <Txt
          ref={head}
          y={60}
          opacity={0}
          text={s.headline}
          fontFamily={FONT}
          fontWeight={800}
          fontSize={104}
          letterSpacing={-2}
          fill={B.text}
        />
        <Rect
          ref={sub}
          y={170}
          opacity={0}
          layout
          direction={'row'}
          alignItems={'center'}
          gap={14}
          padding={[16, 30]}
          radius={999}
          fill={rgba(B.surface, 0.85)}
          stroke={'rgba(255,255,255,0.08)'}
          lineWidth={2}
        >
          <Circle size={14} fill={B.accent} />
          <Txt
            text={s.sub ?? ''}
            fontFamily={FONT}
            fontWeight={500}
            fontSize={40}
            fill={B.muted}
          />
        </Rect>
      </>,
    );
    yield* all(
      rise(ring(), {dy: 0, s0: 0.7, d: 0.7}),
      (function* () {
        yield* waitFor(0.2);
        // Txt text must be changed from a generator (a reactive text={() => ..}
        // spawns TxtLeaf nodes outside the scene context and fails).
        yield* all(
          tween(1.4, v => {
            count(easeOutCubic(v) * target);
            num().text(Math.round(count()).toString());
          }),
          arc(1, 1.6, easeOutCubic),
        );
      })(),
      (function* () {
        yield* waitFor(0.45);
        yield* rise(head(), {dy: 50, d: 0.6});
      })(),
      (function* () {
        yield* at(cue('jumladan', s.start + 1.4));
        yield* rise(sub(), {dy: 40, d: 0.6});
      })(),
    );
  };

  // list ------------------------------------------------------------------
  builders.list = function* (s, i) {
    const title = createRef<Txt>();
    const rows = (s.items ?? []).map(() => createRef<Rect>());
    const keys = ['quvonch', 'hayrat', 'sokinlik', 'boshqalar'];
    const ghosts = createRef<Node>();
    const y0 = -280;
    const step = 138;
    const split = (t: string) => {
      const m = t.match(/^(\S+)\s+(.*)$/u);
      return m && /\p{Extended_Pictographic}/u.test(m[1]) ? [m[1], m[2]] : ['', t];
    };
    groups[i]().add(
      <>
        <Txt
          ref={title}
          y={-470}
          opacity={0}
          text={s.headline}
          fontFamily={FONT}
          fontWeight={800}
          fontSize={104}
          letterSpacing={-3}
          fill={B.text}
        />
        <Node ref={ghosts} opacity={0}>
          {(s.items ?? []).map((_, k) => ghost(y0 + k * step, 740, 114, 30))}
        </Node>
        {(s.items ?? []).map((item, k) => {
          const [emoji, label] = split(item);
          return (
            <Rect
              ref={rows[k]}
              y={y0 + k * step}
              opacity={0}
              width={740}
              height={114}
              radius={30}
              fill={rgba(B.surface, 0.92)}
              stroke={'rgba(255,255,255,0.07)'}
              lineWidth={2}
              shadowColor={'rgba(0,0,0,0.35)'}
              shadowBlur={30}
              shadowOffsetY={10}
              layout
              direction={'row'}
              alignItems={'center'}
              gap={26}
              padding={[0, 20]}
            >
              <Circle
                size={80}
                fill={rgba(B.primary, 0.2)}
                stroke={rgba(B.primary, 0.5)}
                lineWidth={2}
                layout
                alignItems={'center'}
                justifyContent={'center'}
              >
                <Txt text={emoji} fontFamily={EMOJI} fontSize={44} textWrap={false} />
              </Circle>
              <Txt
                text={label}
                fontFamily={FONT}
                fontWeight={600}
                fontSize={52}
                fill={B.text}
              />
              <Layout grow={1} />
              <Layout direction={'row'} alignItems={'center'} gap={5}>
                {[0, 1, 2, 3, 4].map(b => (
                  <Rect
                    width={6}
                    radius={3}
                    fill={b % 2 ? B.accent : B.primary}
                    height={() =>
                      10 + 26 * (0.5 + 0.5 * Math.sin(T() * (5 + k) + b * 1.3 + k))
                    }
                  />
                ))}
              </Layout>
            </Rect>
          );
        })}
      </>,
    );
    yield* all(
      rise(title(), {dy: 60, d: 0.65}),
      ghosts().opacity(1, 0.8, easeOutCubic),
      ...rows.map((row, k) =>
        (function* () {
          yield* at(cue(keys[k] ?? '', s.start + 1 + k * 0.45));
          yield* slideIn(row(), 0, 0.55);
        })(),
      ),
    );
  };

  // cta -------------------------------------------------------------------
  builders.cta = function* (s, i) {
    const head = createRef<Txt>();
    const sub = createRef<Txt>();
    const btn = createRef<Node>();
    const t0 = s.start + 0.45;
    const phase = (p: number) => Math.max(0, T() - t0) / 1.2 + p;
    groups[i]().add(
      <>
        <Txt
          ref={head}
          y={-360}
          opacity={0}
          text={s.headline}
          width={940}
          textWrap
          textAlign={'center'}
          lineHeight={'105%'}
          fontFamily={FONT}
          fontWeight={800}
          fontSize={118}
          letterSpacing={-3}
          fill={B.text}
        />
        <Txt
          ref={sub}
          y={-160}
          opacity={0}
          text={s.sub ?? ''}
          fontFamily={FONT}
          fontWeight={500}
          fontSize={48}
          fill={B.muted}
        />
        <Node ref={btn} y={30} opacity={0}>
          {[0, 0.5].map(p => (
            <Rect
              width={440}
              height={150}
              radius={75}
              stroke={B.accent}
              lineWidth={3}
              scale={() => 1 + 0.35 * (phase(p) % 1)}
              opacity={() => (T() < t0 ? 0 : 0.6 * (1 - (phase(p) % 1)))}
            />
          ))}
          <Rect
            width={440}
            height={150}
            radius={75}
            fill={brandGradient(440, 150)}
            shadowColor={rgba(B.primary, 0.75)}
            shadowBlur={() => 50 + 25 * Math.sin(phase(0) * Math.PI * 2)}
            scale={() => 1 + 0.04 * Math.sin(phase(0) * Math.PI * 2)}
            layout
            direction={'row'}
            alignItems={'center'}
            justifyContent={'center'}
            gap={24}
          >
            <Circle size={64} fill={'#ffffff'} layout alignItems={'center'} justifyContent={'center'}>
              <Polygon sides={3} size={26} fill={B.primary} rotation={90} x={3} layout={false} />
            </Circle>
            <Txt
              text={B.name}
              fontFamily={FONT}
              fontWeight={800}
              fontSize={68}
              fill={'#ffffff'}
            />
          </Rect>
        </Node>
      </>,
    );
    yield* all(
      rise(head(), {dy: 60, s0: 0.9, d: 0.6}),
      (function* () {
        yield* waitFor(0.2);
        yield* rise(sub(), {dy: 40, d: 0.55});
      })(),
      (function* () {
        yield* waitFor(0.35);
        yield* pop(btn(), 0.6);
      })(),
    );
  };

  view.add(
    <Node>
      {SCENES.map((_, i) => (
        <Node ref={groups[i]} opacity={0} />
      ))}
    </Node>,
  );

  // ------------------------------------------------- waveform + captions --
  const BARS = 44;
  view.add(
    <Node y={WAVE_Y}>
      {Array.from({length: BARS}, (_, i) => {
        const c = (i - (BARS - 1) / 2) / (BARS / 2);
        const bell = Math.exp(-c * c * 2.2);
        return (
          <Rect
            x={(i - (BARS - 1) / 2) * 17}
            width={8}
            radius={4}
            fill={i / BARS < 0.5 ? B.primary : B.accent}
            opacity={0.35 + 0.5 * bell}
            height={() =>
              8 +
              4 * (1 + Math.sin(T() * 2.2 + i * 0.5)) +
              120 * env() * bell * (0.55 + 0.45 * Math.sin(T() * 11 + i * 1.9))
            }
          />
        );
      })}
    </Node>,
  );

  const capBox = createRef<Rect>();
  const capTxt = createRef<Txt>();
  view.add(
    <Rect
      ref={capBox}
      y={CAPTION_Y}
      opacity={0}
      layout
      padding={[22, 44]}
      radius={999}
      fill={'rgba(8,12,26,0.78)'}
      stroke={'rgba(255,255,255,0.12)'}
      lineWidth={2}
      shadowColor={'rgba(0,0,0,0.45)'}
      shadowBlur={40}
    >
      <Txt
        ref={capTxt}
        text={''}
        textWrap={false}
        fontFamily={FONT}
        fontWeight={700}
        fontSize={46}
        fill={B.text}
      />
    </Rect>,
  );

  // ------------------------------------------------------- progress bar --
  view.add(
    <Node y={PROGRESS_Y}>
      <Rect width={900} height={6} radius={3} fill={'rgba(255,255,255,0.10)'} />
      <Rect
        x={-450}
        offset={[-1, 0]}
        width={() => 900 * Math.min(1, T() / DUR)}
        height={6}
        radius={3}
        fill={B.accent}
        shadowColor={B.accent}
        shadowBlur={12}
      />
    </Node>,
  );

  view.add(<Audio src={'/voiceover.wav'} play={true} />);

  // ----------------------------------------------------------- threads --
  function* captions(): ThreadGenerator {
    for (let k = 0; k < CAPTIONS.length; k++) {
      const c = CAPTIONS[k];
      const next = CAPTIONS[k + 1];
      yield* at(c.start);
      const visible = capBox().opacity() > 0.5;
      capTxt().text(c.text);
      if (visible) {
        capBox().scale(0.96);
        yield* capBox().scale(1, 0.18, easeOutCubic);
      } else {
        capBox().scale(0.85);
        yield* all(capBox().opacity(1, 0.18, easeOutCubic), capBox().scale(1, 0.3, easeOutBack));
      }
      if (!next || next.start - c.end > 0.12) {
        yield* at(c.end);
        yield* all(capBox().opacity(0, 0.2, easeInCubic), capBox().scale(0.94, 0.2));
      }
    }
  }

  function* sceneThread(s: Scene, i: number): ThreadGenerator {
    yield* at(s.start);
    const g = groups[i]();
    g.opacity(1);
    const build = builders[s.layout] ?? builders.hero;
    yield* build(s, i);
    if (i < SCENES.length - 1) {
      yield* at(s.end - 0.32);
      yield* all(
        g.opacity(0, 0.32, easeInCubic),
        g.y(-70, 0.32, easeInCubic),
        g.scale(0.96, 0.32, easeInCubic),
      );
      g.removeChildren();
    }
  }

  yield* all(
    T(DUR, DUR, linear),
    header().opacity(1, 0.5),
    captions(),
    ...SCENES.map((s, i) => sceneThread(s, i)),
  );
});

export default makeProject({
  scenes: [promo],
  settings: {
    shared: {
      size: {x: W, y: H},
      background: B.background,
    },
    rendering: {
      fps: FPS,
    },
  },
});
