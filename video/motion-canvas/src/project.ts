import './fonts';
import {makeProject} from '@motion-canvas/core';
import main from './scenes/main?scene';
import audio from '../assets/voiceover.wav';

export default makeProject({
  scenes: [main],
  audio,
});
