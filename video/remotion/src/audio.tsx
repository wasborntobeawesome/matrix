import {createContext, useContext, useMemo} from 'react';
import {useAudioData, visualizeAudio, type AudioData} from '@remotion/media-utils';
import {staticFile, useCurrentFrame, useVideoConfig} from 'remotion';

// One decode of the voice-over shared by every audio-reactive element.
const AudioCtx = createContext<AudioData | null>(null);

export const VoiceProvider: React.FC<{children: React.ReactNode}> = ({children}) => {
  const data = useAudioData(staticFile('voiceover.wav'));
  return <AudioCtx.Provider value={data}>{children}</AudioCtx.Provider>;
};

const rmsAt = (audio: AudioData, t: number, win: number) => {
  const ch = audio.channelWaveforms[0];
  const from = Math.max(0, Math.floor(t * audio.sampleRate));
  const to = Math.min(ch.length, Math.floor((t + win) * audio.sampleRate));
  if (to <= from) return 0;
  let sum = 0;
  for (let i = from; i < to; i += 4) sum += ch[i] * ch[i];
  return Math.sqrt(sum / ((to - from) / 4));
};

/** 0..1 loudness of the voice-over around the current frame (smoothed). */
export const useVoiceLevel = () => {
  const audio = useContext(AudioCtx);
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return useMemo(() => {
    if (!audio) return 0;
    const t = frame / fps;
    const r = (rmsAt(audio, t - 1 / fps, 1 / fps) + 2 * rmsAt(audio, t, 1 / fps) + rmsAt(audio, t + 1 / fps, 1 / fps)) / 4;
    return Math.min(1, r * 5);
  }, [audio, frame, fps]);
};

/** Frequency bars (0..1) for the current frame, low bins emphasised for voice. */
export const useVoiceBars = (count: number) => {
  const audio = useContext(AudioCtx);
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return useMemo(() => {
    if (!audio) return new Array(count).fill(0);
    const spectrum = visualizeAudio({fps, frame, audioData: audio, numberOfSamples: 128});
    const out: number[] = [];
    for (let i = 0; i < count; i++) {
      // voice energy lives in the lower ~third of the spectrum
      const idx = Math.floor(Math.pow(i / count, 1.4) * 44) + 1;
      const v = spectrum[idx] ?? 0;
      out.push(Math.min(1, Math.pow(v * 9, 0.75)));
    }
    return out;
  }, [audio, frame, fps, count]);
};
