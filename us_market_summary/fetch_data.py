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
개별 종목 뉴스/사유/한국 증시 영향은 여전히 Claude 웹서치가 채운다 — 여기서는 다루지 않음.
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

KIWOOM_TOKEN_URL = "https://api.kiwoom.com/oauth2/token"
KIWOOM_QUOTE_URL = "https://api.kiwoom.com/api/us/mrkcond"


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


def fetch_sectors() -> list[dict]:
    """
    SPDR 11개 섹터 ETF 등락률을 등락률 내림차순으로 반환.
    KIWOOM_APP_KEY/KIWOOM_APP_SECRET 환경변수가 없거나 API 실패 시 빈 리스트 반환
    (report.py 쪽에서 빈 리스트면 섹터도 웹서치로 채우도록 폴백).
    """
    app_key = os.environ.get("KIWOOM_APP_KEY")
    app_secret = os.environ.get("KIWOOM_APP_SECRET")
    if not app_key or not app_secret:
        print("  [섹터] KIWOOM_APP_KEY/SECRET 없음 — 섹터 ETF 조회 건너뜀")
        return []

    try:
        token = _kiwoom_token(app_key, app_secret)
    except Exception as e:
        print(f"  [섹터] Kiwoom 토큰 발급 실패: {e}")
        return []

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "secretkey": app_secret,
        "api-id": "usa20100",
    }

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


def build_data_block() -> str:
    """Claude 프롬프트에 그대로 삽입할 '확인된 수치' 텍스트 블록."""
    indices = fetch_indices()
    sectors = fetch_sectors()

    lines = []
    if indices:
        lines.append("### 지수 (API로 확인된 정확한 수치 — 아래 값을 그대로 쓸 것, 재검색/재계산 금지)")
        for r in indices:
            lines.append(f"- {r['name']}: {r['price']:,} ({r['changePct']:+.2f}%)")
    else:
        lines.append("### 지수: API 조회 실패 — 웹서치로 직접 확인할 것")

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
