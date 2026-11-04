import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import { loadFont as loadGloock } from "@remotion/google-fonts/Gloock";
import { loadFont as loadFigtree } from "@remotion/google-fonts/Figtree";
import { loadFont as loadMono } from "@remotion/google-fonts/JetBrainsMono";
import { C, SCENES, STARTS } from "./scenes";

const display = loadGloock().fontFamily;
const body = loadFigtree("normal", { weights: ["400", "600"], subsets: ["latin"] }).fontFamily;
const mono = loadMono("normal", { weights: ["400", "500"], subsets: ["latin"] }).fontFamily;

// ---------- timing helpers (seconds) ----------
const clamp = (x: number, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
const ease = (k: number) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2);
const seg = (t: number, a: number, len: number) => clamp((t - a) / len);
const S = (i: number) => STARTS[i];
const E = (i: number) => STARTS[i] + SCENES[i].d;
/** Opacity for something on screen from the start of scene a to the end of scene b, with fades. */
const span = (t: number, a: number, b: number, f = 0.6) =>
  Math.min(clamp((t - S(a)) / f), clamp((E(b) - t) / f));
const mix = (c1: string, c2: string, k: number) => {
  const p = (c: string) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
  const a = p(c1), b = p(c2);
  return "#" + a.map((v, i) => Math.round(lerp(v, b[i], k)).toString(16).padStart(2, "0")).join("");
};

// ---------- the cast ----------
const BW = 360, BY = 580;
const BX = (i: number) => 150 + i * 420;
const BENCH = [
  { name: "Book", next: "Outline chapter 3", ask: "Asks: medium", grant: "Granted: medium", lit: true, cap: 35, end: 30, candles: 2 },
  { name: "Investing", next: "Test the thesis", ask: "Asks: small", grant: "Granted: small", lit: true, cap: 15, end: 12, candles: 1 },
  { name: "Garden", next: "Waiting on you:", ask: "layout A or B?", grant: "Not funded tonight", lit: false, cap: 0, end: 0, candles: 0 },
  { name: "Side project", next: "Fix the login bug", ask: "Asks: large", grant: "Asked large, got medium", lit: true, cap: 35, end: 35, candles: 2 },
];
const CARD_Y = 318, CARD_H = 150;
const JAR = { x: 1190, y: 70, w: 160, h: 150 };
// which bench each granted candle goes to, in order
const GRANTS = BENCH.flatMap((b, i) => Array.from({ length: b.candles }, (_, j) => ({ bench: i, j })));

let seed = 11;
const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
const STARS = Array.from({ length: 90 }, () => ({ x: rnd() * 1920, y: rnd() * 720, r: rnd() * 1.9 + 0.6, o: rnd() * 0.6 + 0.3, ph: rnd() * 6 }));

const toMin = (s: string) => { const [h, m] = s.split(":").map(Number); return h * 60 + m; };
const fmt = (m: number) => `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(Math.floor(m % 60)).padStart(2, "0")}`;

// ---------- pieces ----------
const Folder: React.FC<{ x: number; y: number; o: number; label?: string }> = ({ x, y, o, label }) => (
  <g transform={`translate(${x} ${y})`} opacity={o}>
    <rect x={0} y={-12} width={40} height={16} rx={3} fill={C.parchment} />
    <rect x={0} y={0} width={96} height={68} rx={5} fill={C.parchment} />
    {[22, 36, 50].map((yy, k) => <line key={k} x1={12} y1={yy} x2={[80, 66, 72][k]} y2={yy} stroke={C.muted} strokeWidth={3} />)}
    {label && <text x={48} y={100} textAnchor="middle" fontFamily={body} fontSize={22} fill={C.parchment}>{label}</text>}
  </g>
);

const Candle: React.FC<{ x: number; y: number; o: number; flicker: number }> = ({ x, y, o, flicker }) => (
  <g transform={`translate(${x} ${y})`} opacity={o}>
    <ellipse cx={5} cy={-8} rx={5 + flicker} ry={9 + flicker * 2} fill={C.lamp} opacity={0.9} />
    <rect x={0} y={0} width={10} height={30} rx={2} fill={C.parchment} />
  </g>
);

