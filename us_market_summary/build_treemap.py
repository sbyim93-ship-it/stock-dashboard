"""
Finviz 144개 업종 중 상승률 TOP10 / 하락률 TOP10을 뽑아서 트리맵 2개를 만든다.
- 박스 크기 = 등락폭의 절대값 (시가총액이 아니라 "얼마나 많이 움직였나" 기준, 2026-10-20부터)
- 박스 색 = 같은 등락폭을 농도로 표현 (상승 패널은 초록 계열, 하락 패널은 빨강 계열 —
  패널 안이 전부 같은 부호라 diverging이 아니라 sequential이 맞음)
- 마우스 호버 시 그 업종의 대표 종목 중 등락률 상위 5개를 보여준다. Finviz의 개별 종목
  스크리너(screener.ashx)는 JS로 렌더링돼서 서버사이드로 못 긁어오기 때문에(2026-10-20
  확인 — groups.ashx 집계 페이지만 서버 렌더링됨), industry_meta.py에 수동 큐레이션해둔
  업종별 대표 종목을 Kiwoom usa20100으로 실시간 조회해서 상위 5개를 추린다. 정확한 업종
  전체 1~5위는 아닐 수 있지만 대형/유동성 종목 위주라 그날의 주된 움직임은 대체로 잡힌다.

사용법: python build_treemap.py [출력경로]
"""

import json
import sys
from datetime import datetime

from fetch_data import fetch_finviz_industries, fetch_stock_quotes
from industry_meta import INDUSTRY_KO, INDUSTRY_TICKERS

TOP_N_INDUSTRIES = 10
TOP_N_STOCKS = 5
_ALT_EXCHANGE = {"NY": "ND", "ND": "NY", "NA": "NY"}


def _fetch_top_stocks_for_industries(industry_names: list[str]) -> dict[str, list[dict]]:
    """업종명 -> [{ticker, name, changePct}, ...] (등락률 큰 순으로 정렬된 전체 후보, 호출부에서 5개 자름)."""
    candidates = []  # (industry, ticker, exchange)
    for ind in industry_names:
        for ticker, exch in INDUSTRY_TICKERS.get(ind, []):
            candidates.append((ind, ticker, exch))

    if not candidates:
        return {}

    items = [(t, e) for _, t, e in candidates]
    quotes = fetch_stock_quotes(items)

    # 1차 실패분만 반대 거래소로 재시도
    retry_idx = [i for i, q in enumerate(quotes) if q is None]
    if retry_idx:
        retry_items = [(candidates[i][1], _ALT_EXCHANGE.get(candidates[i][2], "NY")) for i in retry_idx]
        retry_quotes = fetch_stock_quotes(retry_items)
        for pos, i in enumerate(retry_idx):
            if retry_quotes[pos] is not None:
                quotes[i] = retry_quotes[pos]

    by_industry: dict[str, list[dict]] = {}
    for (ind, ticker, _), q in zip(candidates, quotes):
        if q is None:
            continue
        by_industry.setdefault(ind, []).append(
            {"ticker": ticker, "name": q.get("name") or ticker, "changePct": q["changePct"]}
        )
    return by_industry


def build_panel_data(industries: list[dict], ascending: bool, stocks_by_industry: dict[str, list[dict]]) -> list[dict]:
    panel = []
    for r in industries:
        stocks = stocks_by_industry.get(r["name"], [])
        stocks_sorted = sorted(stocks, key=lambda s: s["changePct"], reverse=not ascending)[:TOP_N_STOCKS]
        panel.append({
            "name": r["name"],
            "nameKo": INDUSTRY_KO.get(r["name"], r["name"]),
            "changePct": r["changePct"],
            "size": abs(r["changePct"]),
            "stocks": stocks_sorted,
        })
    return panel


