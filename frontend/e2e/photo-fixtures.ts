/** Generated landscape pixels: deterministic, local, and free of household image data. */
import { deflateSync } from 'node:zlib';

function crc32(data: Buffer): number {
  let value = 0xffffffff;
  for (const byte of data) {
    value ^= byte;
    for (let bit = 0; bit < 8; bit += 1) value = (value >>> 1) ^ ((value & 1) ? 0xedb88320 : 0);
  }
  return (value ^ 0xffffffff) >>> 0;
}
function chunk(type: string, data: Buffer): Buffer {
  const label = Buffer.from(type); const size = Buffer.alloc(4); size.writeUInt32BE(data.length);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(Buffer.concat([label, data])));
  return Buffer.concat([size, label, data, crc]);
}
export function landscapePng(seed = 0, width = 960, height = 640): Buffer {
  const pixels = Buffer.alloc(height * (1 + width * 3));
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const nx = x / width; const ny = y / height;
      const ridge = 0.47 + Math.sin(nx * 8 + seed) * 0.12 + Math.sin(nx * 17 - seed) * 0.05;
      const mountain = ny > ridge;
      const foreground = ny > 0.8 + Math.sin(nx * 6 + seed) * 0.08;
      const sun = Math.hypot(nx - 0.74, (ny - 0.24) * height / width) < 0.045;
      const color = sun ? [250, 220, 155] : foreground ? [25 + seed * 3 % 30, 55, 51] : mountain ? [52 + seed * 7 % 40, 83 + ny * 35, 98] : [115 + ny * 80 + seed * 5 % 30, 150 + ny * 40, 185 - ny * 30];
      const at = y * (1 + width * 3) + 1 + x * 3;
      for (let channel = 0; channel < 3; channel += 1) pixels[at + channel] = Math.round(color[channel]);
    }
  }
  const header = Buffer.alloc(13); header.writeUInt32BE(width); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 2;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(pixels)), chunk('IEND', Buffer.alloc(0))]);
}
