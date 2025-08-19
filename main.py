# main.py

import os
import datetime
import requests
import schedule
import time
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from googletrans import Translator
import openai  # (미사용이면 제거 가능)
import csv, io, json  # FRED/시세 CSV/JSON 파싱용

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/csv;q=0.9,*/*;q=0.8",
}

# (dotenv 사용 안 하면 그대로 두세요)
load_dotenv = None

# ── 환경 변수 ─────────────────────────────────────────────
TOKEN           = os.environ['TOKEN']
CHAT_ID         = os.environ['CHAT_ID']
EXCHANGE_KEY    = os.environ['EXCHANGEAPI']
TWELVEDATA_API  = os.environ["TWELVEDATA_API"]
TELEGRAM_URL    = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
today           = datetime.datetime.now().strftime('%Y년 %m월 %d일')
FRED_API_KEY    = os.getenv("FRED_API_KEY")  # 없어도 CSV 폴백 경로로 동작

translator = Translator()

# ── 지표/시세 수집 ────────────────────────────────────────
def get_us_indices():
    url = "https://www.investing.com/indices/major-indices"
    res = requests.get(url, headers=HTTP_HEADERS, timeout=20)
    soup = BeautifulSoup(res.text, "html.parser")
    rows = soup.select("table tbody tr")[:3]
    out = []
    for r in rows:
        try:
            name = r.select_one("td:nth-child(2)").text.strip()
            now  = float(r.select_one("td:nth-child(3)").text.replace(",", ""))
            prev = float(r.select_one("td:nth-child(4)").text.replace(",", ""))
            diff = now - prev
            pct  = diff / prev * 100
            icon = "▲" if diff > 0 else "▼" if diff < 0 else "-"
            out.append(f"{name}: {now:,.2f} {icon}{abs(diff):,.2f} ({pct:+.2f}%)")
        except Exception:
            out.append(f"{name}: 데이터 오류")
    return "\n".join(out)

def get_exchange_rates():
    res = requests.get(f"https://v6.exchangerate-api.com/v6/{EXCHANGE_KEY}/latest/USD", timeout=20).json()
    rates = res.get("conversion_rates", {})
    return (
        f"USD: 1.00 기준\n"
        f"KRW: {rates.get('KRW', 0):.2f}\n"
        f"JPY (100엔): {rates.get('JPY', 0) * 100:.2f}\n"
        f"EUR: {rates.get('EUR', 0):.2f}\n"
        f"CNY: {rates.get('CNY', 0):.2f}"
    )

def get_sector_etf_changes(api_key):
    etfs = {"💻 기술": "XLK", "🏦 금융": "XLF", "💊 헬스케어": "XLV", "⚡ 에너지": "XLE", "🛒 소비재": "XLY"}
    out = []
    for name, sym in etfs.items():
        try:
            j = requests.get(f"https://api.twelvedata.com/quote?symbol={sym}&apikey={api_key}", timeout=20).json()
            p = float(j["close"])
            c = float(j["change"])
            pct = float(j["percent_change"])
            icon = "▲" if c > 0 else "▼" if c < 0 else "-"
            out.append(f"{name}: {p:.2f} {icon}{abs(c):.2f} ({pct:+.2f}%)")
        except Exception:
            out.append(f"{name}: 정보 없음")
    return "\n".join(out)

def get_stock_prices(api_key):
    symbols = {
        "Tesla (TSLA)": "TSLA",
        "Nvidia (NVDA)": "NVDA",
        "Apple (AAPL)": "AAPL",
        "Microsoft (MSFT)": "MSFT",
        "Amazon (AMZN)": "AMZN",
        "Meta (META)": "META",
        "Berkshire Hathaway (BRK.B)": "BRK.B"
    }
    out = []
    for name, sym in symbols.items():
        try:
            j = requests.get(f"https://api.twelvedata.com/quote?symbol={sym}&apikey={api_key}", timeout=20).json()
            p = float(j["close"])
            c = float(j["change"])
            pct = float(j["percent_change"])
            icon = "▲" if c > 0 else "▼" if c < 0 else "-"
            out.append(f"• {name}: ${p:.2f} {icon}{abs(c):.2f} ({pct:+.2f}%)")
        except Exception:
            out.append(f"• {name}: 정보 없음")
    return "📌 주요 종목 시세:\n" + "\n".join(out)

def fetch_us_market_news_titles():
    try:
        url = "https://finance.yahoo.com/"
        html = requests.get(url, headers=HTTP_HEADERS, timeout=20).text
        soup = BeautifulSoup(html, "html.parser")
        arts = soup.select("li.js-stream-content a.js-content-viewer")[:3]
        return "\n".join(
            f"• {a.get_text(strip=True)}\n👉 {a['href'] if a['href'].startswith('http') else 'https://finance.yahoo.com' + a['href']}"
            for a in arts
        ) or "(기사 없음)"
    except Exception as e:
        print("[WARN] yahoo fetch failed:", repr(e))
        return "(뉴스 수집 실패)"

# ── 네이버 랭킹 뉴스 (Playwright, 타임아웃 폴백) ───────────────
def fetch_media_press_ranking_playwright(press_id="215", count=10):
    url = f"https://media.naver.com/press/{press_id}/ranking"
    result = f"📌 언론사 {press_id} 랭킹 뉴스 TOP {count}\n"
    anchors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page()
        page.goto(url)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)
        try:
            page.wait_for_selector(f"a[href*='/article/{press_id}/']", timeout=10000)
            anchors = page.query_selector_all(f"a[href*='/article/{press_id}/']")[:count]
        except PlaywrightTimeoutError:
            anchors = page.query_selector_all("ul.list_ranking li a")[:count]

        for a in anchors:
            img = a.query_selector("img")
            title = (
                img.get_attribute("alt").strip()
                if img and img.get_attribute("alt")
                else a.inner_text().strip()
            )
            href = (a.get_attribute("href") or "").strip()
            if href and not href.startswith("http"):
                href = "https://n.news.naver.com" + href
            if title:
                result += f"• {title}\n👉 {href}\n"

        browser.close()

    return result if anchors else f"• 현재 시점에 해당 언론사의 랭킹 뉴스가 없습니다.\n"

def get_fear_greed_index():
    try:
        url = "https://api.alternative.me/fng/?limit=1"
        res = requests.get(url, timeout=10).json()
        data = res["data"][0]
        value = data["value"]
        label = data["value_classification"]
        return f"📌 공포·탐욕 지수 (코인 Crypto 기준): {value}점 ({label})"
    except Exception as e:
        print("[ERROR] 공포·탐욕 지수 예외:", e)
        return "📌 공포·탐욕 지수: 가져오기 실패"

# ── FRED 헬퍼 (API → fredgraph.csv → downloaddata CSV 폴백) ─────
def _fred_api_latest(series_id: str, api_key: str | None, tries: int = 2):
    if not api_key:
        return None
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "observation_start": "1990-01-01",
        "limit": 1000,
    }
    last_exc = None
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(url, params=params, headers=HTTP_HEADERS, timeout=20)
            r.raise_for_status()
            j = r.json()
            for obs in j.get("observations", []):
                raw = (obs.get("value") or "").strip()
                if raw and raw != ".":
                    return obs.get("date"), float(raw)
            raise ValueError(f"[FRED API] No numeric observations for {series_id}")
        except Exception as e:
            last_exc = e
            time.sleep(0.5 * attempt)
    print(f"[WARN] FRED API fail {series_id}:", repr(last_exc))
    return None

def _fred_csv_latest_combined(series_ids, tries: int = 2):
    base = "https://fred.stlouisfed.org/graph/fredgraph.csv"
    params = {"id": ",".join(series_ids)}
    last_exc = None
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(base, params=params, headers=HTTP_HEADERS, timeout=20)
            r.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(r.text)))
            latest = {sid: (None, None) for sid in series_ids}
            for row in reversed(rows):
                for sid in series_ids:
                    if latest[sid][1] is not None:
                        continue
                    raw = (row.get(sid) or "").strip()
                    if raw and raw != ".":
                        latest[sid] = (row.get("DATE"), float(raw))
                if all(v[1] is not None for v in latest.values()):
                    break
            missing = [sid for sid, v in latest.items() if v[1] is None]
            if missing:
                raise ValueError(f"No numeric observations for series: {missing}")
            return latest
        except Exception as e:
            last_exc = e
            time.sleep(0.5 * attempt)
    print("[WARN] fredgraph.csv fail:", repr(last_exc))
    return None

def _fred_csv_latest_single(series_id: str, tries: int = 2):
    url = f"https://fred.stlouisfed.org/series/{series_id}/downloaddata/{series_id}.csv"
    last_exc = None
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(url, headers=HTTP_HEADERS, timeout=20)
            r.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(r.text)))
            for row in reversed(rows):
                raw = (row.get("VALUE") or "").strip()
                if raw and raw != ".":
                    return row.get("DATE"), float(raw)
            raise ValueError(f"No numeric observations for {series_id}")
        except Exception as e:
            last_exc = e
            time.sleep(0.5 * attempt)
    print(f"[WARN] downloaddata CSV fail {series_id}:", repr(last_exc))
    return None

def fred_latest_one(series_id: str, api_key: str | None):
    val = _fred_api_latest(series_id, api_key)
    if val:
        return val
    combo = _fred_csv_latest_combined([series_id])
    if combo and combo.get(series_id) and combo[series_id][1] is not None:
        return combo[series_id]
    return _fred_csv_latest_single(series_id)

# ── 버핏지수 (시장×GDP 보정) 헬퍼 ───────────────────────────────
def _classify_buffett(pct: float) -> str:
    if pct < 75:
        return "저평가 구간"
    elif pct < 90:
        return "약간 저평가"
    elif pct < 115:
        return "적정 범위"
    elif pct < 135:
        return "약간 고평가"
    else:
        return "고평가 경고"

def fred_value_for_year(series_id: str, year: int):
    url = f"https://fred.stlouisfed.org/series/{series_id}/downloaddata/{series_id}.csv"
    r = requests.get(url, headers=HTTP_HEADERS, timeout=20)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    for row in reversed(rows):
        d = (row.get("DATE") or "")
        v = (row.get("VALUE") or "").strip()
        if d.startswith(f"{year}-") and v and v != ".":
            return d, float(v)
    return None

def _td_get_json(endpoint: str, params: dict, tries: int = 3, sleep_sec: float = 0.8):
    url = f"https://api.twelvedata.com/{endpoint}"
    last = None
    for _ in range(tries):
        try:
            r = requests.get(url, params=params, headers=HTTP_HEADERS, timeout=20)
            r.raise_for_status()
            j = r.json()
            if isinstance(j, dict) and j.get("status") == "error":
                last = j.get("message")
                time.sleep(sleep_sec)
                continue
            return j
        except Exception as e:
            last = repr(e)
            time.sleep(sleep_sec)
    raise RuntimeError(f"TwelveData {endpoint} failed: {last}")

def get_vti_latest_and_ref_from_twelvedata(year: int, api_key: str):
    # 최신가
    q = _td_get_json("quote", {"symbol": "VTI", "apikey": api_key})
    latest_str = (q.get("close") or q.get("previous_close") or q.get("price"))
    if latest_str is None:
        raise RuntimeError(f"VTI quote has no price fields: {q}")
    latest = float(latest_str)

    def _fetch_ref(start_date: str, end_date: str):
        ts = _td_get_json("time_series", {
            "symbol": "VTI",
            "interval": "1day",
            "start_date": start_date,
            "end_date": end_date,
            "order": "asc",
            "apikey": api_key,
        })
        vals = ts.get("values") or []
        if not vals:
            return None
        return float(vals[-1].get("close"))

    ref = _fetch_ref(f"{year}-12-20", f"{year+1}-01-10")
    if ref is None:
        ref = _fetch_ref(f"{year}-12-01", f"{year+1}-02-15")
    if ref is None:
        ref = _fetch_ref(f"{year}-11-15", f"{year+1}-03-15")

    if ref is None:
        raise RuntimeError("VTI reference price not found in all windows")

    print(f"[BUFFETT] VTI latest={latest}, base({year} year-end)={ref}")
    return latest, ref, "VTI(TwelveData)"

def get_spy_latest_and_ref_from_stooq(base_year: int):
    """
    Stooq CSV로 SPY 일별 시세 (무인증).
    https://stooq.com/q/d/l/?s=spy.us&i=d
    """
    url = "https://stooq.com/q/d/l/"
    params = {"s": "spy.us", "i": "d"}
    r = requests.get(url, params=params, headers=HTTP_HEADERS, timeout=20)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    if not rows or "Date" not in rows[0] or "Close" not in rows[0]:
        raise RuntimeError("Unexpected Stooq CSV schema")

    latest_close = None
    base_close = None
    base_start = datetime.date(base_year, 12, 1)
    base_end   = datetime.date(base_year + 1, 1, 15)

    for row in rows:
        ds = row.get("Date")
        cs = row.get("Close")
        if not ds or not cs or cs in ("", "null", "NaN"):
            continue
        d = datetime.date.fromisoformat(ds)
        c = float(cs)
        latest_close = c  # 마지막 유효값이 최신
        if base_start <= d <= base_end:
            base_close = c  # 범위 내 마지막 거래일 종가로 계속 갱신

    if base_close is None:
        # 폴백: 기준연도 12/31 이전 마지막 거래일 종가
        for row in reversed(rows):
            ds = row.get("Date")
            cs = row.get("Close")
            if not ds or not cs or cs in ("", "null", "NaN"):
                continue
            d = datetime.date.fromisoformat(ds)
            if d <= datetime.date(base_year, 12, 31):
                base_close = float(cs)
                break

    if latest_close is None or base_close is None:
        raise RuntimeError("Stooq SPY parse failed")

    print(f"[BUFFETT] SPY(Stooq) latest={latest_close}, base={base_close}")
    return latest_close, base_close, "SPY(Stooq)"

def get_spy_latest_and_ref_from_yahoo(base_year: int):
    """
    Yahoo Finance CSV로 SPY 일별 시세(무인증).
    - 최신값: 마지막 유효 행의 종가
    - 기준값: [base_year-12-01, base_year+1-01-15] 범위의 마지막 거래일 종가
    """
    period1 = int(datetime.datetime(base_year, 12, 1, 0, 0).timestamp())
    period2 = int(time.time())
    url = "https://query1.finance.yahoo.com/v7/finance/download/SPY"
    params = {
        "period1": period1,
        "period2": period2,
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }
    r = requests.get(url, params=params, headers=HTTP_HEADERS, timeout=20)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    if not rows or "Date" not in rows[0] or "Close" not in rows[0]:
        raise RuntimeError("Unexpected Yahoo CSV schema")

    latest_close = None
    base_close = None
    base_start = datetime.date(base_year, 12, 1)
    base_end   = datetime.date(base_year + 1, 1, 15)

    for row in rows:
        ds = row.get("Date")
        cs = row.get("Close")
        if not ds or not cs or cs in ("", "null", "NaN"):
            continue
        d = datetime.date.fromisoformat(ds)
        c = float(cs)
        latest_close = c
        if base_start <= d <= base_end:
            base_close = c

    if base_close is None:
        for row in reversed(rows):
            ds = row.get("Date")
            cs = row.get("Close")
            if not ds or not cs or cs in ("", "null", "NaN"):
                continue
            d = datetime.date.fromisoformat(ds)
            if d <= datetime.date(base_year, 12, 31):
                base_close = float(cs)
                break

    if latest_close is None or base_close is None:
        raise RuntimeError("Yahoo SPY parse failed")

    print(f"[BUFFETT] SPY(Yahoo) latest={latest_close}, base={base_close}")
    return latest_close, base_close, "SPY(Yahoo)"

# ── 버핏지수 (현재 추정 + 연간 확정) ────────────────────────────
def get_buffett_indicator():
    """
    📐 버핏지수(현재 추정 + 연간 확정)
    - 확정치: FRED(World Bank 변환) DDDM01USA156NWDB (%)
    - 현재 추정(nowcast):
        nowcast ≈ base_pct × (Market_latest / Market_base) / (GDP_latest / GDP_base)
    """
    api_key = FRED_API_KEY

    # 1) 연간 확정치 (마지막 공개 연도)
    base = fred_latest_one("DDDM01USA156NWDB", api_key)
    if not base:
        print("[WARN] Buffett (DDDM01USA156NWDB) fetch failed")
        return "📐 버핏지수: 데이터 없음"
    base_date, base_pct = base
    base_year = int(base_date[:4]) if base_date else None
    base_line = f"    · 연간 확정치: {base_pct:.0f}% (기준연도 {base_year})"

    # 2) nowcast 계산 시도
    try:
        # GDP 최신/기준
        gdp_latest = fred_latest_one("GDP", api_key)
        gdp_base   = fred_value_for_year("GDP", base_year)
        if not gdp_latest or not gdp_base:
            raise RuntimeError("GDP 데이터 부족")
        _, gdp_latest_val = gdp_latest
        _, gdp_base_val   = gdp_base

        # 시장지표 최신/기준: SPY(Stooq) → SPY(Yahoo) → VTI(TwelveData)
        try:
            mkt_latest, mkt_base, mkt_name = get_spy_latest_and_ref_from_stooq(base_year)
        except Exception as ee1:
            print("[WARN] SPY(Stooq) path failed:", repr(ee1))
            try:
                mkt_latest, mkt_base, mkt_name = get_spy_latest_and_ref_from_yahoo(base_year)
            except Exception as ee2:
                print("[WARN] SPY(Yahoo) path failed:", repr(ee2))
                mkt_latest, mkt_base, mkt_name = get_vti_latest_and_ref_from_twelvedata(
                    base_year, TWELVEDATA_API
                )

        mkt_factor = mkt_latest / mkt_base
        gdp_factor = gdp_latest_val / gdp_base_val
        nowcast_pct = base_pct * (mkt_factor / gdp_factor)

        print(f"[BUFFETT] GDP latest/base = {gdp_latest_val}/{gdp_base_val} → {gdp_factor:.2f}")
        print(f"[BUFFETT] Market({mkt_name}) latest/base = {mkt_latest}/{mkt_base} → {mkt_factor:.2f}")
        print(f"[BUFFETT] Nowcast = {base_pct:.1f}% × {mkt_factor:.2f}/{gdp_factor:.2f} = {nowcast_pct:.1f}%")

        head = f"📐 버핏지수(현재 추정): {nowcast_pct:.0f}% — {_classify_buffett(nowcast_pct)}"
        tail = (
            f"{base_line}\n"
            f"    · 보정계수: {mkt_name}×{mkt_factor:.2f} / GDP×{gdp_factor:.2f}"
        )
        return f"{head}\n{tail}"

    except Exception as e:
        print("[WARN] Buffett nowcast failed:", repr(e))
        # 추정 실패 시 확정치만 노출
        return f"📐 버핏지수(연간 확정치): {base_pct:.0f}% — {_classify_buffett(base_pct)}\n{base_line}"

# ── 메시지 구성/전송 ──────────────────────────────────────
def build_message():
    return (
        f"📈 [{today}] 뉴스 요약 + 시장 지표\n\n"
        f"📊 미국 주요 지수:\n{get_us_indices()}\n\n"
        f"💱 환율:\n{get_exchange_rates()}\n\n"
        f"📉 미국 섹터별 지수 변화:\n{get_sector_etf_changes(TWELVEDATA_API)}\n\n"
        f"{get_buffett_indicator()}\n"
        f"{get_fear_greed_index()}\n\n"
        f"{get_stock_prices(TWELVEDATA_API)}\n\n"
        f"📰 세계 언론사 랭킹 뉴스 (press 074):\n{fetch_media_press_ranking_playwright('074', 3)}"
    )

def send_to_telegram():
    part1 = build_message()
    part2 = fetch_media_press_ranking_playwright("215", 10)

    for msg in [part1, part2]:
        if len(msg) > 4000:
            msg = msg[:3990] + "\n(※ 일부 생략됨)"
        res = requests.post(TELEGRAM_URL, data={"chat_id": CHAT_ID, "text": msg})
        print("✅ 응답 코드:", res.status_code, "| 📨", res.text)

# ── 스케줄러 ──────────────────────────────────────────────
schedule.every().day.at("07:00").do(send_to_telegram)
schedule.every().day.at("15:00").do(send_to_telegram)

if __name__ == "__main__":
    send_to_telegram()