TEMPLATE = """<meta charset="utf-8">
<meta name="claude:viewport-fit-sides" content="cover">
<title>업종 히트맵</title>
<style>
:root {
  --bg: #f7f6f2;
  --surface: #ffffff;
  --fg: #1c1b19;
  --muted: #6b6a64;
  --border: #e4e2db;
  --gain-0: #eef3ea;
  --gain-1: #8fc98f;
  --gain-2: #1f8a4c;
  --gain-3: #0f5c30;
  --loss-0: #f6ece9;
  --loss-1: #e59a8c;
  --loss-2: #c0392b;
  --loss-3: #7e2217;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #15140f; --surface: #1d1c17; --fg: #ece9e2; --muted: #9a988f; --border: #322f27;
    --gain-0: #203321; --gain-1: #3f8f54; --gain-2: #38b566; --gain-3: #7fe3a0;
    --loss-0: #351f1b; --loss-1: #a8463a; --loss-2: #e5675c; --loss-3: #f3a89e;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #15140f; --surface: #1d1c17; --fg: #ece9e2; --muted: #9a988f; --border: #322f27;
  --gain-0: #203321; --gain-1: #3f8f54; --gain-2: #38b566; --gain-3: #7fe3a0;
  --loss-0: #351f1b; --loss-1: #a8463a; --loss-2: #e5675c; --loss-3: #f3a89e;
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
header { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 8px 20px; margin-bottom: 16px; }
h1 { font-size: 1.3rem; margin: 0; letter-spacing: -0.01em; }
.meta { color: var(--muted); font-size: 0.82rem; }
.panels { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
@media (max-width: 760px) { .panels { grid-template-columns: 1fr; } }
.panel-title { font-size: 0.95rem; font-weight: 700; margin: 0 0 8px; display: flex; align-items: center; gap: 6px; }
.panel-title.gain { color: var(--gain-2); }
.panel-title.loss { color: var(--loss-2); }
.treemap { width: 100%; height: min(60vh, 560px); min-height: 360px; position: relative; }
.treemap svg { width: 100%; height: 100%; display: block; }
.cell-rect { stroke: var(--bg); stroke-width: 1.5px; cursor: pointer; }
.cell-label { pointer-events: none; font-variant-numeric: tabular-nums; }
.tooltip {
  position: fixed; pointer-events: none; background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 10px 12px; font-size: 0.78rem; box-shadow: 0 4px 18px rgba(0,0,0,0.2);
  opacity: 0; transition: opacity 0.1s; z-index: 50; max-width: 260px;
}
.tooltip .t-name { font-weight: 700; margin-bottom: 2px; }
.tooltip .t-change { font-variant-numeric: tabular-nums; margin-bottom: 6px; color: var(--muted); }
.tooltip .t-stocklabel { font-size: 0.68rem; color: var(--muted); margin-bottom: 3px; }
.tooltip .t-stock-row { display: flex; justify-content: space-between; gap: 14px; font-variant-numeric: tabular-nums; padding: 1px 0; }
.tooltip .t-stock-row .nm { color: var(--fg); }
footer { margin-top: 18px; color: var(--muted); font-size: 0.74rem; }
</style>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;600;700&display=swap">
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>

<div class="wrap">
  <header>
    <h1>업종 히트맵</h1>
    <div class="meta">__ASOF__ · 박스 크기 = 등락폭 · 업종 상승·하락 TOP10, 호버 시 대표 종목 TOP5</div>
  </header>
  <div class="panels">
    <div>
      <p class="panel-title gain">🟢 상승 업종 TOP10</p>
      <div class="treemap" id="gain-map"></div>
    </div>
    <div>
      <p class="panel-title loss">🔴 하락 업종 TOP10</p>
      <div class="treemap" id="loss-map"></div>
    </div>
  </div>
  <footer>데이터: Finviz 업종 그룹 스크리너(업종 등락률) + Kiwoom 실시간 시세(업종 내 대표 종목) · 매일 아침 갱신</footer>
</div>
<div class="tooltip" id="tooltip"></div>

<script>
const GAIN_DATA = __GAIN_JSON__;
const LOSS_DATA = __LOSS_JSON__;

function colorScale(kind) {
  const css = getComputedStyle(document.documentElement);
  const stops = [0, 1, 2, 3].map(i => css.getPropertyValue(`--${kind}-${i}`).trim());
  return d3.scaleLinear().domain([0, 1, 2, 3].map(i => i)).range(stops).interpolate(d3.interpolateRgb);
}

function luminance(hex) {
  const rgb = d3.color(hex).rgb();
  const [r, g, b] = [rgb.r, rgb.g, rgb.b].map(v => {
    v /= 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function truncate(text, widthPx, fontSize) {
  const maxChars = Math.floor(widthPx / (fontSize * 0.56));
  return text.length > maxChars ? text.slice(0, Math.max(3, maxChars - 1)) + '…' : text;
}

function renderPanel(containerId, data, kind) {
  const container = document.getElementById(containerId);
  const width = container.clientWidth;
  const height = container.clientHeight;
  container.innerHTML = '';
  if (!data.length) {
    container.innerHTML = '<div style="color:var(--muted);font-size:0.8rem;padding:12px">데이터 없음</div>';
    return;
  }

  const maxSize = d3.max(data, d => d.size) || 1;
  const scale = colorScale(kind);

  const root = d3.hierarchy({ children: data }).sum(d => d.size);
  d3.treemap().size([width, height]).paddingInner(2)(root);

  const svg = d3.select(container).append('svg').attr('viewBox', `0 0 ${width} ${height}`);
  const tooltip = d3.select('#tooltip');

  const leaf = svg.selectAll('g').data(root.leaves()).join('g')
    .attr('transform', d => `translate(${d.x0},${d.y0})`);

  leaf.append('rect')
    .attr('class', 'cell-rect')
    .attr('width', d => Math.max(0, d.x1 - d.x0))
    .attr('height', d => Math.max(0, d.y1 - d.y0))
    .attr('fill', d => scale((d.data.size / maxSize) * 3))
    .on('mousemove', (event, d) => {
      const stockRows = d.data.stocks.length
        ? d.data.stocks.map(s =>
            `<div class="t-stock-row"><span class="nm">${s.name}</span><span>${s.changePct >= 0 ? '+' : ''}${s.changePct.toFixed(2)}%</span></div>`
          ).join('')
        : '<div class="t-stock-row" style="color:var(--muted)">대표 종목 데이터 없음</div>';
      tooltip.style('opacity', 1)
        .style('left', Math.min(event.clientX + 14, window.innerWidth - 270) + 'px')
        .style('top', Math.min(event.clientY + 14, window.innerHeight - 160) + 'px')
        .html(`<div class="t-name">${d.data.nameKo}</div>
               <div class="t-change">${d.data.changePct >= 0 ? '+' : ''}${d.data.changePct.toFixed(2)}%</div>
               <div class="t-stocklabel">대표 종목 등락률 TOP5</div>${stockRows}`);
    })
    .on('mouseleave', () => tooltip.style('opacity', 0));

  leaf.each(function (d) {
    const w = d.x1 - d.x0, h = d.y1 - d.y0;
    if (w < 40 || h < 24) return;
    const fill = scale((d.data.size / maxSize) * 3);
    const textColor = luminance(fill) > 0.5 ? '#1a1a1a' : '#f5f5f3';
    const g = d3.select(this);
    const nameSize = Math.min(13, Math.max(9, w / 10));
    g.append('text').attr('class', 'cell-label')
      .attr('x', 6).attr('y', 16).attr('fill', textColor)
      .style('font-size', nameSize + 'px').style('font-weight', 600)
      .text(truncate(d.data.nameKo, w - 10, nameSize));
    if (h > 34) {
      g.append('text').attr('class', 'cell-label')
        .attr('x', 6).attr('y', 32).attr('fill', textColor)
        .style('font-size', (nameSize + 1) + 'px').style('font-weight', 700)
        .text((d.data.changePct >= 0 ? '+' : '') + d.data.changePct.toFixed(2) + '%');
    }
  });
}

function renderAll() {
  renderPanel('gain-map', GAIN_DATA, 'gain');
  renderPanel('loss-map', LOSS_DATA, 'loss');
}

renderAll();
window.addEventListener('resize', () => { clearTimeout(window.__rtmo); window.__rtmo = setTimeout(renderAll, 120); });
</script>
"""


