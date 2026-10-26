export function hsv(hex: string) {
  const values = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = values, max = Math.max(...values), min = Math.min(...values), delta = max - min;
  let hue = 0;
  if (delta) {
    if (max === r) hue = ((g - b) / delta) % 6;
    else if (max === g) hue = (b - r) / delta + 2;
    else hue = (r - g) / delta + 4;
  }
  return {hue: (hue * 60 + 360) % 360, saturation: max ? delta / max : 0};
}

export function hexFromHsv(hue: number, saturation: number) {
  const h = ((hue % 360) + 360) % 360 / 60;
  const x = saturation * (1 - Math.abs(h % 2 - 1)), m = 1 - saturation;
  const choices = [[saturation, x, 0], [x, saturation, 0], [0, saturation, x],
    [0, x, saturation], [x, 0, saturation], [saturation, 0, x]];
  return '#' + choices[Math.floor(h)].map(value => Math.round((value + m) * 255).toString(16).padStart(2, '0')).join('');
}
