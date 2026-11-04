import { Composition } from "remotion";
import { Explainer } from "./Explainer";
import { FPS, TOTAL } from "./scenes";

export const Root = () => (
  <Composition id="Explainer" component={Explainer} durationInFrames={Math.round(TOTAL * FPS)}
    fps={FPS} width={1920} height={1080} />
);