const Caption: React.FC<{ t: number }> = ({ t }) => {
  const i = SCENES.findIndex((_, k) => t >= S(k) && t < E(k));
  if (i < 0 || i === SCENES.length - 1) return null;
  const o = Math.min(seg(t, S(i) + 0.1, 0.5), clamp((E(i) - t) / 0.4));
  return (
    <div style={{ position: "absolute", left: 150, right: 150, top: 880, opacity: o, transform: `translateY(${(1 - o) * 12}px)` }}>
      <div style={{ fontFamily: display, fontSize: 58, color: C.parchment, lineHeight: 1.1 }}>{SCENES[i].h}</div>
      <div style={{ fontFamily: body, fontSize: 31, color: C.muted, marginTop: 14, maxWidth: 1500, lineHeight: 1.35 }}>{SCENES[i].p}</div>
    </div>
  );
};

const Clock: React.FC<{ t: number }> = ({ t }) => {
  const i = Math.max(0, SCENES.findIndex((_, k) => t >= S(k) && t < E(k)));
  const c = SCENES[i].clock;
  if (!c[0]) return null;
  const m = c.length > 1 ? lerp(toMin(c[0]), toMin(c[1]), ease(seg(t, S(i) + 0.3, SCENES[i].d - 0.8))) : toMin(c[0]);
  const o = Math.min(clamp((t - S(i)) / 0.3), clamp((E(i) - t) / 0.3), 1) * 0.5 + 0.5;
  return (
    <div style={{ position: "absolute", right: 150, top: 58, fontFamily: mono, fontSize: 36, color: C.lamp, opacity: o, letterSpacing: 2 }}>
      {fmt(m)}
    </div>
  );
};

