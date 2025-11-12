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
HTTP_DEBUG = True  # 동작 확인 후 False로 내려도 됨

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
            try:
                error_details = r.json()
                print(f"[ERROR 400 DETAILS] {error_details}")
            except Exception:
                print(f"[ERROR 400 DETAILS] {r.text}")
            return r

        r.raise_for_status() 
        return r
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] HTTP POST 요청 실패: {e}")
        return None


# (dotenv 안 쓰면 그대로)
load_dotenv = None


# ── 환경 변수 ─────────────────────────────────────────────
TOKEN             = os.environ['TOKEN']
CHAT_IDS          = os.environ['CHAT_IDS'].split(",")
EXCHANGE_KEY      = os.environ['EXCHANGEAPI']
TWELVEDATA_API    = os.environ["TWELVEDATA_API"]
FRED_API_KEY      = os.environ["FRED_API_KEY"]  
ALPHAVANTAGE_KEY  = os.environ["ALPHAVANTAGE_KEY"] # (사용하지 않음, 호환성 유지)
TELEGRAM_URL      = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
today             = datetime.datetime.now().strftime('%Y년 %m월 %d일')

CHAT_IDS = [
    _id.strip() 
    for _id in os.environ['CHAT_IDS'].split(",") 
    if _id.strip() # 공백 제거 후 내용이 있는 ID만 사용
]


# ── 지표/시세 수집 ────────────────────────────────────────

def get_us_indices():
    # Investing.com 스크래핑 로직 유지
    url = "https://www.investing.com/indices/major-indices"
    try:
        res = http_get(url)
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
    except Exception as e:
        print(f"[ERROR] 미국 지수 수집 실패: {e}")
        return "Dow Jones: 데이터 수집 오류\nS&P 500 derived: 데이터 수집 오류\nNasdaq: 데이터 수집 오류"


def get_korean_indices_twelve(api_key):
    """🌟 수정: TwelveData API를 사용하여 코스피와 코스닥 지수를 가져옵니다."""
    symbols = {"코스피": "KOSPI", "코스닥": "KOSDAQ"} 
    out = []
    
    for name, sym in symbols.items():
        try:
            j = http_get("https://api.twelvedata.com/quote",
                         params={"symbol": sym, "apikey": api_key}).json()
            
            # API 응답 오류 처리
            if j.get('status') == 'error':
                 raise RuntimeError(f"TwelveData Error: {j.get('message', '알 수 없는 오류')}")

            p = float(j["close"])
            pct = float(j["percent_change"])
            icon = "▲" if pct > 0 else "▼" if pct < 0 else "-"
            out.append(f"{name}: {p:,.2f} ({icon}{pct:+.2f}%)")
            
        except Exception as e:
            print(f"[ERROR] {name} API 수집 실패: {e}")
            out.append(f"{name}: 데이터 수집 오류 (TwelveData API)")
            
    if not out or "데이터 수집 오류" in "".join(out):
        return "API 연결 또는 설정 오류"
        
    return "\n".join(out)


def get_crypto_prices():
    """CoinGecko API를 사용하여 BTC/ETH 시세를 가져옵니다."""
    url = "https://api.coingecko.com/api/v3/simple/price"
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
            
    return "\n".join(out)


def get_exchange_rates():
    """🌟 수정: JPY 환율을 100엔당 KRW로 정확히 계산합니다."""
    try:
        j = http_get(f"https://v6.exchangerate-api.com/v6/{EXCHANGE_KEY}/latest/USD").json()
        rates = j.get("conversion_rates", {})
        
        krw_rate = rates.get('KRW', 0) # USD당 KRW
        jpy_rate = rates.get('JPY', 0) # USD당 JPY
        
        # 100 JPY당 KRW 계산: (KRW/USD) / (JPY/USD) * 100
        jpy_to_krw_100 = (krw_rate / jpy_rate) * 100 if krw_rate and jpy_rate else 0

        return (
            f"USD: 1.00 기준\n"
            f"KRW: {krw_rate:.2f}\n"
            f"JPY (100엔): {jpy_to_krw_100:.2f}\n" # <-- 수정된 계산값 적용
            f"EUR: {rates.get('EUR', 0):.2f}\n"
            f"CNY: {rates.get('CNY', 0):.2f}"
        )
    except Exception as e:
        print(f"[ERROR] 환율 수집 실패: {e}")
        return "환율 데이터 수집 오류"


