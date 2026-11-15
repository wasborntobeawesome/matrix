import {continueRender, delayRender, staticFile} from 'remotion';

// Both shipped fonts are variable fonts (wght axis), so one face covers 100-900.
const faces = [
  {family: 'Inter', file: 'fonts/Inter.ttf'},
  {family: 'Noto Sans Arabic', file: 'fonts/NotoSansArabic.ttf'},
];

let started = false;
export const loadFonts = () => {
  if (started || typeof document === 'undefined') return;
  started = true;
  const handle = delayRender('Loading Inter + Noto Sans Arabic');
  Promise.all(
    faces.map(async ({family, file}) => {
      const face = new FontFace(
        family,
        `url('${staticFile(file)}') format('truetype')`,
        {weight: '100 900', style: 'normal'},
      );
      await face.load();
      document.fonts.add(face);
    }),
  )
    .then(() => continueRender(handle))
    .catch((err) => {
      console.error(err);
      continueRender(handle);
    });
};

// Inter first; Arabic glyphs fall back to Noto Sans Arabic (shaped + RTL by
// Chromium/HarfBuzz); emoji fall back to the OS colour emoji font.
export const FONT_STACK =
  "'Inter', 'Noto Sans Arabic', 'Segoe UI Emoji', 'Apple Color Emoji', 'Noto Color Emoji', sans-serif";
export const ARABIC_STACK = "'Noto Sans Arabic', 'Inter', sans-serif";
export const EMOJI_STACK =
  "'Segoe UI Emoji', 'Apple Color Emoji', 'Noto Color Emoji', sans-serif";
