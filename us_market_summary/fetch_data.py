"""
report.py가 Claude 호출 전에 쓰는 정밀 수치 수집 모듈. (대화형 /us_market_summary 스킬도
같은 모듈을 직접 호출한다 — 트리거는 "시작")
- 지수(S&P500/나스닥/다우/러셀2000/SOX)는 yfinance로 공식 종가를 직접 가져온다
  (Kiwoom REST API는 미국 지수 자체의 시세 조회를 지원하지 않음 — /api/us/mrkcond,
  /api/us/chart 둘 다 stex_tp가 NA/ND/NY(개별 종목 거래소)만 허용, INX/DJI/IXIC 같은
  지수코드는 거부됨. usa10102는 지수 이름 목록만 주고 가격은 안 줌 — 확인됨).
- 섹터 강세/약세는 SPDR 11개 섹터 ETF(XLK/XLF/XLE/XLV/XLY/XLP/XLI/XLB/XLU/XLC/XLRE)의
  등락률을 Kiwoom usa20100(미국주식 현재가 종목정보)으로 직접 조회해서 정렬한다.
  (usa23100 업종별 등락률 상위/하위는 종목 단위 랭킹이라 워런트/소형주가 대부분이라
  섹터 강세 판단에는 안 맞음 — 섹터 ETF 조회가 훨씬 깨끗한 신호)
- 시장 지표(국채금리/유가/금/비트코인)도 yfinance로 가져온다.
- fetch_stock_quotes()는 대화형 스킬이 뉴스로 찾아낸 종목들의 정확한 현재가/등락률을
  한 번에 검증할 때 쓴다 (build_data_block에는 포함 안 됨 — 어떤 종목이 그날의 주요
  상승/하락 종목이 될지는 미리 알 수 없어서). base_close/oyr_high/oyr_high_date도 같이
  돌려주는데, 이게 있으면 "이 급등이 정말 오늘 일인가"를 별도 호출 없이 바로 판단할 수
  있다 — 실제로 PTC/Cerebras가 검색엔 "오늘 급등"으로 나왔는데 oyr_high_date가 전날이라
  어제 일이 이미 반영된 상태였던 걸 이 필드로 잡아낸 적 있음 (2026-10-06).
개별 종목 뉴스/사유/한국 증시 영향은 여전히 웹서치가 채운다.

Kiwoom usa20100 응답의 cur_prc는 부호가 가격이 아니라 당일 등락 방향을 나타낸다
(상승 시 +, 하락 시 -) — 그래서 실제 가격은 abs(cur_prc)를 써야 한다. 실제로 관찰됨:
MU가 하락한 날 cur_prc="-1046.31"로 왔는데 Micron 주가가 음수일 리는 없음.

속도: 모든 조회는 ThreadPoolExecutor로 병렬 실행한다 (네트워크 왕복이 대부분이라 스레드로도
충분히 빨라짐). Kiwoom 토큰은 한 프로세스 실행당 한 번만 발급해서 재사용 — 섹터/종목 조회를
분리된 스크립트로 여러 번 실행하면 토큰도 매번 새로 받게 되니, 종목 검증이 여러 개 필요하면
fetch_stock_quotes()에 한꺼번에 몰아서 넘길 것 (하나씩 여러 번 호출하지 말 것).
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor

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
KIWOOM_MAX_CONCURRENCY = 4  # usa20100 유량 제한이 초당 5회라 여유를 좀 두고 4로 캡 (+1700 에러는 재시도로 보완)


def _parallel_map(fn, items, max_workers: int = 16) -> list:
    """items 각각에 fn을 병렬 적용, 입력 순서를 보존해 결과 리스트로 반환."""
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as pool:
        return list(pool.map(fn, items))


def _yf_quote(sym_name: tuple[str, str]) -> dict | None:
    sym, name = sym_name
    try:
        fi = yf.Ticker(sym).fast_info
        price = fi.last_price
        prev = fi.previous_close
        if price is None or not prev:
            return None
        chg_pct = round((price - prev) / prev * 100, 2)
        return {"name": name, "price": round(float(price), 2), "changePct": chg_pct}
    except Exception as e:
        print(f"  [yfinance] {name}({sym}) 조회 실패: {e}")
        return None


def fetch_indices() -> list[dict]:
    """지수별 종가/등락률 — 실패한 개별 지수는 결과에서 생략(추정치로 채우지 않음)."""
    results = _parallel_map(_yf_quote, list(INDEX_SYMBOLS.items()))
    return [r for r in results if r]


def fetch_indicators() -> list[dict]:
    """국채금리/유가/금/비트코인 — 실패한 항목은 결과에서 생략."""
    results = _parallel_map(_yf_quote, list(INDICATOR_SYMBOLS.items()))
    return [r for r in results if r]


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
    """
    KIWOOM_APP_KEY/SECRET 환경변수로 토큰을 발급받아 요청 헤더를 만든다. 실패 시 None.
    한 프로세스 안에서는 최초 1회만 토큰을 발급하고 재사용한다 (여러 함수가 같은 실행 중에
    이 함수를 불러도 토큰 발급 왕복이 한 번만 일어나도록).
    """
    global _HEADERS_CACHE
    if _HEADERS_CACHE is not None:
        return _HEADERS_CACHE

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

    _HEADERS_CACHE = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "secretkey": app_secret,
        "api-id": "usa20100",
    }
    return _HEADERS_CACHE


_HEADERS_CACHE: dict | None = None


def _kiwoom_quote_raw(headers: dict, stk_cd: str, stex_tp: str, _retries: int = 3) -> dict | None:
    """
    병렬로 쏘다 보면 usa20100의 초당 유량(5)을 가끔 넘겨서 1700 에러가 난다 — 그 경우만
    짧게 쉬었다 재시도한다 (다른 종류의 실패는 재시도하지 않고 바로 None).
    """
    for attempt in range(_retries):
        try:
            res = requests.post(
                KIWOOM_QUOTE_URL, headers=headers,
                json={"stex_tp": stex_tp, "stk_cd": stk_cd}, timeout=10,
            )
            data = res.json()
            if data.get("return_code") == 0:
                return data
            if "1700" in str(data.get("return_msg", "")) and attempt < _retries - 1:
                time.sleep(0.4 * (attempt + 1))
                continue
            print(f"  [종목] {stk_cd}({stex_tp}) 조회 실패: {data.get('return_msg')}")
            return None
        except Exception as e:
            print(f"  [종목] {stk_cd}({stex_tp}) 조회 실패: {e}")
            return None
    return None


def fetch_sectors() -> list[dict]:
    """
    SPDR 11개 섹터 ETF 등락률을 등락률 내림차순으로 반환.
    KIWOOM_APP_KEY/KIWOOM_APP_SECRET 환경변수가 없거나 API 실패 시 빈 리스트 반환
    (report.py 쪽에서 빈 리스트면 섹터도 웹서치로 채우도록 폴백).
    """
    headers = _kiwoom_headers()
    if headers is None:
        return []

    def _one(ticker_name: tuple[str, str]) -> dict | None:
        ticker, name = ticker_name
        data = _kiwoom_quote_raw(headers, ticker, "NY")
        if data is None:
            return None
        return {"ticker": ticker, "name": name, "changePct": float(data["flu_rt"])}

    results = [r for r in _parallel_map(_one, list(SECTOR_ETFS.items()), max_workers=KIWOOM_MAX_CONCURRENCY) if r]
    results.sort(key=lambda r: r["changePct"], reverse=True)
    return results


def fetch_stock_quotes(items: list[tuple[str, str]]) -> list[dict | None]:
    """
    여러 종목/ETF의 정확한 현재가·등락률·전일종가·연중최고가(일자)를 한 번에 병렬 조회한다.
    items: [(종목코드, 거래소구분), ...] — 거래소구분은 NASDAQ='ND', NYSE/NYSE Arca(ETF
    포함)='NY', AMEX='NA'.
    여러 종목을 확인해야 하면 이 함수에 리스트로 한꺼번에 넘길 것 — 종목마다 따로
    fetch_stock_quote()를 여러 번 호출하면 그만큼 느려진다.
    반환 리스트는 items와 같은 순서. 조회 실패한 항목은 None.
    각 dict에는 base_close(전일종가), oyr_high/oyr_high_date(연중최고가/그 날짜)도 들어있어
    "이 움직임이 정말 오늘 일인지"를 추가 호출 없이 바로 확인할 수 있다.
    """
    headers = _kiwoom_headers()
    if headers is None:
        return [None] * len(items)

    def _one(item: tuple[str, str]) -> dict | None:
        stk_cd, stex_tp = item
        data = _kiwoom_quote_raw(headers, stk_cd, stex_tp)
        if data is None:
            return None
        return {
            "ticker": stk_cd,
            "name": data.get("stk_nm"),
            "price": abs(float(data["cur_prc"])),
            "changePct": float(data["flu_rt"]),
            "base_close": abs(float(data["base_close_pric"])) if data.get("base_close_pric") else None,
            "oyr_high": abs(float(data["oyr_hgst"])) if data.get("oyr_hgst") else None,
            "oyr_high_date": data.get("oyr_hgst_dt"),
        }

    return _parallel_map(_one, items, max_workers=KIWOOM_MAX_CONCURRENCY)


def fetch_stock_quote(stk_cd: str, stex_tp: str = "NY") -> dict | None:
    """단일 종목 조회 편의 함수. 여러 종목이 필요하면 fetch_stock_quotes()를 쓸 것."""
    return fetch_stock_quotes([(stk_cd, stex_tp)])[0]


def build_data_block() -> str:
    """Claude 프롬프트에 그대로 삽입할 '확인된 수치' 텍스트 블록."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_indices = pool.submit(fetch_indices)
        f_indicators = pool.submit(fetch_indicators)
        f_sectors = pool.submit(fetch_sectors)
        indices, indicators, sectors = f_indices.result(), f_indicators.result(), f_sectors.result()

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