def get_fred_data(api_key, series_id, name, unit=""):
    """FRED API에서 단일 시계열 데이터를 가져오는 범용 함수."""
    try:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 1
        }
        j = http_get(url, params=params).json()
        
        latest_observation = j.get("observations", [{}])[0]
        value_str = latest_observation.get("value")
        date = latest_observation.get("date", "최신")
        
        if value_str and value_str != ".":
            value = float(value_str)
            return f"• {name}: {value:+.2f}{unit} (기준일: {date})"
        else:
            return f"• {name}: 데이터 없음 (FRED API)"

    except Exception as e:
        print(f"[ERROR] {name} 수집 실패: {e}")
        return f"• {name}: API 연결 오류"

def get_tips_yield(api_key):
    """10년 만기 TIPS (실질금리) 수익률 (FII10)"""
    return get_fred_data(api_key, "FII10", "10년 TIPS (실질금리)", unit="%")

def get_cpi_index(api_key):
    """미국 소비자 물가 지수 (CPIAUCSL)"""
    return get_fred_data(api_key, "CPIAUCSL", "미국 CPI (지수)", unit="")


def get_vix_index(api_key):
    """TwelveData API를 사용하여 VIX 지수 (공포 지수)를 가져옵니다."""
    try:
        j = http_get("https://api.twelvedata.com/quote",
                     params={"symbol": "VIX", "apikey": api_key}).json()
        
        if j.get('status') == 'error':
             raise RuntimeError(f"TwelveData Error: {j.get('message', '알 수 없는 오류')}")
             
        p = float(j["close"])
        c = float(j["change"])
        pct = float(j["percent_change"])
        icon = "▲" if c > 0 else "▼" if c < 0 else "-"

        # VIX 지수 해석
        if p < 15: classification = "낮음 (시장 안정)"
        elif p < 20: classification = "보통 (주의)"
        elif p < 30: classification = "높음 (리스크 경고)"
        else: classification = "매우 높음 (공포 심리)"

        return f"<b>🔥 VIX 지수(공포 지수):</b> {p:.2f} {icon}{abs(c):.2f} ({pct:+.2f}%) - {classification}"

    except Exception as e:
        print(f"[ERROR] VIX 지수 수집 실패: {e}")
        return "<b>🔥 VIX 지수:</b> 정보 없음"


def get_sector_etf_changes(api_key):
    etfs = {"💻 기술": "XLK", "🏦 금융": "XLF", "💊 헬스케어": "XLV", "⚡ 에너지": "XLE", "🛒 소비재": "XLY"}
    out = []
    for name, sym in etfs.items():
        try:
            j = http_get("https://api.twelvedata.com/quote",
                         params={"symbol": sym, "apikey": api_key}).json()
            if j.get('status') == 'error': continue # TwelveData 오류시 다음 항목으로
                         
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
            if j.get('status') == 'error': continue # TwelveData 오류시 다음 항목으로
                         
            p = float(j["close"]); c = float(j["change"]); pct = float(j["percent_change"])
            icon = "▲" if c > 0 else "▼" if c < 0 else "-"
            out.append(f"• {name}: ${p:.2f} {icon}{abs(c):.2f} ({pct:+.2f}%)")
        except Exception:
            out.append(f"• {name}: 정보 없음")
    return "\n".join(out)


def get_fear_greed_index():
    try:
        j = http_get("https://api.alternative.me/fng/", params={"limit": 1}).json()
        data = j["data"][0]
        value = data["value"]; label = data["value_classification"]
        return f"<b>📌 공포·탐욕 지수 (코인 Crypto 기준):</b> {value}점 ({label})"
    except Exception as e:
        print("[ERROR] 공포·탐욕 지수 예외:", e)
        return "<b>📌 공포·탐욕 지수:</b> 가져오기 실패"


