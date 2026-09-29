// Renders app-logo.png (512x512) from the LogoMark geometry so the raster
// never drifts from the SVG. Usage: node scripts/render_logo.mjs
import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createCanvas } from 'canvas' // dev dependency, see Step 6

const size = 512
const c = createCanvas(size, size)
const g = c.getContext('2d')
const s = size / 100
const poly = (pts, fill, alpha = 1) => {
  g.globalAlpha = alpha
  g.fillStyle = fill
  g.beginPath()
  pts.forEach(([x, y], i) => (i ? g.lineTo(x * s, y * s) : g.moveTo(x * s, y * s)))
  g.closePath()
  g.fill()
  g.globalAlpha = 1
}
g.fillStyle = '#f4fafb'
g.fillRect(0, 0, size, size)
poly([[14, 38], [38, 26], [38, 50]], '#0b7c80')
poly([[42, 30], [62, 20], [62, 40]], '#3aa7aa')
poly([[66, 23], [82, 15], [82, 31]], '#8fd4d6')
g.strokeStyle = '#bcd3da'; g.lineWidth = 2 * s
g.beginPath(); g.moveTo(10 * s, 54 * s); g.lineTo(90 * s, 54 * s); g.stroke()
poly([[14, 70], [38, 82], [38, 58]], '#0b7c80', 0.35)
poly([[42, 78], [62, 88], [62, 68]], '#3aa7aa', 0.35)
poly([[66, 85], [82, 93], [82, 77]], '#8fd4d6', 0.35)
writeFileSync(resolve('..', 'app-logo.png'), c.toBuffer('image/png'))
console.log('wrote ../app-logo.png')
