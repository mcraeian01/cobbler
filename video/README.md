# Explainer video

The 71-second explainer in `docs/` is built with [Remotion](https://www.remotion.dev): the video is React code, rendered to MP4.

- `src/scenes.ts`: the script. Nine scenes with on-screen words, clock times and durations. Change the words or timing here; every animation is timed from it.
- `src/Explainer.tsx`: the picture. One SVG stage (benches, foreman, budget jar, ledger, phone, brief, end card), driven by the current frame.

## Use
Needs Node 18 or later.

```
cd video
npm install
npm run studio        # live preview with a timeline scrubber, in the browser
npm run render        # out/cobbler-explainer.mp4 (1920x1080, 30 fps)
npx remotion still Explainer out/frame.png --frame=900   # one frame, for checking a change
```

To refresh the copies in `docs/`:

```
cp out/cobbler-explainer.mp4 ../docs/
npx remotion render Explainer ../docs/cobbler-explainer.gif --codec=gif --every-nth-frame=4 --scale=0.36
```

## Narration
No voice yet; the words are captions. To add one, record or generate a voiceover that follows `src/scenes.ts`, save it as `public/voice.mp3`, and add `<Audio src={staticFile("voice.mp3")} />` to `Explainer.tsx`. Adjust scene durations to the read.

Remotion has its own licence: free for individuals and small teams; larger companies need a paid licence (https://www.remotion.dev/license).
