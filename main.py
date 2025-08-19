# main.py

import os
import datetime
import requests
import schedule
import time
from bs4 import BeautifulSoup
from googletrans import Translator
from playwright.sync_api import sync_playwright
import openai
import csv, io, json  # ← 버핏지수 계산용(FRED CSV 파싱)

# (dotenv 사용 안 하면 그대로 두세요)
load_dotenv = None

# ── 환경 변수 ─────────────────────────────────────────────
TOKEN           = os.environ['TOKEN']
CHAT_ID         = os.environ['CHAT_ID']
EXCHANGE_KEY    = os.environ['EXCHANGEAPI']
TWELVEDATA_API  = os.environ["TWELVEDATA_API"]
TELEGRAM_URL    = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
today           = datetime.datetime.now().strftime('%Y년 %m월 %d일')
FRED_API_KEY   = os.getenv("FRED_API_KEY")


translator = Translator()

# ── 지표/시세 수집 ────────────────────────────────────────
def get_us_indices():
    url = "https://www.investing.com/indices/major-indices"
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers)
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
        except:
            out.append(f"{name}: 데이터 오류")
    return "\n".join(out)

def get_exchange_rates():
    res = requests.get(f"https://v6.exchangerate-api.com/v6/{EXCHANGE_KEY}/latest/USD").json()
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
            j = requests.get(f"https://api.twelvedata.com/quote?symbol={sym}&apikey={api_key}").json()
            p = float(j["close"])
            c = float(j["change"])
            pct = float(j["percent_change"])
            icon = "▲" if c > 0 else "▼" if c < 0 else "-"
            out.append(f"{name}: {p:.2f} {icon}{abs(c):.2f} ({pct:+.2f}%)")
        except:
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
            j = requests.get(f"https://api.twelvedata.com/quote?symbol={sym}&apikey={api_key}").json()
            p = float(j["close"])
            c = float(j["change"])
            pct = float(j["percent_change"])
            icon = "▲" if c > 0 else "▼" if c < 0 else "-"
            out.append(f"• {name}: ${p:.2f} {icon}{abs(c):.2f} ({pct:+.2f}%)")
        except:
            out.append(f"• {name}: 정보 없음")
    return "📌 주요 종목 시세:\n" + "\n".join(out)

def get_korean_stock_price(stock_code, name):
    try:
        url = f"https://finance.naver.com/item/sise.naver?code={stock_code}"
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(res.text, "html.parser")
        price = soup.select_one("strong#_nowVal").text.replace(",", "")
        change = soup.select_one("span#_change").text.strip().replace(",", "")
        rate = soup.select_one("span#_rate").text.strip()
        icon = "▲" if "-" not in change else "▼"
        return f"• {name}: {int(price):,}원 {icon}{change.replace('-', '')} ({rate})"
    except:
        return f"• {name}: 정보 없음"

def fetch_us_market_news_titles():
    try:
        url = "https://finance.yahoo.com/"
        soup = BeautifulSoup(requests.get(url, headers={"User-Agent": "Mozilla/5.0"}).text, "html.parser")
        arts = soup.select("li.js-stream-content a.js-content-viewer")[:3]
        return "\n".join(
            f"• {a.get_text(strip=True)}\n👉 {a['href'] if a['href'].startswith('http') else 'https://finance.yahoo.com' + a['href']}"
            for a in arts
        ) or "(기사 없음)"
    except:
        return "(뉴스 수집 실패)"

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
        anchors = page.query_selector_all(f"a[href*='/article/{press_id}/']")[:count]
        for a in anchors:
            img = a.query_selector("img")
            title = (
                img.get_attribute("alt").strip()
                if img and img.get_attribute("alt")
                else a.inner_text().strip()
            )
            href = a.get_attribute("href")
            if not href.startswith("http"):
                href = "https://n.news.naver.com" + href
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

# ── 버핏지수 (신규 / 견고 폴백 버전) ─────────────────────────────
import csv, io, time

