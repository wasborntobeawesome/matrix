import {brand} from './timeline';
import {alpha} from './theme';

/** Rounded gradient tile with "sound bars" — the Ovoz app mark. */
export const LogoMark: React.FC<{size: number; bars?: number[]}> = ({size, bars}) => {
  const b = bars ?? [0.45, 0.85, 0.6, 1, 0.5];
  return (
    <div
      style={{
        width: size,
        height: size,
        flexShrink: 0,
        borderRadius: size * 0.3,
        background: `linear-gradient(135deg, ${brand.primary}, ${brand.accent})`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: size * 0.06,
        boxShadow: `0 ${size * 0.15}px ${size * 0.5}px ${alpha(brand.primary, 0.45)}`,
      }}
    >
      {b.map((v, i) => (
        <div
          key={i}
          style={{width: size * 0.08, height: size * (0.18 + 0.42 * v), borderRadius: size, background: '#FFFFFF'}}
        />
      ))}
    </div>
  );
};
