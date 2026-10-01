"""
report.py가 Claude 호출 전에 쓰는 정밀 수치 수집 모듈.
- 지수(S&P500/나스닥/다우/러셀2000/SOX)는 yfinance로 공식 종가를 직접 가져온다
  (Kiwoom REST API는 미국 지수 자체의 시세 조회를 지원하지 않음 — /api/us/mrkcond,
  /api/us/chart 둘 다 stex_tp가 NA/ND/NY(개별 종목 거래소)만 허용, INX/DJI/IXIC 같은
  지수코드는 거부됨. usa10102는 지수 이름 목록만 주고 가격은 안 줌 — 확인됨).
- 섹터 강세/약세는 SPDR 11개 섹터 ETF(XLK/XLF/XLE/XLV/XLY/XLP/XLI/XLB/XLU/XLC/XLRE)의
  등락률을 Kiwoom usa20100(미국주식 현재가 종목정보)으로 직접 조회해서 정렬한다.
  (usa23100 업종별 등락률 상위/하위는 종목 단위 랭킹이라 워런트/소형주가 대부분이라
  섹터 강세 판단에는 안 맞음 — 섹터 ETF 조회가 훨씬 깨끗한 신호)
- 시장 지표(국채금리/유가/금/비트코인)도 yfinance로 가져온다.
- fetch_stock_quote()는 대화형 /us_market_summary 스킬이 뉴스로 찾아낸 특정 종목의
  정확한 현재가/등락률을 검증할 때 개별 호출용으로 쓴다 (build_data_block에는 포함 안 됨 —
  어떤 종목이 그날의 주요 상승/하락 종목이 될지는 미리 알 수 없어서).
개별 종목 뉴스/사유/한국 증시 영향은 여전히 웹서치가 채운다.

Kiwoom usa20100 응답의 cur_prc는 부호가 가격이 아니라 당일 등락 방향을 나타낸다
(상승 시 +, 하락 시 -) — 그래서 실제 가격은 abs(cur_prc)를 써야 한다. 실제로 관찰됨:
MU가 하락한 날 cur_prc="-1046.31"로 왔는데 Micron 주가가 음수일 리는 없음.
"""

import os

import requests
import yfinance as yf

INDEX_SYMBOLS = {
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq",
    "^DJI": "Dow Jones",
    "^RUT": "Russell 2000",
    "^SOX": "필라델피아 반도체 (SOX)",
}

SECTOR_ETFS = {
    "XLK": "기술",
    "XLF": "금융",
    "XLE": "에너지",
    "XLV": "헬스케어",
    "XLY": "임의소비재",
    "XLP": "필수소비재",
    "XLI": "산업재",
    "XLB": "소재",
    "XLU": "유틸리티",
    "XLC": "커뮤니케이션",
    "XLRE": "리츠",
}

INDICATOR_SYMBOLS = {
    "^TNX": "10Y 국채 금리",
    "^TYX": "30Y 국채 금리",
    "CL=F": "WTI 유가",
    "BZ=F": "Brent 유가",
    "GC=F": "금 (Gold)",
    "BTC-USD": "비트코인",
}

KIWOOM_TOKEN_URL = "https://api.kiwoom.com/oauth2/token"
KIWOOM_QUOTE_URL = "https://api.kiwoom.com/api/us/mrkcond"


def fetch_indicators() -> list[dict]:
    """국채금리/유가/금/비트코인 — 실패한 항목은 결과에서 생략."""
    results = []
    for sym, name in INDICATOR_SYMBOLS.items():
        try:
            fi = yf.Ticker(sym).fast_info
            price = fi.last_price
            prev = fi.previous_close
            if price is None or not prev:
                continue
            chg_pct = round((price - prev) / prev * 100, 2)
            results.append({"name": name, "price": round(float(price), 2), "changePct": chg_pct})
        except Exception as e:
            print(f"  [지표] {name} 조회 실패: {e}")
    return results


def fetch_indices() -> list[dict]:
    """지수별 종가/등락률 — 실패한 개별 지수는 결과에서 생략(추정치로 채우지 않음)."""
    results = []
    for sym, name in INDEX_SYMBOLS.items():
        try:
            fi = yf.Ticker(sym).fast_info
            price = fi.last_price
            prev = fi.previous_close
            if price is None or not prev:
                continue
            chg_pct = round((price - prev) / prev * 100, 2)
            results.append({"name": name, "price": round(float(price), 2), "changePct": chg_pct})
        except Exception as e:
            print(f"  [지수] {name} 조회 실패: {e}")
    return results


def _kiwoom_token(app_key: str, app_secret: str) -> str:
    res = requests.post(
        KIWOOM_TOKEN_URL,
        headers={"Content-Type": "application/json;charset=UTF-8"},
        json={"grant_type": "client_credentials", "appkey": app_key, "secretkey": app_secret},
        timeout=10,
    )
    res.raise_for_status()
    token = res.json().get("token")
    if not token:
        raise RuntimeError(f"Kiwoom 토큰 발급 실패: {res.text}")
    return token


