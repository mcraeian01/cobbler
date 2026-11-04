// The script. Edit words or durations here; every animation is timed from these starts.
export const FPS = 30;

export type Scene = { d: number; clock: string[]; h: string; p: string };

export const SCENES: Scene[] = [
  { d: 7, clock: ["21:00", "23:30"], h: "More projects than evenings.",
    p: "A book to write, money to invest, a side project. Each one needs a few focused hours you don't have." },
  { d: 8, clock: ["00:10"], h: "Give each project its own worker.",
    p: "Every project gets a brief, a memory of its own, and walls. A worker sees its own bench and nothing else." },
  { d: 8, clock: ["04:40"], h: "Every night, each worker pitches.",
    p: "One concrete next step and what it would cost. The foreman reads every pitch, then decides." },
  { d: 9, clock: ["04:41"], h: "A fixed budget goes to the best pitches.",
    p: "The foreman shares out a set amount of effort, keeps some back for tomorrow, and can grant less than was asked." },
  { d: 8, clock: ["05:10", "06:10"], h: "Then they work, inside their allowance.",
    p: "One focused step each. When the allowance runs low, they stop and write down exactly where they left off." },
  { d: 8, clock: ["06:20"], h: "Promises are checked.",
    p: "What a worker said it would deliver is compared with what it did. Overpromise, and tomorrow's share shrinks." },
  { d: 8, clock: ["07:00"], h: "By morning, one short note.",
    p: "What got done, what's waiting on you, and any new project worth starting. It fits on one phone screen." },
  { d: 8, clock: ["21:30"], h: "Steer with a sentence.",
    p: "Write a note on a project's brief. Its worker reads it the next time it wakes. There's nothing else to manage." },
  { d: 7, clock: [""], h: "Cobbler",
    p: "Self-directed AI agents that work through the night, on a budget you set." },
];

export const STARTS: number[] = [];
let acc = 0;
for (const s of SCENES) { STARTS.push(acc); acc += s.d; }
export const TOTAL = acc;

export const C = {
  night: "#0d1324", night2: "#151d36", panel: "#1b2442", line: "#2e3a63",
  parchment: "#ece5d4", muted: "#a3a9c2", lamp: "#f0b54a", thread: "#5ec4b6",
  warn: "#ef8a6b", leather: "#4a3426", leather2: "#33241b", dawn: "#3b2d4d", dawnGlow: "#e9925a",
};
