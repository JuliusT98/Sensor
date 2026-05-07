/**
 * Jet-Colormap: normalisiert einen NIR-Wert auf [0,1] und interpoliert
 * durch die klassischen Jet-Farben: dunkelblau → cyan → grün → gelb → rot.
 * Gibt [r, g, b, a] als Integer-Array (0–255) zurück.
 */
export function nirToColor(
  value: number,
  min: number,
  max: number
): [number, number, number, number] {
  if (max === min) return [0, 212, 255, 255];

  const t = Math.max(0, Math.min(1, (value - min) / (max - min)));

  // Jet colormap control points: [t, r, g, b]
  const stops: [number, number, number, number][] = [
    [0.00,   0,   0, 143],
    [0.125,  0,   0, 255],
    [0.375,  0, 255, 255],
    [0.50,   0, 128,   0],
    [0.625, 255, 255,   0],
    [0.875, 255,   0,   0],
    [1.00,  128,   0,   0],
  ];

  // Find bracket
  for (let i = 0; i < stops.length - 1; i++) {
    const [t0, r0, g0, b0] = stops[i];
    const [t1, r1, g1, b1] = stops[i + 1];
    if (t >= t0 && t <= t1) {
      const f = (t - t0) / (t1 - t0);
      return [
        Math.round(r0 + f * (r1 - r0)),
        Math.round(g0 + f * (g1 - g0)),
        Math.round(b0 + f * (b1 - b0)),
        255,
      ];
    }
  }

  // Fallback: last color
  const last = stops[stops.length - 1];
  return [last[1], last[2], last[3], 255];
}

/**
 * Gibt die Jet-Farbe als CSS-Hex-String zurück.
 */
export function nirToHex(value: number, min: number, max: number): string {
  const [r, g, b] = nirToColor(value, min, max);
  return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
}