// ---------- the film ----------
export const Explainer: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;

  const dawn = Math.min(seg(t, S(6) - 0.4, 1.4), clamp((E(6) - t) / 1.0));
  const bg = mix(C.night, C.dawn, dawn);
  const flick = (k: number) => Math.sin(t * 9 + k * 1.7) * 0.8;

  // benches and folders live from scene 0/1 to the end of scene 5, dimmed during the ledger
  const benchO = span(t, 1, 5);
  const dim = 1 - 0.65 * Math.min(seg(t, S(5) + 0.2, 0.6), clamp((E(5) - t) / 0.6));
  const cardsO = (i: number) => Math.min(seg(t, S(2) + 0.4 + i * 0.35, 0.6), clamp((E(4) - t) / 0.6));
  const foremanO = span(t, 2, 4);

  return (
    <AbsoluteFill style={{ backgroundColor: bg }}>
      <svg viewBox="0 0 1920 1080" width={1920} height={1080} style={{ position: "absolute", inset: 0 }}>
        <defs>
          <radialGradient id="glow"><stop offset="0" stopColor={C.lamp} stopOpacity={0.55} /><stop offset="1" stopColor={C.lamp} stopOpacity={0} /></radialGradient>
          <radialGradient id="sun" cx="0.5" cy="1"><stop offset="0" stopColor={C.dawnGlow} stopOpacity={0.75} /><stop offset="1" stopColor={C.dawnGlow} stopOpacity={0} /></radialGradient>
          <linearGradient id="floor" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={C.night2} stopOpacity={0} /><stop offset="1" stopColor={C.night2} stopOpacity={0.9} /></linearGradient>
        </defs>

        {/* sky */}
        <g opacity={1 - dawn * 0.85}>
          {STARS.map((s, k) => <circle key={k} cx={s.x} cy={s.y} r={s.r} fill={C.parchment} opacity={s.o * (0.75 + 0.25 * Math.sin(t * 1.3 + s.ph))} />)}
        </g>
        <ellipse cx={960} cy={1080} rx={1300} ry={620} fill="url(#sun)" opacity={dawn} />
        <rect x={0} y={560} width={1920} height={520} fill="url(#floor)" />

        {/* scene 0: the pile of projects, then onto their benches */}
        {BENCH.map((b, i) => {
          const appear = seg(t, 0.6 + i * 1.1, 0.6);
          const drop = (1 - ease(appear)) * -40;
          const move = ease(seg(t, S(1) + 0.6 + i * 0.3, 1.3));
          const x = lerp(960 + (i - 1.5) * 240 - 48, BX(i) + 28, move);
          const y = lerp(300 + [0, 26, -8, 18][i] + drop + Math.sin(t * 2 + i) * 3, BY - 70, move);
          const o = appear * clamp((E(5) - t) / 0.6) * (move > 0 ? dim : 1);
          return <Folder key={b.name} x={x} y={y} o={o} label={move < 0.05 ? b.name : undefined} />;
        })}
        <g opacity={span(t, 0, 0) * 0.8}>
          <ellipse cx={960} cy={340} rx={520} ry={200} fill="url(#glow)" />
        </g>

        {/* benches */}
        <g opacity={benchO * dim}>
          {BENCH.map((b, i) => {
            const x = BX(i);
            const on = b.lit ? seg(t, S(4) + 0.2 + i * 0.25, 0.5) * clamp((E(5) - t) / 0.6) : 0;
            const turns = b.lit ? b.end * ease(seg(t, S(4) + 0.9 + i * 0.2, 6)) : 0;
            const workO = b.lit ? span(t, 4, 5) : 0;
            const stopped = b.end === b.cap && turns >= b.cap - 0.01;
            return (
              <g key={b.name}>
                <circle cx={x + BW - 58} cy={BY - 46} r={170} fill="url(#glow)" opacity={on * 0.9} />
                <rect x={x - 12} y={BY} width={BW + 24} height={18} rx={4} fill={C.leather} />
                <rect x={x} y={BY + 18} width={BW} height={190} rx={8} fill={C.leather2} stroke={C.leather} strokeWidth={3} />
                {/* desk lamp */}
                <line x1={x + BW - 40} y1={BY} x2={x + BW - 40} y2={BY - 56} stroke={C.line} strokeWidth={5} />
                <line x1={x + BW - 40} y1={BY - 56} x2={x + BW - 66} y2={BY - 70} stroke={C.line} strokeWidth={5} />
                <circle cx={x + BW - 70} cy={BY - 64} r={15} fill={on > 0 ? mix(C.line, C.lamp, on) : C.line} />
                {/* the work: a stitched line that grows with turns used */}
                <clipPath id={`clip${i}`}><rect x={x + 30} y={BY + 70} width={(BW - 60) * (b.cap ? turns / b.cap : 0)} height={40} /></clipPath>
                <line x1={x + 30} y1={BY + 90} x2={x + BW - 30} y2={BY + 90} stroke={C.line} strokeWidth={5} strokeDasharray="16 10" strokeLinecap="round" opacity={workO * 0.6} />
                <line x1={x + 30} y1={BY + 90} x2={x + BW - 30} y2={BY + 90} stroke={stopped ? C.warn : C.thread} strokeWidth={5} strokeDasharray="16 10" strokeLinecap="round" clipPath={`url(#clip${i})`} opacity={workO} />
                <text x={x + BW / 2} y={BY + 150} textAnchor="middle" fontFamily={mono} fontSize={22} fill={stopped ? C.warn : C.muted} opacity={workO}>
                  {b.lit ? (stopped ? `${b.cap} / ${b.cap} · stopped` : `${Math.round(turns)} / ${b.cap} turns`) : ""}
                </text>
                {!b.lit && <text x={x + BW / 2} y={BY + 150} textAnchor="middle" fontFamily={mono} fontSize={22} fill={C.muted} opacity={span(t, 4, 5) * 0.8}>dark tonight</text>}
                <text x={x + BW / 2} y={BY + 250} textAnchor="middle" fontFamily={body} fontWeight={600} fontSize={26} fill={C.parchment}>{b.name}</text>
              </g>
            );
          })}
          {/* walls between benches */}
          <g opacity={seg(t, S(1) + 2.6, 0.8)}>
            {[1, 2, 3].map((k) => <line key={k} x1={BX(k) - 30} y1={260} x2={BX(k) - 30} y2={850} stroke={C.line} strokeWidth={4} strokeDasharray="2 12" strokeLinecap="round" />)}
          </g>
        </g>

        {/* foreman and reading lines */}
        <g opacity={foremanO}>
          <circle cx={960} cy={150} r={130} fill="url(#glow)" opacity={0.7} />
          <rect x={934} y={128} width={52} height={64} rx={10} fill={C.panel} stroke={C.lamp} strokeWidth={3} />
          <circle cx={960} cy={160} r={14 + flick(0) * 0.6} fill={C.lamp} />
          <line x1={945} y1={128} x2={975} y2={128} stroke={C.lamp} strokeWidth={3} />
          <path d="M948 128 Q960 104 972 128" fill="none" stroke={C.lamp} strokeWidth={3} />
          <text x={960} y={92} textAnchor="middle" fontFamily={mono} fontSize={22} fill={C.lamp} letterSpacing={3}>FOREMAN</text>
          <g opacity={Math.min(seg(t, S(2) + 2.4, 0.6), clamp((E(2) + 1.2 - t) / 0.6))}>
            {BENCH.map((_, i) => (
              <line key={i} x1={960} y1={200} x2={BX(i) + BW / 2} y2={CARD_Y} stroke={C.lamp} strokeWidth={2.5}
                strokeDasharray="5 9" strokeDashoffset={-t * 30} opacity={0.75} />
            ))}
          </g>
        </g>

        {/* pitch cards */}
        {BENCH.map((b, i) => {
          const o = cardsO(i);
          const rise = (1 - ease(seg(t, S(2) + 0.4 + i * 0.35, 0.6))) * 30;
          const gO = seg(t, S(3) + 3.4 + i * 0.4, 0.5);
          const x = BX(i) + 10;
          return (
            <g key={b.name} opacity={o} transform={`translate(0 ${rise})`}>
              <rect x={x} y={CARD_Y} width={BW - 20} height={CARD_H} rx={10} fill={C.panel} stroke={C.line} strokeWidth={2} />
              <text x={x + 20} y={CARD_Y + 42} fontFamily={body} fontWeight={600} fontSize={24} fill={C.parchment}>{b.next}</text>
              <text x={x + 20} y={CARD_Y + 76} fontFamily={body} fontSize={21} fill={C.muted}>{b.ask}</text>
              <text x={x + 20} y={CARD_Y + 122} fontFamily={body} fontWeight={600} fontSize={21} fill={b.lit ? C.lamp : C.muted} opacity={gO}>{b.grant}</text>
            </g>
          );
        })}

        {/* budget jar and candles */}
        <g opacity={span(t, 3, 4)}>
          <rect x={JAR.x} y={JAR.y} width={JAR.w} height={JAR.h} rx={18} fill="none" stroke={C.muted} strokeWidth={3} opacity={0.8} />
          <rect x={JAR.x + 20} y={JAR.y - 14} width={JAR.w - 40} height={16} rx={5} fill={C.line} />
          <text x={JAR.x + JAR.w / 2} y={JAR.y + JAR.h + 40} textAnchor="middle" fontFamily={mono} fontSize={20} fill={C.muted}
            opacity={seg(t, S(3) + 4.8, 0.6)}>{`kept for tomorrow: ${10 - GRANTS.length}`}</text>
          <text x={JAR.x + JAR.w + 24} y={JAR.y + 40} fontFamily={mono} fontSize={20} fill={C.muted}>tonight's budget</text>
        </g>
        {Array.from({ length: 10 }, (_, k) => {
          const home = { x: JAR.x + 22 + (k % 5) * 25, y: JAR.y + 30 + Math.floor(k / 5) * 58 };
          const g = GRANTS[k];
          let { x, y } = home;
          if (g) {
            const e = ease(seg(t, S(3) + 1.3 + k * 0.4, 1));
            const dest = { x: BX(g.bench) + BW - 56 - g.j * 24, y: CARD_Y + 98 };
            x = lerp(home.x, dest.x, e);
            y = lerp(home.y, dest.y, e) - Math.sin(Math.PI * e) * 90;
          }
          const o = g ? Math.min(seg(t, S(3), 0.6), clamp((E(4) - t) / 0.6)) : span(t, 3, 4);
          return <Candle key={k} x={x} y={y} o={o} flicker={flick(k) * 0.6} />;
        })}

        {/* scene 5: the ledger */}
        <g opacity={span(t, 5, 5)} transform={`translate(0 ${(1 - ease(seg(t, S(5), 0.7))) * 20})`}>
          <rect x={520} y={140} width={880} height={420} rx={14} fill={C.parchment} />
          <text x={560} y={200} fontFamily={display} fontSize={38} fill={C.leather2}>Ledger · last night</text>
          {[
            ["Book", "promised: outline chapter 3", "delivered", C.thread],
            ["Investing", "promised: test the thesis", "delivered", C.thread],
            ["Side project", "promised: fix the login bug", "partial", C.warn],
          ].map(([n, p, v, col], k) => (
            <g key={n} opacity={seg(t, S(5) + 0.8 + k * 0.6, 0.4)}>
              <text x={560} y={270 + k * 66} fontFamily={body} fontWeight={600} fontSize={26} fill={C.leather2}>{n}</text>
              <text x={760} y={270 + k * 66} fontFamily={body} fontSize={24} fill={C.leather}>{p}</text>
              <text x={1360} y={270 + k * 66} textAnchor="end" fontFamily={mono} fontSize={22} fill={col === C.thread ? "#2f7d73" : "#b5502f"}>{v}</text>
            </g>
          ))}
          <g opacity={seg(t, S(5) + 3.2, 0.3)} transform={`rotate(-4 960 500) translate(0 ${(1 - seg(t, S(5) + 3.2, 0.3)) * -10})`}>
            <rect x={640} y={462} width={640} height={62} rx={8} fill="none" stroke="#b5502f" strokeWidth={4} />
            <text x={960} y={503} textAnchor="middle" fontFamily={mono} fontWeight={500} fontSize={24} fill="#b5502f">SIDE PROJECT · TOMORROW'S SHARE −1</text>
          </g>
        </g>

        {/* scene 6: the morning note */}
        <g opacity={span(t, 6, 6)} transform={`translate(0 ${(1 - ease(seg(t, S(6), 0.8))) * 40})`}>
          <rect x={740} y={90} width={440} height={770} rx={54} fill={C.night} stroke={C.line} strokeWidth={6} />
          <rect x={760} y={110} width={400} height={730} rx={40} fill={C.night2} />
          <text x={960} y={200} textAnchor="middle" fontFamily={display} fontSize={64} fill={C.parchment}>7:00</text>
          <rect x={780} y={235} width={360} height={585} rx={22} fill={C.panel} />
          <text x={804} y={276} fontFamily={mono} fontSize={18} fill={C.lamp} letterSpacing={1}>COBBLER · MORNING NOTE</text>
          {[
            ["DONE", "Book: chapter 3 outlined", C.thread],
            ["DONE", "Investing: thesis tested", C.thread],
            ["PARTIAL", "Side project: notes left", C.warn],
            ["NEEDS YOU", "Garden: layout A or B?", C.lamp],
            ["NEW IDEA", "Trip planning. Approve?", C.muted],
            ["BUDGET", "9 of 20 used this week", C.muted],
          ].map(([k, v, col], i) => (
            <g key={i} opacity={seg(t, S(6) + 1 + i * 0.45, 0.4)}>
              <text x={804} y={326 + i * 82} fontFamily={mono} fontSize={17} fill={col}>{k}</text>
              <text x={804} y={356 + i * 82} fontFamily={body} fontSize={24} fill={C.parchment}>{v}</text>
            </g>
          ))}
        </g>

        {/* scene 7: steer with a sentence */}
        <g opacity={span(t, 7, 7)}>
          <rect x={560} y={150} width={800} height={330} rx={14} fill={C.parchment} />
          <text x={600} y={214} fontFamily={display} fontSize={42} fill={C.leather2}>Book · brief</text>
          <text x={600} y={272} fontFamily={body} fontSize={26} fill={C.leather}>Goal: a finished first draft by March.</text>
          <line x1={600} y1={306} x2={1320} y2={306} stroke={C.muted} strokeWidth={2} />
          <text x={600} y={360} fontFamily={mono} fontSize={22} fill={C.leather}>NOTE</text>
          {(() => {
            const msg = "Start with the ending, then work back.";
            const n = Math.floor(clamp((t - S(7) - 1.1) * 16, 0, msg.length));
            const caret = n < msg.length || Math.floor(t * 2) % 2 === 0;
            return <text x={600} y={412} fontFamily={display} fontSize={36} fill={C.leather2}>{msg.slice(0, n)}{caret ? "|" : ""}</text>;
          })()}
          <g opacity={seg(t, S(7) + 4.2, 0.6)}>
            <path d="M960 500 C960 600 960 600 960 700" stroke={C.thread} strokeWidth={4} strokeDasharray="10 8" strokeDashoffset={-t * 24} fill="none" />
            <Folder x={912} y={720} o={1} />
            <text x={1040} y={765} fontFamily={mono} fontSize={22} fill={C.thread}>read on its next wake · 04:40</text>
          </g>
        </g>

        {/* scene 8: end card */}
        <g opacity={span(t, 8, 8, 0.8)}>
          <circle cx={960} cy={430} r={300} fill="url(#glow)" opacity={0.45} />
          <text x={960} y={500} textAnchor="middle" fontFamily={display} fontSize={190} fill={C.parchment}>Cobbler</text>
          <clipPath id="endclip"><rect x={600} y={530} width={720 * ease(seg(t, S(8) + 0.6, 1.6))} height={30} /></clipPath>
          <line x1={640} y1={548} x2={1280} y2={548} stroke={C.thread} strokeWidth={6} strokeDasharray="22 12" strokeLinecap="round" clipPath="url(#endclip)" />
          <text x={960} y={650} textAnchor="middle" fontFamily={body} fontSize={40} fill={C.muted} opacity={seg(t, S(8) + 1.4, 0.8)}>
            {SCENES[8].p}
          </text>
        </g>
      </svg>

      {/* chrome */}
      <div style={{ position: "absolute", left: 150, top: 54, fontFamily: display, fontSize: 38, color: C.parchment, opacity: 1 - span(t, 8, 8) }}>
        Cobbler
      </div>
      <Clock t={t} />
      <Caption t={t} />
    </AbsoluteFill>
  );
};
