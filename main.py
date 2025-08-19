# main.py

import os
import datetime
import requests
import schedule
import time
from bs4 import BeautifulSoup
from googletrans import Translator
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import openai  # (미사용이면 제거해도 무방)
import csv, io, json  # FRED CSV/JSON 파싱용

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
FRED_API_KEY    = os.getenv("FRED_API_KEY")  # 없어도 동작(CSV 폴백)

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

def get_korean_stock_price(stock_code, name):
    try:
        url = f"https://finance.naver.com/item/sise.naver?code={stock_code}"
        res = requests.get(url, headers=HTTP_HEADERS, timeout=20)
        soup = BeautifulSoup(res.text, "html.parser")
        price = soup.select_one("strong#_nowVal").text.replace(",", "")
        change = soup.select_one("span#_change").text.strip().replace(",", "")
        rate = soup.select_one("span#_rate").text.strip()
        icon = "▲" if "-" not in change else "▼"
        return f"• {name}: {int(price):,}원 {icon}{change.replace('-', '')} ({rate})"
    except Exception:
        return f"• {name}: 정보 없음"

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
    """FRED 공식 JSON API로 최신 유효값(숫자) 가져오기."""
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
    """fredgraph.csv?id=A,B… 일괄 요청."""
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
    """downloaddata 단일 시리즈 폴백. (헤더는 DATE, VALUE)"""
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
    """한 개 시리즈를 ①API → ②fredgraph.csv → ③downloaddata 순으로 시도."""
    val = _fred_api_latest(series_id, api_key)
    if val:
        return val
    combo = _fred_csv_latest_combined([series_id])
    if combo and combo.get(series_id) and combo[series_id][1] is not None:
        return combo[series_id]
    return _fred_csv_latest_single(series_id)

# ── 버핏지수 (시총/GDP 직접 시리즈 사용) ────────────────────────
def _classify_buffett(ratio: float) -> str:
    if ratio < 75:
        return "저평가 구간"
    elif ratio < 90:
        return "약간 저평가"
    elif ratio < 115:
        return "적정 범위"
    elif ratio < 135:
        return "약간 고평가"
    else:
        return "고평가 경고"

def get_buffett_indicator():
    """
    버핏지수(근사) = 시총/GDP.
    FRED(World Bank 변환) 시리즈 'DDDM01USA156NWDB'는 이미 퍼센트(%)로 제공되는 연간 데이터.
    """
    api_key = FRED_API_KEY  # 없어도 CSV 폴백으로 동작
    val = fred_latest_one("DDDM01USA156NWDB", api_key)
    if not val:
        print("[WARN] Buffett (DDDM01USA156NWDB) fetch failed")
        return "📐 버핏지수: 데이터 없음"

    date, pct = val  # pct는 이미 퍼센트 값
    label = _classify_buffett(pct)
    year = date[:4] if date else "N/A"
    return (
        f"📐 버핏지수(시총/GDP, 연간): {pct:.0f}% — {label}\n"
        f"    · 기준연도: {year} (FRED: DDDM01USA156NWDB)"
    )

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