def build_html(gain_panel: list[dict], loss_panel: list[dict]) -> str:
    asof = datetime.now().strftime("%Y-%m-%d %H:%M 기준")
    html = TEMPLATE
    html = html.replace("__ASOF__", asof)
    html = html.replace("__GAIN_JSON__", json.dumps(gain_panel, ensure_ascii=False))
    html = html.replace("__LOSS_JSON__", json.dumps(loss_panel, ensure_ascii=False))
    return html


def main(out_path: str) -> None:
    industries = fetch_finviz_industries()
    if not industries:
        print("FAILED: no industry data (Finviz fetch failed)")
        sys.exit(1)

    gainers = industries[:TOP_N_INDUSTRIES]
    losers = sorted(industries, key=lambda r: r["changePct"])[:TOP_N_INDUSTRIES]

    names = [r["name"] for r in gainers] + [r["name"] for r in losers]
    stocks_by_industry = _fetch_top_stocks_for_industries(names)

    gain_panel = build_panel_data(gainers, ascending=False, stocks_by_industry=stocks_by_industry)
    loss_panel = build_panel_data(losers, ascending=True, stocks_by_industry=stocks_by_industry)

    html = build_html(gain_panel, loss_panel)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {out_path} (gain={len(gain_panel)}, loss={len(loss_panel)} industries)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "treemap_output.html")
