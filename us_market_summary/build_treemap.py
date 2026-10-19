"""
Finviz 144개 업종 데이터(시가총액+당일 등락률)로 트리맵 HTML 아티팩트를 만든다.
매일 "시작" 스킬 흐름에서 이 스크립트로 최신 데이터를 담은 HTML을 생성한 뒤,
Artifact 툴로 같은 URL에 재배포한다(날마다 새 링크를 만들지 않음).

사용법: python build_treemap.py [출력경로]
  출력경로 생략 시 ./treemap_output.html에 씀.
"""

import json
import sys
from datetime import datetime

from fetch_data import fetch_finviz_industry_treemap_data

TEMPLATE = """<meta name="claude:viewport-fit-sides" content="cover">
<title>업종 히트맵</title>
<style>
:root {
  --bg: #f7f6f2;
  --surface: #ffffff;
  --fg: #1c1b19;
  --muted: #6b6a64;
  --border: #e4e2db;
  --accent: #3d5a80;
  --cell-loss: #c0392b;
  --cell-neutral: #c7c5bd;
  --cell-gain: #1f8a4c;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #15140f;
    --surface: #1d1c17;
    --fg: #ece9e2;
    --muted: #9a988f;
    --border: #322f27;
    --accent: #7ea8d8;
    --cell-loss: #e5675c;
    --cell-neutral: #5c5a53;
    --cell-gain: #38b566;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #15140f;
  --surface: #1d1c17;
  --fg: #ece9e2;
  --muted: #9a988f;
  --border: #322f27;
  --accent: #7ea8d8;
  --cell-loss: #e5675c;
  --cell-neutral: #5c5a53;
  --cell-gain: #38b566;
  color-scheme: dark;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: "IBM Plex Sans KR", "Pretendard", -apple-system, sans-serif;
  padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px));
}
.wrap { max-width: 1400px; margin: 0 auto; padding-block: 20px 32px; }
header { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 8px 20px; margin-bottom: 14px; }
h1 { font-size: 1.3rem; margin: 0; letter-spacing: -0.01em; }
.meta { color: var(--muted); font-size: 0.82rem; }
.legend { display: flex; align-items: center; gap: 10px; margin: 4px 0 18px; flex-wrap: wrap; }
.legend-bar { width: 220px; max-width: 48vw; height: 10px; border-radius: 5px;
  background: linear-gradient(90deg, var(--cell-loss), var(--cell-neutral) 50%, var(--cell-gain)); }
.legend-labels { display: flex; justify-content: space-between; width: 220px; max-width: 48vw;
  font-size: 0.72rem; color: var(--muted); font-variant-numeric: tabular-nums; }
#treemap { width: 100%; height: min(78vh, 820px); min-height: 420px; position: relative; }
#treemap svg { width: 100%; height: 100%; display: block; }
.cell-rect { stroke: var(--bg); stroke-width: 1.5px; }
.cell-label {
  font-size: 11px;
  pointer-events: none;
  font-variant-numeric: tabular-nums;
}
.cell-name { font-weight: 600; }
.tooltip {
  position: fixed;
  pointer-events: none;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 0.78rem;
  box-shadow: 0 4px 16px rgba(0,0,0,0.18);
  opacity: 0;
  transition: opacity 0.1s;
  z-index: 50;
  max-width: 240px;
}
.tooltip .t-name { font-weight: 600; margin-bottom: 2px; }
.tooltip .t-row { color: var(--muted); display: flex; justify-content: space-between; gap: 12px; }
footer { margin-top: 18px; color: var(--muted); font-size: 0.74rem; }
</style>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;600;700&display=swap">
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>

<div class="wrap">
  <header>
    <h1>업종 히트맵</h1>
    <div class="meta">__ASOF__ · 박스 크기 = 시가총액 · 색 = 당일 등락률</div>
  </header>
  <div class="legend">
    <span style="font-size:0.72rem;color:var(--muted)">하락</span>
    <div>
      <div class="legend-bar"></div>
      <div class="legend-labels"><span>-__CLAMP__%</span><span>0</span><span>+__CLAMP__%</span></div>
    </div>
    <span style="font-size:0.72rem;color:var(--muted)">상승</span>
  </div>
  <div id="treemap"></div>
  <footer>데이터: Finviz 업종 그룹 스크리너 (144개 업종, 시가총액·당일 등락률) · 매일 아침 갱신</footer>
</div>
<div class="tooltip" id="tooltip"></div>

<script>
const DATA = __DATA_JSON__;
const CLAMP = __CLAMP__;

function cellColor(pct) {
  const css = getComputedStyle(document.documentElement);
  const loss = css.getPropertyValue('--cell-loss').trim();
  const neutral = css.getPropertyValue('--cell-neutral').trim();
  const gain = css.getPropertyValue('--cell-gain').trim();
  const t = Math.max(-1, Math.min(1, pct / CLAMP));
  if (t >= 0) return d3.interpolateRgb(neutral, gain)(t);
  return d3.interpolateRgb(neutral, loss)(-t);
}

function luminance(hex) {
  const rgb = d3.color(hex).rgb();
  const [r, g, b] = [rgb.r, rgb.g, rgb.b].map(v => {
    v /= 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function render() {
  const container = document.getElementById('treemap');
  const width = container.clientWidth;
  const height = container.clientHeight;
  container.innerHTML = '';

  const root = d3.hierarchy({ children: DATA }).sum(d => d.marketCapB);
  d3.treemap().size([width, height]).paddingInner(2)(root);

  const svg = d3.select(container).append('svg')
    .attr('viewBox', `0 0 ${width} ${height}`);

  const tooltip = d3.select('#tooltip');

  const leaf = svg.selectAll('g')
    .data(root.leaves())
    .join('g')
    .attr('transform', d => `translate(${d.x0},${d.y0})`);

  leaf.append('rect')
    .attr('class', 'cell-rect')
    .attr('width', d => Math.max(0, d.x1 - d.x0))
    .attr('height', d => Math.max(0, d.y1 - d.y0))
    .attr('fill', d => cellColor(d.data.changePct))
    .on('mousemove', (event, d) => {
      tooltip.style('opacity', 1)
        .style('left', Math.min(event.clientX + 14, window.innerWidth - 250) + 'px')
        .style('top', Math.min(event.clientY + 14, window.innerHeight - 90) + 'px')
        .html(`<div class="t-name">${d.data.name}</div>
               <div class="t-row"><span>시가총액</span><span>$${d.data.marketCapB.toLocaleString(undefined,{maximumFractionDigits:0})}B</span></div>
               <div class="t-row"><span>등락률</span><span>${d.data.changePct >= 0 ? '+' : ''}${d.data.changePct.toFixed(2)}%</span></div>`);
    })
    .on('mouseleave', () => tooltip.style('opacity', 0));

  leaf.each(function (d) {
    const w = d.x1 - d.x0, h = d.y1 - d.y0;
    if (w < 34 || h < 22) return;
    const fill = cellColor(d.data.changePct);
    const textColor = luminance(fill) > 0.42 ? '#1a1a1a' : '#f5f5f3';
    const g = d3.select(this);
    const pad = 5;
    const nameSize = Math.min(12, Math.max(9, w / 9));
    if (h > 34) {
      g.append('text').attr('class', 'cell-label cell-name')
        .attr('x', pad).attr('y', 15).attr('fill', textColor)
        .style('font-size', nameSize + 'px')
        .text(truncate(d.data.name, w, nameSize));
      g.append('text').attr('class', 'cell-label')
        .attr('x', pad).attr('y', 29).attr('fill', textColor)
        .style('font-size', (nameSize + 1) + 'px').style('font-weight', 700)
        .text((d.data.changePct >= 0 ? '+' : '') + d.data.changePct.toFixed(2) + '%');
    } else {
      g.append('text').attr('class', 'cell-label')
        .attr('x', pad).attr('y', h / 2 + 4).attr('fill', textColor)
        .style('font-size', nameSize + 'px').style('font-weight', 700)
        .text((d.data.changePct >= 0 ? '+' : '') + d.data.changePct.toFixed(1) + '%');
    }
  });
}

function truncate(text, widthPx, fontSize) {
  const maxChars = Math.floor(widthPx / (fontSize * 0.62));
  return text.length > maxChars ? text.slice(0, Math.max(3, maxChars - 1)) + '…' : text;
}

render();
window.addEventListener('resize', () => { clearTimeout(window.__rtmo); window.__rtmo = setTimeout(render, 120); });
</script>
"""


def build_html(data: list[dict], clamp: float = 4.0) -> str:
    asof = datetime.now().strftime("%Y-%m-%d %H:%M 기준")
    html = TEMPLATE
    html = html.replace("__ASOF__", asof)
    html = html.replace("__CLAMP__", str(clamp))
    html = html.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
    return html


if __name__ == "__main__":
    out_path = sys.argv[1] if len(sys.argv) > 1 else "treemap_output.html"
    data = fetch_finviz_industry_treemap_data()
    if not data:
        print("FAILED: no treemap data (Finviz fetch failed)")
        sys.exit(1)
    html = build_html(data)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {out_path} ({len(data)} industries)")
