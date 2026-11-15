import {
  Circle,
  Gradient,
  Layout,
  Node,
  Rect,
  Txt,
  makeScene2D,
} from '@motion-canvas/2d';
import {
  ThreadGenerator,
  all,
  createRef,
  createSignal,
  easeInCubic,
  easeOutBack,
  easeOutCubic,
  linear,
  sequence,
  useThread,
  waitFor,
} from '@motion-canvas/core';
import timeline from '../../assets/timeline.json';
import {FONT} from '../fonts';

// ---------------------------------------------------------------------------
// Brand + layout constants (Motion Canvas origin is the frame centre, y down).
// 1080x1920 frame => x in [-540, 540], y in [-960, 960].
// Instagram safe zone: nothing important above y=-740 (top 220px) or below
// y=580 (bottom 380px).
// ---------------------------------------------------------------------------
const B = timeline.brand;
const W = timeline.width;
const H = timeline.height;
const DURATION = timeline.duration;
const CAPTION_Y = 430;
const PROGRESS_Y = 548;
const HEADER_Y = -676;

const alpha = (hex: string, a: number) => {
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

const glow = (color: string, a: number, r: number) =>
  new Gradient({
    type: 'radial',
    from: [0, 0],
    to: [0, 0],
    fromRadius: 0,
    toRadius: r,
    stops: [
      {offset: 0, color: alpha(color, a)},
      {offset: 0.5, color: alpha(color, a * 0.35)},
      {offset: 1, color: alpha(color, 0)},
    ],
  });

/** Wait until an absolute timeline time (seconds) on the current thread. */
function* until(t: number): ThreadGenerator {
  const d = t - useThread().time();
  if (d > 0) yield* waitFor(d);
}

type SceneDef = (typeof timeline.scenes)[number] & {
  kicker?: string;
  sub?: string;
  items?: string[];
  stat?: string;
};

export default makeScene2D(function* (view) {
  view.fill(B.background);

  // Global clock signal (seconds) drives ambient motion + progress bar.
  const clock = createSignal(0);

  // ---------------------------------------------------------------- background
  view.add(
    <Node>
      <Circle
        size={1500}
        fill={glow(B.primary, 0.42, 750)}
        position={() => [
          -260 + 140 * Math.sin(clock() * 0.45),
          -560 + 110 * Math.cos(clock() * 0.38),
        ]}
      />
      <Circle
        size={1400}
        fill={glow(B.accent, 0.26, 700)}
        position={() => [
          330 + 120 * Math.cos(clock() * 0.41),
          520 + 140 * Math.sin(clock() * 0.33),
        ]}
      />
      <Circle
        size={900}
        fill={glow(B.primary, 0.22, 450)}
        position={() => [
          380 + 90 * Math.sin(clock() * 0.6 + 1),
          -120 + 120 * Math.cos(clock() * 0.5 + 2),
        ]}
      />
      {/* faint dot grid */}
      <Node opacity={0.06}>
        {Array.from({length: 13 * 23}, (_, i) => {
          const cx = (i % 13) - 6;
          const cy = Math.floor(i / 13) - 11;
          return <Circle size={5} fill={B.text} position={[cx * 84, cy * 84]} />;
        })}
      </Node>
      {/* vignette */}
      <Rect
        size={[W, H]}
        fill={
          new Gradient({
            type: 'radial',
            from: [0, -100],
            to: [0, -100],
            fromRadius: 300,
            toRadius: 1200,
            stops: [
              {offset: 0, color: alpha(B.background, 0)},
              {offset: 1, color: alpha(B.background, 0.85)},
            ],
          })
        }
      />
    </Node>,
  );

  // ------------------------------------------------------------------- header
  const header = createRef<Layout>();
  view.add(
    <Layout
      ref={header}
      layout
      direction="row"
      alignItems="center"
      gap={18}
      y={HEADER_Y}
      opacity={0}
    >
      <Rect
        size={64}
        radius={18}
        fill={brandGradient(64, 64)}
        layout
        direction="row"
        alignItems="center"
        justifyContent="center"
        gap={6}
      >
        {[18, 34, 26, 14].map((h, i) => (
          <Rect
            width={6}
            radius={3}
            fill={B.text}
            height={() => h + 8 * Math.sin(clock() * 7 + i * 1.3)}
          />
        ))}
      </Rect>
      <Txt text={B.name} fontFamily={FONT} fontWeight={800} fontSize={44} fill={B.text} />
    </Layout>,
  );

  // ------------------------------------------------------------- progress bar
  const PW = 760;
  view.add(
    <Rect width={PW} height={8} radius={4} y={PROGRESS_Y} fill={alpha(B.text, 0.12)}>
      <Rect
        height={8}
        radius={4}
        x={() => -PW / 2 + (PW * Math.min(1, clock() / DURATION)) / 2}
        width={() => PW * Math.min(1, clock() / DURATION)}
        fill={brandGradient(PW)}
        shadowColor={alpha(B.accent, 0.8)}
        shadowBlur={12}
      />
    </Rect>,
  );

  // ---------------------------------------------------------- scene container
  const stage = createRef<Node>();
  view.add(<Node ref={stage} />);

  // ------------------------------------------------------------------ caption
  const capBox = createRef<Rect>();
  const capTxt = createRef<Txt>();
  view.add(
    <Rect
      ref={capBox}
      layout
      y={CAPTION_Y}
      padding={[22, 44]}
      radius={999}
      fill={alpha(B.surface, 0.88)}
      stroke={alpha(B.text, 0.14)}
      lineWidth={2}
      shadowColor={'rgba(0,0,0,0.45)'}
      shadowBlur={40}
      shadowOffsetY={12}
      opacity={0}
      maxWidth={960}
    >
      <Txt
        ref={capTxt}
        text=""
        fontFamily={FONT}
        fontWeight={600}
        fontSize={50}
        lineHeight={64}
        fill={B.text}
        textAlign="center"
        textWrap
      />
    </Rect>,
  );

  // ===================================================== ambient threads ====
  yield clock(DURATION + 1, DURATION + 1, linear);
  yield header().opacity(1, 0.6, easeOutCubic);

  // Captions thread.
  yield (function* () {
    const caps = timeline.captions;
    for (let i = 0; i < caps.length; i++) {
      const c = caps[i];
      yield* until(c.start);
      const visible = capBox().opacity() > 0.5;
      capTxt().text(c.text);
      if (visible) {
        capBox().scale(0.96);
        yield capBox().scale(1, 0.18, easeOutBack);
      } else {
        capBox().scale(0.85);
        capBox().y(CAPTION_Y + 24);
        yield all(
          capBox().opacity(1, 0.2, easeOutCubic),
          capBox().scale(1, 0.28, easeOutBack),
          capBox().y(CAPTION_Y, 0.28, easeOutCubic),
        );
      }
      const next = caps[i + 1];
      if (!next || next.start - c.end > 0.05) {
        yield* until(c.end);
        yield* all(
          capBox().opacity(0, 0.2, easeInCubic),
          capBox().scale(0.94, 0.2, easeInCubic),
        );
      }
    }
  })();

  // ============================================================ scenes ======
  const scenes = timeline.scenes as SceneDef[];
  const OUT = 0.38; // exit duration, played inside each scene's trailing pad

  for (let i = 0; i < scenes.length; i++) {
    const s = scenes[i];
    yield* until(s.start);
    const group = createRef<Node>();
    stage().add(<Node ref={group} />);
    const enter = build[s.layout](group(), s);
    yield enter;
    if (i < scenes.length - 1) {
      yield* until(s.end - OUT);
      yield* all(
        group().opacity(0, OUT, easeInCubic),
        group().y(-70, OUT, easeInCubic),
        group().scale(0.96, OUT, easeInCubic),
      );
      group().remove();
    } else {
      yield* until(s.end);
    }
  }
});

// ===========================================================================
// Layout builders: add nodes to `g` and return the enter-animation generator.
// ===========================================================================
type Builder = (g: Node, s: SceneDef) => ThreadGenerator;

/** Reveal time for item i: spread over the part of the line where items are named. */
const cue = (s: SceneDef, i: number) =>
  s.start + (s.speech_end - s.start) * (0.38 + 0.14 * i);

const T = (props: Record<string, any>) => (
  <Txt fontFamily={FONT} fill={B.text} textAlign="center" {...props} />
);

// NOTE: these are deliberately *not* generator functions. The hidden start
// state must be applied immediately when the animation is scheduled, not when
// the (lazy) generator first runs - otherwise staggered items are visible
// until their turn comes.
function rise(node: Node, d = 0.6, dy = 60, dx = 0): ThreadGenerator {
  const x = node.x();
  const y = node.y();
  node.position([x + dx, y + dy]);
  node.opacity(0);
  return all(
    node.opacity(1, d * 0.8, easeOutCubic),
    node.position([x, y], d, easeOutCubic),
  );
}

function pop(node: Node, d = 0.5): ThreadGenerator {
  node.scale(0);
  node.opacity(0);
  return all(node.opacity(1, d * 0.5, easeOutCubic), node.scale(1, d, easeOutBack));
}

/** Run `task` at absolute timeline time `t` (its start state is applied now). */
function* at(t: number, task: ThreadGenerator): ThreadGenerator {
  yield* until(t);
  yield* task;
}

const build: Record<string, Builder> = {
  // ------------------------------------------------------------------ hero
  hero(g, s) {
    const [first, ...rest] = s.headline.split(' ');
    const kicker = createRef<Rect>();
    const l1 = createRef<Txt>();
    const l2 = createRef<Txt>();
    const sub = createRef<Txt>();
    const wave = createRef<Layout>();
    const waveAmp = createSignal(0);
    const t0 = createSignal(0);
    g.add(
      <>
        <Rect
          ref={kicker}
          layout
          y={-470}
          padding={[16, 34]}
          radius={999}
          fill={alpha(B.accent, 0.12)}
          stroke={alpha(B.accent, 0.7)}
          lineWidth={2}
        >
          <T text={s.kicker ?? ''} fontSize={34} fontWeight={700} letterSpacing={6} fill={B.accent} />
        </Rect>
        <T ref={l1} text={first} y={-290} fontSize={230} fontWeight={800} letterSpacing={-6} fill={brandGradient(520, 120)} />
        <T ref={l2} text={rest.join(' ')} y={-110} fontSize={128} fontWeight={800} letterSpacing={-3} />
        <T ref={sub} text={s.sub ?? ''} y={40} fontSize={54} fontWeight={500} fill={B.muted} width={940} textWrap />
        <Layout ref={wave} layout direction="row" alignItems="center" gap={12} y={220}>
          {Array.from({length: 23}, (_, i) => (
            <Rect
              width={12}
              radius={6}
              fill={brandGradient(12, 140)}
              height={() =>
                10 +
                waveAmp() *
                  (60 + 50 * Math.sin(i * 0.9 + t0() * 9)) *
                  (1 - Math.abs(i - 11) / 14)
              }
            />
          ))}
        </Layout>
      </>,
    );
    return all(
      t0(30, 30, linear),
      sequence(
        0.14,
        pop(kicker()),
        rise(l1(), 0.7, 80),
        rise(l2(), 0.7, 80),
        rise(sub(), 0.6, 40),
        all(rise(wave(), 0.6, 30), waveAmp(1, 0.8, easeOutCubic)),
      ),
    );
  },

  // ----------------------------------------------------------------- chips
  chips(g, s) {
    const head = createRef<Txt>();
    const codes = ['UZ', 'RU', 'EN', 'AR'];
    const chips: Rect[] = [];
    const items = s.items ?? [];
    g.add(<T ref={head} text={s.headline} y={-420} fontSize={190} fontWeight={800} letterSpacing={-5} />);
    items.forEach((item, i) => {
      const col = i % 2;
      const row = Math.floor(i / 2);

      const chip = (
        <Rect
          layout
          direction="row"
          alignItems="center"
          justifyContent="center"
          gap={22}
          width={460}
          height={150}
          radius={44}
          x={(col - 0.5) * 492}
          y={-150 + row * 186}
          fill={alpha(B.surface, 0.92)}
          stroke={alpha(B.primary, 0.55)}
          lineWidth={3}
          shadowColor={alpha(B.primary, 0.35)}
          shadowBlur={30}
        >
          <Rect
            size={66}
            radius={33}
            fill={brandGradient(66, 66)}
            layout
            alignItems="center"
            justifyContent="center"
          >
            <T text={codes[i] ?? ''} fontSize={24} fontWeight={800} letterSpacing={1} />
          </Rect>
          <T text={item} fontSize={54} fontWeight={600} />
        </Rect>
      ) as Rect;
      chips.push(chip);
      g.add(chip);
    });
    return all(
      rise(head(), 0.7, 70),
      ...chips.map((c, i) => at(cue(s, i), pop(c, 0.55))),
    );
  },

  // ------------------------------------------------------------------ stat
  stat(g, s) {
    const target = parseFloat(s.stat ?? '0');
    const count = createSignal(0);
    const ring = createRef<Node>();
    const arc = createRef<Circle>();
    const head = createRef<Txt>();
    const sub = createRef<Txt>();
    const R = 250;
    g.add(
      <>
        <Node ref={ring} y={-300}>
          <Circle size={R * 2 + 120} fill={glow(B.primary, 0.35, R + 60)} />
          <Circle size={R * 2} stroke={alpha(B.text, 0.08)} lineWidth={22} />
          <Circle
            ref={arc}
            size={R * 2}
            stroke={brandGradient(R * 2, R * 2)}
            lineWidth={22}
            lineCap="round"
            startAngle={-90}
            endAngle={-90}
            shadowColor={alpha(B.primary, 0.9)}
            shadowBlur={24}
          />
          <T
            text={() => Math.round(count()).toString()}
            fontSize={330}
            fontWeight={800}
            letterSpacing={-8}
            fill={brandGradient(240, 300)}
            y={8}
          />
        </Node>
        <T ref={head} text={s.headline} y={60} fontSize={104} fontWeight={800} letterSpacing={-2} />
        <T ref={sub} text={s.sub ?? ''} y={190} fontSize={48} fontWeight={500} fill={B.muted} width={900} textWrap lineHeight={62} />
      </>,
    );
    return all(
      pop(ring(), 0.7),
      count(target, 1.4, easeOutCubic),
      arc().endAngle(-90 + 360 * (target / 10), 1.4, easeOutCubic),
      sequence(0.15, waitFor(0.25), rise(head(), 0.6, 60), rise(sub(), 0.6, 40)),
    );
  },

  // ------------------------------------------------------------------ list
  list(g, s) {
    const head = createRef<Txt>();
    const rows: Rect[] = [];
    g.add(
      <T ref={head} text={s.headline} y={-470} fontSize={104} fontWeight={800} letterSpacing={-2} width={960} textWrap />,
    );
    (s.items ?? []).forEach((item, i) => {
      const sp = item.indexOf(' ');
      const emoji = item.slice(0, sp);
      const label = item.slice(sp + 1);
      const row = (
        <Rect
          layout
          direction="row"
          alignItems="center"
          gap={30}
          padding={[0, 30]}
          width={820}
          height={132}
          radius={40}
          y={-300 + i * 156}
          fill={alpha(B.surface, 0.9)}
          stroke={alpha(B.text, 0.1)}
          lineWidth={2}
        >
          <Rect
            size={88}
            radius={28}
            fill={alpha(i % 2 ? B.accent : B.primary, 0.2)}
            layout
            alignItems="center"
            justifyContent="center"
          >
            <T text={emoji} fontSize={52} />
          </Rect>
          <T text={label} fontSize={56} fontWeight={600} grow={1} textAlign="left" />
          <Circle size={16} fill={i % 2 ? B.accent : B.primary} />
        </Rect>
      ) as Rect;
      rows.push(row);
      g.add(row);
    });
    return all(
      rise(head(), 0.7, 70),
      ...rows.map((r, i) => at(cue(s, i), rise(r, 0.6, 0, 140))),
    );
  },

  // ------------------------------------------------------------------- cta
  cta(g, s) {
    const head = createRef<Txt>();
    const sub = createRef<Txt>();
    const btn = createRef<Node>();
    const pulse = createSignal(0);
    g.add(
      <>
        <T ref={head} text={s.headline} y={-400} fontSize={116} fontWeight={800} letterSpacing={-3} width={940} textWrap lineHeight={128} />
        <T ref={sub} text={s.sub ?? ''} y={-200} fontSize={50} fontWeight={500} fill={B.muted} width={940} textWrap />
        <Node ref={btn} y={40}>
          {/* ripple */}
          <Rect
            width={() => 520 + 160 * (pulse() % 1)}
            height={() => 150 + 160 * (pulse() % 1)}
            radius={999}
            stroke={B.accent}
            lineWidth={4}
            opacity={() => 0.7 * (1 - (pulse() % 1))}
          />
          <Rect
            width={520}
            height={150}
            radius={999}
            fill={brandGradient(520, 150)}
            shadowColor={alpha(B.primary, 0.9)}
            shadowBlur={() => 40 + 30 * Math.sin(pulse() * Math.PI * 2)}
            scale={() => 1 + 0.045 * Math.sin(pulse() * Math.PI * 2)}
            layout
            direction="row"
            alignItems="center"
            justifyContent="center"
            gap={24}
          >
            <Circle size={58} fill={B.text} layout alignItems="center" justifyContent="center">
              <T text="▶" fontSize={26} fill={B.primary} marginLeft={4} />
            </Circle>
            <T text={timeline.brand.name} fontSize={76} fontWeight={800} letterSpacing={-1} />
          </Rect>
        </Node>
      </>,
    );
    return all(
      pulse(4, 4 / 1.1, linear),
      sequence(0.14, rise(head(), 0.6, 70), rise(sub(), 0.6, 40), pop(btn(), 0.7)),
    );
  },
};

