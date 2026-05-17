import yfinance as yf
import requests
import json
from datetime import datetime
import pytz
import time

SYMBOLS = {
    "USDKRW=X": {"name": "원/달러 환율", "fx": True},
    "^DJI":     {"name": "다우존스"},
    "^GSPC":    {"name": "S&P 500"},
    "^IXIC":    {"name": "나스닥"},
    "^SOX":     {"name": "필라델피아 반도체"},
    "ES=F":     {"name": "E-mini S&P 500"},
    "YM=F":     {"name": "E-mini Dow Jones"},
    "NQ=F":     {"name": "E-mini Nasdaq 100"},
}


def _parse_naver_index(d, label):
    """네이버 /api/index/{code}/basic 응답에서 price/change/changePct 추출"""
    try:
        price = float(str(d.get('closePrice', '')).replace(',', ''))
        chg   = float(str(d.get('compareToPreviousClosePrice', '0')).replace(',', '').replace('+', ''))
        pct   = float(str(d.get('fluctuationsRatio', '0')).replace(',', '').replace('+', ''))
        direction = d.get('compareToPreviousPrice', {})
        if isinstance(direction, dict):
            code = direction.get('code', '')
        else:
            code = str(direction)
        if 'FALL' in code or 'DOWN' in code or code == '2':
            chg, pct = -abs(chg), -abs(pct)
        print(f"  ✓ {label}: {price} ({chg:+.2f}, {pct:+.2f}%)")
        return price, chg, pct
    except Exception as e:
        print(f"  ✗ {label} 파싱 오류: {e}")
        return None, None, None


def fetch_naver_night_futures():
    """야간KP선물/KQ선물 — 네이버 금융 /api/index 사용"""
    hdrs = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'ko-KR,ko;q=0.9',
        'Referer': 'https://m.stock.naver.com/',
    }

    nf_results = {}

    # ── 야간KP선물 (FUT = 코스피200 야간선물지수) ──
    try:
        r = requests.get("https://m.stock.naver.com/api/index/FUT/basic", headers=hdrs, timeout=8)
        if r.ok:
            price, chg, pct = _parse_naver_index(r.json(), '야간KP선물')
            if price:
                nf_results['KP_night'] = {'name': '야간KP선물', 'price': price, 'change': chg, 'changePct': pct}
        else:
            print(f"  ✗ 야간KP선물: HTTP {r.status_code}")
    except Exception as e:
        print(f"  ✗ 야간KP선물: {e}")

    # ── 야간KQ선물 ──
    kq_found = False
    for kq_code in ['KQFUT', 'KQF']:
        try:
            r = requests.get(f"https://m.stock.naver.com/api/index/{kq_code}/basic", headers=hdrs, timeout=5)
            if r.ok:
                price, chg, pct = _parse_naver_index(r.json(), '야간KQ선물')
                if price:
                    nf_results['KQ_night'] = {'name': '야간KQ선물', 'price': price, 'change': chg, 'changePct': pct}
                    kq_found = True
                    break
        except Exception:
            pass

    if not kq_found:
        try:
            r = requests.get("https://m.stock.naver.com/api/index/KQ150/basic", headers=hdrs, timeout=5)
            if r.ok:
                price, chg, pct = _parse_naver_index(r.json(), '야간KQ(코스닥150)')
                if price:
                    nf_results['KQ_night'] = {'name': '야간KQ(코스닥150)', 'price': price, 'change': chg, 'changePct': pct}
                    print(f"  – KQ선물 없음, 코스닥150 지수로 표시")
        except Exception as e:
            print(f"  ✗ 야간KQ: {e}")

    for key, label in [('KP_night', '야간KP선물'), ('KQ_night', '야간KQ선물')]:
        if key not in nf_results:
            nf_results[key] = {'name': label, 'price': None, 'change': None, 'changePct': None}
            print(f"  – {label}: 데이터 없음")

    return nf_results


result = {}

for sym, meta in SYMBOLS.items():
    try:
        fi = yf.Ticker(sym).fast_info
        price = fi.last_price
        prev  = fi.previous_close
        if price is not None and prev:
            chg     = round(float(price - prev), 4)
            chg_pct = round(float(chg / prev * 100), 4)
            price   = round(float(price), 4)
        else:
            chg = chg_pct = None
            price = round(float(price), 4) if price else None
        result[sym] = {**meta, "price": price, "change": chg, "changePct": chg_pct}
        print(f"  ✓ {sym}: {price}")
    except Exception as e:
        print(f"  ✗ {sym}: {e}")
        result[sym] = {**meta, "price": None, "change": None, "changePct": None}
    time.sleep(0.3)

print("\n야간선물 수집 중...")
result.update(fetch_naver_night_futures())

kst = pytz.timezone("Asia/Seoul")
output = {
    "updated": datetime.now(kst).strftime("%m/%d %H:%M"),
    "data": result
}
with open("market_data.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\n완료: {output['updated']}")
