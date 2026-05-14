import yfinance as yf
import json
from datetime import datetime
import pytz
import time

SYMBOLS = {
    "USDKRW=X": {"name": "원/달러 환율", "fx": True},
    "^KS200":   {"name": "KOSPI 200",   "badge": "야간참고"},
    "^KQ11":    {"name": "코스닥",       "badge": "야간참고"},
    "^DJI":     {"name": "다우존스"},
    "^GSPC":    {"name": "S&P 500"},
    "^IXIC":    {"name": "나스닥"},
    "^SOX":     {"name": "필라델피아 반도체"},
    "ES=F":     {"name": "E-mini S&P 500"},
    "YM=F":     {"name": "E-mini Dow Jones"},
    "NQ=F":     {"name": "E-mini Nasdaq 100"},
}

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

kst = pytz.timezone("Asia/Seoul")
output = {
    "updated": datetime.now(kst).strftime("%m/%d %H:%M"),
    "data": result
}
with open("market_data.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\n완료: {output['updated']}")
