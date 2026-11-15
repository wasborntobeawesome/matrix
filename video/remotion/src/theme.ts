export const SAFE = {
  top: 220, // Instagram UI overlay
  bottom: 380,
  captionTop: 1370, // caption pill sits in the lower third, ends well above 1540
};

/** '#RRGGBB' + alpha -> rgba() */
export const alpha = (hex: string, a: number) => {
  const h = hex.replace('#', '');
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const n = parseInt(full, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
};