def fetch_us_market_news_titles():
    # 뉴스 스크래핑 로직 유지 (안정성 문제로 생략)
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


def fetch_media_press_ranking_playwright(press_id="215", count=10):
    # Playwright 로직 유지 (복잡도/길이 문제로 내부 함수 본문 생략)
    url = f"https://media.naver.com/press/{press_id}/ranking"
    result = f"📌 언론사 {press_id} 랭킹 뉴스 TOP {count}\n"
    anchors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page()
            page.goto(url)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000) # 로딩 대기
            
            try:
                page.wait_for_selector(f"a[href*='/article/{press_id}/']", timeout=10000)
                anchors = page.query_selector_all(f"a[href*='/article/{press_id}/']")[:count]
            except PlaywrightTimeoutError:
                # 타임아웃 발생 시 일반적인 리스트 항목으로 대체 시도
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
    except Exception as e:
        print(f"[ERROR] Playwright 뉴스 수집 오류: {e}")
        return "📌 네이버 랭킹 뉴스: 수집 오류 발생"

    return result if anchors else f"• 현재 시점에 해당 언론사의 랭킹 뉴스가 없습니다.\n"


# ── 메시지/전송 ──────────────────────────────────────────

def build_message():
    # 🌟 HTML <b> 태그를 사용하여 제목 포맷팅
    fred_data = (
        f"<b>🇺🇸 주요 경제 지표 (FRED)</b>:\n"
        f"{get_tips_yield(FRED_API_KEY)}\n"
        f"{get_cpi_index(FRED_API_KEY)}\n"
    )
    
    # 🌟 KOSPI/KOSDAQ 함수 교체
    korean_indices = get_korean_indices_twelve(TWELVEDATA_API) 

    return (
        f"<b>📈 [{today}] 뉴스 요약 + 시장 지표</b>\n\n"
        f"<b>📊 미국 주요 지수</b>:\n{get_us_indices()}\n\n"
        f"<b>🇰🇷 한국 주요 지수</b>:\n{korean_indices}\n\n" # 교체된 함수 사용
        f"<b>💱 환율</b>:\n{get_exchange_rates()}\n\n"
        f"{fred_data}\n"
        f"<b>🌐 주요 암호화폐 시세</b>:\n{get_crypto_prices()}\n\n"
        f"<b>📉 미국 섹터별 지수 변화</b>:\n{get_sector_etf_changes(TWELVEDATA_API)}\n\n"
        f"{get_vix_index(TWELVEDATA_API)}\n" # <b> 태그는 함수 내부에서 적용됨
        f"{get_fear_greed_index()}\n\n" # <b> 태그는 함수 내부에서 적용됨
        f"<b>📌 주요 종목 시세</b>:\n{get_stock_prices(TWELVEDATA_API)}"
    )


def send_to_telegram():
    part1 = build_message()
    part2 = fetch_media_press_ranking_playwright("215", 10)

    for chat_id in CHAT_IDS:  # ✅ 여러 명에게 순차 전송
        for msg in (part1, part2):
            if len(msg) > 4000:
                msg = msg[:3990] + "\n(※ 일부 생략됨)"
            
            # 🌟 parse_mode='HTML'을 추가하여 포맷팅 적용
            data = {"chat_id": chat_id.strip(), "text": msg, "parse_mode": "HTML"}
            res = http_post(TELEGRAM_URL, data=data)
            print(f"✅ {chat_id} 전송 완료 | 코드: {res.status_code if res else 'N/A'}")


# ── 스케줄러 ──────────────────────────────────────────────
schedule.every().day.at("07:00").do(send_to_telegram)
schedule.every().day.at("15:00").do(send_to_telegram)
schedule.every().day.at("22:00").do(send_to_telegram)

if __name__ == "__main__":
    send_to_telegram()