def _fred_api_latest(series_id: str, api_key: str | None, tries: int = 2):
    """
    FRED 공식 JSON API로 최신 유효값(숫자)을 가져옴.
    키가 없으면 None 반환하여 상위 로직에서 CSV 폴백.
    """
    if not api_key:
        return None  # 키 없으면 이 경로는 건너뜀

    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",      # 최신부터
        "observation_start": "1990-01-01",
        "limit": 1000
    }
    last_exc = None
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(url, params=params, timeout=20)
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
    """
    fredgraph.csv?id=A,B 방식으로 여러 시리즈를 한번에 요청해
    각 시리즈의 최신 유효값을 반환.
    """
    base = "https://fred.stlouisfed.org/graph/fredgraph.csv"
    params = {"id": ",".join(series_ids)}
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "text/csv"}

    last_exc = None
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(base, params=params, headers=headers, timeout=20)
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
    """
    downloaddata CSV 단일 시리즈 폴백.
    """
    url = f"https://fred.stlouisfed.org/series/{series_id}/downloaddata/{series_id}.csv"
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "text/csv"}
    last_exc = None
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=20)
            r.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(r.text)))
            for row in reversed(rows):
                raw = (row.get(series_id) or "").strip()
                if raw and raw != ".":
                    return row.get("DATE"), float(raw)
            raise ValueError(f"No numeric observations for {series_id}")
        except Exception as e:
            last_exc = e
            time.sleep(0.5 * attempt)
    print(f"[WARN] downloaddata CSV fail {series_id}:", repr(last_exc))
    return None


def fred_latest_values_resilient(series_ids, api_key: str | None):
    """
    1) (있다면) FRED 공식 API로 각 시리즈 최신값
    2) 실패 시 fredgraph.csv로 일괄
    3) 그래도 실패 시 downloaddata 단일 CSV로 각각
    """
    result = {}

    # 1) API 경로 (옵션)
    if api_key:
        api_ok = True
        for sid in series_ids:
            val = _fred_api_latest(sid, api_key)
            if val is None:
                api_ok = False
                break
            result[sid] = val
        if api_ok:
            return result
        # 일부 실패 → 아래 CSV 폴백으로 전체 다시 시도

    # 2) fredgraph.csv
    combined = _fred_csv_latest_combined(series_ids)
    if combined:
        return combined

    # 3) downloaddata 단일
    for sid in series_ids:
        val = _fred_csv_latest_single(sid)
        if val is None:
            # 끝까지 실패
            return None
        result[sid] = val
    return result


def get_buffett_indicator():
    """
    버핏지수(근사) ≈ (Wilshire 5000 / 미국 명목 GDP) * 100
    - Wilshire 후보: 'WILL5000INDFC' → 실패시 'WILL5000IND' → 'WILL5000PR'
    - GDP: 'GDP' (분기, 십억달러, SAAR)
    """
    wilshire_candidates = ["WILL5000INDFC", "WILL5000IND", "WILL5000PR"]
    last_error = None

    for sid in wilshire_candidates:
        try:
            data = fred_latest_values_resilient([sid, "GDP"], FRED_API_KEY)
            if not data:
                raise RuntimeError("All FRED paths failed")
            (wil_date, wil_val) = data[sid]
            (gdp_date, gdp_val) = data["GDP"]

            ratio = (wil_val / gdp_val) * 100.0

            if ratio < 75:
                label = "저평가 구간"
            elif ratio < 90:
                label = "약간 저평가"
            elif ratio < 115:
                label = "적정 범위"
            elif ratio < 135:
                label = "약간 고평가"
            else:
                label = "고평가 경고"

            return (
                f"📐 버핏지수(근사): {ratio:.0f}% — {label}\n"
                f"    · Wilshire: {wil_val:,.0f} (기준 {wil_date})\n"
                f"    · GDP: {gdp_val:,.0f} (기준 {gdp_date})"
            )
        except Exception as e:
            last_error = e
            continue

    print("[WARN] Buffett indicator error:", repr(last_error))
    return "📐 버핏지수: 데이터 없음"



# ── 메시지 구성/전송 ──────────────────────────────────────
def build_message():
    return (
        f"📈 [{today}] 뉴스 요약 + 시장 지표\n\n"
        f"📊 미국 주요 지수:\n{get_us_indices()}\n\n"
        f"💱 환율:\n{get_exchange_rates()}\n\n"
        f"📉 미국 섹터별 지수 변화:\n{get_sector_etf_changes(TWELVEDATA_API)}\n\n"
        f"{get_buffett_indicator()}\n"         # ← 버핏지수 추가
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
        res = requests.post(
            TELEGRAM_URL,
            data={"chat_id": CHAT_ID, "text": msg}
        )
        print("✅ 응답 코드:", res.status_code, "| 📨", res.text)

# ── 스케줄러 ──────────────────────────────────────────────
schedule.every().day.at("07:00").do(send_to_telegram)
schedule.every().day.at("15:00").do(send_to_telegram)

if __name__ == "__main__":
    send_to_telegram()
