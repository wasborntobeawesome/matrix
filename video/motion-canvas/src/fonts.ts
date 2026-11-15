// Load the shared brand fonts before any scene is constructed, so text layout
// (which Motion Canvas measures through the DOM) uses the real fonts.
import interUrl from '../assets/Inter.ttf?url';
import arabicUrl from '../assets/NotoSansArabic.ttf?url';

export const FONT = 'Inter, "Noto Sans Arabic", "Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", sans-serif';

const faces = [
  new FontFace('Inter', `url(${interUrl})`, {weight: '100 900'}),
  new FontFace('Noto Sans Arabic', `url(${arabicUrl})`, {weight: '100 900'}),
];
await Promise.all(faces.map(f => f.load()));
faces.forEach(f => document.fonts.add(f));
// Warm up the emoji fallback font as well.
await document.fonts.load(`64px ${FONT}`, '😊😮😌🎙️');