def _kiwoom_headers() -> dict | None:
    """KIWOOM_APP_KEY/SECRET 환경변수로 토큰을 발급받아 요청 헤더를 만든다. 실패 시 None."""
    app_key = os.environ.get("KIWOOM_APP_KEY")
    app_secret = os.environ.get("KIWOOM_APP_SECRET")
    if not app_key or not app_secret:
        print("  [Kiwoom] KIWOOM_APP_KEY/SECRET 없음 — 조회 건너뜀")
        return None
    try:
        token = _kiwoom_token(app_key, app_secret)
    except Exception as e:
        print(f"  [Kiwoom] 토큰 발급 실패: {e}")
        return None
    return {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "secretkey": app_secret,
        "api-id": "usa20100",
    }


def fetch_sectors() -> list[dict]:
    """
    SPDR 11개 섹터 ETF 등락률을 등락률 내림차순으로 반환.
    KIWOOM_APP_KEY/KIWOOM_APP_SECRET 환경변수가 없거나 API 실패 시 빈 리스트 반환
    (report.py 쪽에서 빈 리스트면 섹터도 웹서치로 채우도록 폴백).
    """
    headers = _kiwoom_headers()
    if headers is None:
        return []

    results = []
    for ticker, name in SECTOR_ETFS.items():
        try:
            res = requests.post(
                KIWOOM_QUOTE_URL, headers=headers,
                json={"stex_tp": "NY", "stk_cd": ticker}, timeout=10,
            )
            data = res.json()
            if data.get("return_code") != 0:
                print(f"  [섹터] {ticker} 조회 실패: {data.get('return_msg')}")
                continue
            results.append({
                "ticker": ticker,
                "name": name,
                "changePct": float(data["flu_rt"]),
            })
        except Exception as e:
            print(f"  [섹터] {ticker} 조회 실패: {e}")

    results.sort(key=lambda r: r["changePct"], reverse=True)
    return results


def fetch_stock_quote(stk_cd: str, stex_tp: str = "NY") -> dict | None:
    """
    특정 종목/ETF의 정확한 현재가·등락률을 조회한다 (usa20100).
    stex_tp: NASDAQ 상장 종목이면 'ND', NYSE/NYSE Arca(ETF 포함)면 'NY', AMEX면 'NA'.
    실패하거나 KIWOOM_APP_KEY/SECRET가 없으면 None을 반환한다.
    """
    headers = _kiwoom_headers()
    if headers is None:
        return None
    try:
        res = requests.post(
            KIWOOM_QUOTE_URL, headers=headers,
            json={"stex_tp": stex_tp, "stk_cd": stk_cd}, timeout=10,
        )
        data = res.json()
        if data.get("return_code") != 0:
            print(f"  [종목] {stk_cd} 조회 실패: {data.get('return_msg')}")
            return None
        return {
            "ticker": stk_cd,
            "name": data.get("stk_nm"),
            "price": abs(float(data["cur_prc"])),
            "changePct": float(data["flu_rt"]),
        }
    except Exception as e:
        print(f"  [종목] {stk_cd} 조회 실패: {e}")
        return None


def build_data_block() -> str:
    """Claude 프롬프트에 그대로 삽입할 '확인된 수치' 텍스트 블록."""
    indices = fetch_indices()
    indicators = fetch_indicators()
    sectors = fetch_sectors()

    lines = []
    if indices:
        lines.append("### 지수 (API로 확인된 정확한 수치 — 아래 값을 그대로 쓸 것, 재검색/재계산 금지)")
        for r in indices:
            lines.append(f"- {r['name']}: {r['price']:,} ({r['changePct']:+.2f}%)")
    else:
        lines.append("### 지수: API 조회 실패 — 웹서치로 직접 확인할 것")

    lines.append("")
    if indicators:
        lines.append("### 시장 지표 (API로 확인된 정확한 수치 — 아래 값을 그대로 쓸 것)")
        for r in indicators:
            lines.append(f"- {r['name']}: {r['price']:,} ({r['changePct']:+.2f}%)")
    else:
        lines.append("### 시장 지표: API 조회 실패 — 웹서치로 직접 확인할 것")

    lines.append("")
    if sectors:
        lines.append("### 섹터 ETF 등락률 (API로 확인된 정확한 수치, 등락률 내림차순 — 아래 값을 그대로 쓸 것)")
        for r in sectors:
            lines.append(f"- {r['name']}({r['ticker']}): {r['changePct']:+.2f}%")
    else:
        lines.append("### 섹터: API 조회 실패 — 웹서치로 직접 확인할 것")

    return "\n".join(lines)


if __name__ == "__main__":
    print(build_data_block())
