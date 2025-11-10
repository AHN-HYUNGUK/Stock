import os
import datetime
import requests
import schedule
import time
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import csv, io, json, re

# ───────────────────────── 공통 HTTP 설정 / 디버그 ─────────────────────────
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/csv;q=0.9,*/*;q=0.8",
}
HTTP_DEBUG = True  # 동작 확인 후 False로 내려도 됨

# 잘못된 시스템 프록시 무시
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(k, None)
os.environ.setdefault("NO_PROXY", "*")

S = requests.Session()
S.trust_env = False
S.headers.update(HTTP_HEADERS)
_DEF_PROXIES = {"http": None, "https": None}

def _mask_url(u: str) -> str:
    """로그에 노출될 URL에서 토큰/키를 ***로 마스킹."""
    try:
        u = re.sub(r'(api\.telegram\.org\/bot)[^\/]+', r'\1***', u)
        u = re.sub(r'(?i)(apikey|api_key|token|access_token)=[^&]+', r'\1=***', u)
    except Exception:
        pass
    return u

def http_get(url, *, params=None, timeout=20):
    if HTTP_DEBUG:
        try:
            from requests.models import PreparedRequest
            pr = PreparedRequest()
            pr.prepare_url(url, params)
            print(f"[HTTP GET] {_mask_url(pr.url)}")
        except Exception:
            print(f"[HTTP GET] {_mask_url(url)} {params if params else ''}")
    r = S.get(url, params=params, timeout=timeout, proxies=_DEF_PROXIES, allow_redirects=True)
    r.raise_for_status()
    return r


def http_post(url, data={}):
    """HTTP POST 요청을 보냅니다. (텔레그램 오류 무시 로직 추가)"""
    try:
        r = requests.post(url, data=data)
        
        # 🌟 텔레그램 API 오류 코드 (400)만 특별히 처리합니다.
        if "api.telegram.org" in url and r.status_code == 400:
            print(f"[WARN] 텔레그램 400 오류 발생: {r.status_code}")
            # 오류 메시지 출력 후, 정상 상태가 아니더라도 raise_for_status()를 건너뜁니다.
            # 텔레그램 API의 400 오류 메시지는 JSON으로 제공됩니다.
            try:
                error_details = r.json()
                print(f"[ERROR 400 DETAILS] {error_details}")
            except Exception:
                print(f"[ERROR 400 DETAILS] {r.text}")
            return r # 오류 객체를 반환하되, 예외 발생은 막습니다.

        r.raise_for_status() # 4xx, 5xx 에러가 발생하면 예외를 발생시킵니다.
        return r
    except requests.exceptions.RequestException as e:
        # 그 외 연결 오류나 다른 HTTP 오류는 여전히 처리합니다.
        print(f"[ERROR] HTTP POST 요청 실패: {e}")
        return None


# (dotenv 안 쓰면 그대로)
load_dotenv = None


# ── 환경 변수 ─────────────────────────────────────────────
TOKEN           = os.environ['TOKEN']
CHAT_IDS        = os.environ['CHAT_IDS'].split(",")
EXCHANGE_KEY    = os.environ['EXCHANGEAPI']
TWELVEDATA_API  = os.environ["TWELVEDATA_API"]
FRED_API_KEY    = os.environ["FRED_API_KEY"] 
ALPHAVANTAGE_KEY = os.environ["ALPHAVANTAGE_KEY"]
TELEGRAM_URL    = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
today           = datetime.datetime.now().strftime('%Y년 %m월 %d일')

# 🌟 수정된 코드: 각 항목의 앞뒤 공백을 제거하고, 빈 문자열인 경우 제외
CHAT_IDS = [
    _id.strip() 
    for _id in os.environ['CHAT_IDS'].split(",") 
    if _id.strip() # 공백 제거 후 내용이 있는 ID만 사용
]


# ── 지표/시세 수집 ────────────────────────────────────────
def get_us_indices():
    url = "https://www.investing.com/indices/major-indices"
    res = http_get(url)
    soup = BeautifulSoup(res.text, "html.parser")
    rows = soup.select("table tbody tr")[:3]
    out = []
    for r in rows:
        try:
            name = r.select_one("td:nth-child(2)").text.strip()
            now  = float(r.select_one("td:nth-child(3)").text.replace(",", ""))
            prev = float(r.select_one("td:nth-child(4)").text.replace(",", ""))
            diff = now - prev
            pct  = diff / prev * 100
            icon = "▲" if diff > 0 else "▼" if diff < 0 else "-"
            out.append(f"{name}: {now:,.2f} {icon}{abs(diff):,.2f} ({pct:+.2f}%)")
        except Exception:
            out.append(f"{name}: 데이터 오류")
    return "\n".join(out)


def get_korean_indices():
    """Alpha Vantage API를 사용하여 코스피와 코스닥 지수를 가져옵니다."""
    api_key = ALPHAVANTAGE_KEY
    # Alpha Vantage 심볼: KOSPI (KOSPI) 및 KOSDAQ (KOSDAQ)
    symbols = {"코스피": "KOSPI", "코스닥": "KOSDAQ"} 
    out = []
    
    for name, sym in symbols.items():
        try:
            # Alpha Vantage GLOBAL_QUOTE 엔드포인트 사용
            url = "https://www.alphavantage.co/query"
            params = {
                "function": "GLOBAL_QUOTE",
                "symbol": sym, # Alpha Vantage는 KOSPI/KOSDAQ 심볼을 그대로 사용함
                "apikey": api_key
            }
            j = http_get(url, params=params).json()
            
            data = j.get("Global Quote", {})
            if not data or not data.get("05. price"):
                raise RuntimeError("API에서 유효한 지수 데이터를 찾을 수 없음")

            p = float(data["05. price"])
            # Alpha Vantage는 변동률을 10. change percent에 퍼센트 문자열로 제공
            pct_change = float(data["10. change percent"].replace('%', ''))
            
            icon = "▲" if pct_change > 0 else "▼" if pct_change < 0 else "-"
            out.append(f"{name}: {p:,.2f} ({icon}{pct_change:+.2f}%)")
            
        except Exception as e:
            print(f"[ERROR] {name} API 수집 실패: {e}")
            out.append(f"{name}: 데이터 수집 오류 (Alpha Vantage API)")
            
    if not out or "데이터 수집 오류" in "".join(out):
        return "🇰🇷 한국 주요 지수: API 연결 또는 설정 오류"
        
    return "🇰🇷 한국 주요 지수:\n" + "\n".join(out)


def get_crypto_prices():
    """CoinGecko API를 사용하여 BTC/ETH 시세를 가져옵니다."""
    # CoinGecko의 공개 API (API 키 불필요)
    url = "https://api.coingecko.com/api/v3/simple/price"
    
    # ids: 코인 ID, vs_currencies: 비교 통화, include_24hr_change: 24시간 변동률 요청
    params = {
        "ids": "bitcoin,ethereum", 
        "vs_currencies": "usd", 
        "include_24hr_change": "true"
    }
    
    out = []
    try:
        j = http_get(url, params=params).json()
        
        # 비트코인
        btc_data = j.get("bitcoin", {})
        if btc_data:
            price = btc_data.get("usd", 0)
            pct_change = btc_data.get("usd_24h_change", 0)
            icon = "▲" if pct_change > 0 else "▼" if pct_change < 0 else "-"
            out.append(f"• ₿ 비트코인: ${price:,.0f} ({icon}{pct_change:+.2f}%)")
        
        # 이더리움
        eth_data = j.get("ethereum", {})
        if eth_data:
            price = eth_data.get("usd", 0)
            pct_change = eth_data.get("usd_24h_change", 0)
            icon = "▲" if pct_change > 0 else "▼" if pct_change < 0 else "-"
            out.append(f"• Ξ 이더리움: ${price:,.0f} ({icon}{pct_change:+.2f}%)")
            
    except Exception as e:
        print(f"[ERROR] 암호화폐 시세 수집 실패: {e}")
        out.append("• 비트코인/이더리움: 정보 없음 (CoinGecko API 오류)")
            
    return "🌐 주요 암호화폐 시세:\n" + "\n".join(out)


def get_exchange_rates():
    j = http_get(f"https://v6.exchangerate-api.com/v6/{EXCHANGE_KEY}/latest/USD").json()
    rates = j.get("conversion_rates", {})
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
            j = http_get("https://api.twelvedata.com/quote",
                         params={"symbol": sym, "apikey": api_key}).json()
            p = float(j["close"]); c = float(j["change"]); pct = float(j["percent_change"])
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
            j = http_get("https://api.twelvedata.com/quote",
                         params={"symbol": sym, "apikey": api_key}).json()
            p = float(j["close"]); c = float(j["change"]); pct = float(j["percent_change"])
            icon = "▲" if c > 0 else "▼" if c < 0 else "-"
            out.append(f"• {name}: ${p:.2f} {icon}{abs(c):.2f} ({pct:+.2f}%)")
        except Exception:
            out.append(f"• {name}: 정보 없음")
    return "📌 주요 종목 시세:\n" + "\n".join(out)

def get_korean_stock_price(stock_code, name):
    try:
        url = f"https://finance.naver.com/item/sise.naver?code={stock_code}"
        res = http_get(url)
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
        html = http_get("https://finance.yahoo.com/").text
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
            title = (img.get_attribute("alt").strip() if img and img.get_attribute("alt")
                     else a.inner_text().strip())
            href = (a.get_attribute("href") or "").strip()
            if href and not href.startswith("http"):
                href = "https://n.news.naver.com" + href
            if title:
                result += f"• {title}\n👉 {href}\n"
        browser.close()
    return result if anchors else f"• 현재 시점에 해당 언론사의 랭킹 뉴스가 없습니다.\n"

def get_fear_greed_index():
    try:
        j = http_get("https://api.alternative.me/fng/", params={"limit": 1}).json()
        data = j["data"][0]
        value = data["value"]; label = data["value_classification"]
        return f"📌 공포·탐욕 지수 (코인 Crypto 기준): {value}점 ({label})"
    except Exception as e:
        print("[ERROR] 공포·탐욕 지수 예외:", e)
        return "📌 공포·탐욕 지수: 가져오기 실패"


# ── 메시지/전송 ──────────────────────────────────────────

def build_message():
    return (
        f"📈 [{today}] 뉴스 요약 + 시장 지표\n\n"
        f"📊 미국 주요 지수:\n{get_us_indices()}\n\n"
        f"🇰🇷 한국 주요 지수:\n{get_korean_indices()}\n\n"
        f"💱 환율:\n{get_exchange_rates()}\n\n"
        f"{get_crypto_prices()}\n\n"
        f"📉 미국 섹터별 지수 변화:\n{get_sector_etf_changes(TWELVEDATA_API)}\n\n"
        f"{get_fear_greed_index()}\n\n"
        f"{get_stock_prices(TWELVEDATA_API)}" # 세계 뉴스 (074) 및 버핏지수 제거 완료
    )


def send_to_telegram():
    part1 = build_message()
    # 네이버 랭킹 뉴스 (215)는 그대로 유지됩니다.
    part2 = fetch_media_press_ranking_playwright("215", 10)

    for chat_id in CHAT_IDS:  # ✅ 여러 명에게 순차 전송
        for msg in (part1, part2):
            if len(msg) > 4000:
                msg = msg[:3990] + "\n(※ 일부 생략됨)"
            res = http_post(TELEGRAM_URL, data={"chat_id": chat_id.strip(), "text": msg})
            print(f"✅ {chat_id} 전송 완료 | 코드: {res.status_code}")



# ── 스케줄러 ──────────────────────────────────────────────
schedule.every().day.at("07:00").do(send_to_telegram)
schedule.every().day.at("15:00").do(send_to_telegram)

if __name__ == "__main__":
    send_to_telegram()
