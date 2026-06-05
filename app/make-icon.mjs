// JB×AX 앱 아이콘 생성 — SVG → PNG (sharp). assets/ 폴더에 전경/배경 출력.
import sharp from "sharp";
import { mkdirSync } from "fs";

mkdirSync("assets", { recursive: true });

// 배경: 파란 그라데이션 (어댑티브 아이콘은 시스템이 모서리를 둥글게 마스킹)
const bg = `
<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0a3d7a"/>
      <stop offset="1" stop-color="#1565c0"/>
    </linearGradient>
  </defs>
  <rect width="1024" height="1024" fill="url(#g)"/>
</svg>`;

// 전경: JB(흰색) + ×AX(골드). 중앙 안전영역(약 66%) 안에 배치.
const fg = `
<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024">
  <defs>
    <linearGradient id="gold" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#ffd54a"/>
      <stop offset="1" stop-color="#ffb300"/>
    </linearGradient>
  </defs>
  <g font-family="Arial, 'Noto Sans KR', sans-serif" text-anchor="middle">
    <text x="512" y="540" font-size="360" font-weight="900" fill="#ffffff" letter-spacing="-10">JB</text>
    <text x="512" y="700" font-size="150" font-weight="900" fill="url(#gold)" letter-spacing="2">×AX</text>
  </g>
</svg>`;

await sharp(Buffer.from(bg)).png().toFile("assets/icon-background.png");
await sharp(Buffer.from(fg)).png().toFile("assets/icon-foreground.png");

// 레거시(둥근사각 합성) 아이콘 소스도 생성: 배경 위에 전경 합성
await sharp(Buffer.from(bg))
  .composite([{ input: Buffer.from(fg) }])
  .png()
  .toFile("assets/icon-only.png");

console.log("아이콘 생성 완료: assets/icon-foreground.png, icon-background.png, icon-only.png");
