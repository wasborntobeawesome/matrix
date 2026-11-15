import {Config} from '@remotion/cli/config';

Config.setVideoImageFormat('jpeg');
Config.setJpegQuality(95);
Config.setPixelFormat('yuv420p');
Config.setOverwriteOutput(true);
// tag the output as BT.709 (otherwise ffmpeg writes full-range yuvj420p from the JPEG frames)
Config.setColorSpace('bt709');
