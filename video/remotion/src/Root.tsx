import {Composition} from 'remotion';
import {Promo} from './Promo';
import {DURATION_FRAMES, timeline} from './timeline';

export const RemotionRoot: React.FC = () => (
  <Composition
    id="Promo"
    component={Promo}
    width={timeline.width}
    height={timeline.height}
    fps={timeline.fps}
    durationInFrames={DURATION_FRAMES}
  />
);
