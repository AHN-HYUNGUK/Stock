import os
import datetime
import requests
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
    """HTTP POST 요청을 보냅니다. (텔레그램 오류 무시 로직 + 타임아웃 추가)"""
    try:
        r = requests.post(url, data=data, timeout=20)

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
ALPHAVANTAGE_KEY  = os.environ.get("ALPHAVANTAGE_KEY", "")
TELEGRAM_URL      = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
today             = datetime.datetime.now().strftime('%Y년 %m월 %d일')

CHAT_IDS = [
    _id.strip()
    for _id in os.environ['CHAT_IDS'].split(",")
    if _id.strip()
]


# ── TwelveData 전용 호출 래퍼 (분당 8회 제한 대응) ──────────────────────
_twelvedata_call_count = 0

def twelvedata_get(params, timeout=20):
    global _twelvedata_call_count
    _twelvedata_call_count += 1

    if _twelvedata_call_count > 1 and (_twelvedata_call_count - 1) % 7 == 0:
        print(f"[RATE LIMIT] TwelveData 분당 한도 방지를 위해 61초 대기 "
              f"(누적 호출 {_twelvedata_call_count - 1}회)...")
        time.sleep(61)
    else:
        time.sleep(1.5)

    return http_get("https://api.twelvedata.com/quote", params=params, timeout=timeout)


def twelvedata_quote_line(name, symbol, api_key, prefix="•", unit_fmt="{:.2f}"):
    """TwelveData quote 하나를 가져와 한 줄짜리 문자열로 변환하는 공용 함수."""
    try:
        j = twelvedata_get({"symbol": symbol, "apikey": api_key}).json()
        print(f"[DEBUG] {name}({symbol}) 응답: {j}")

        if j.get('status') == 'error':
            raise RuntimeError(f"TwelveData Error: {j.get('message', '알 수 없는 오류')}")

        p = float(j["close"]); c = float(j["change"]); pct = float(j["percent_change"])
        icon = "▲" if c > 0 else "▼" if c < 0 else "-"
        p_str = unit_fmt.format(p)
        return f"{prefix} {name}: {p_str} {icon}{abs(c):.2f} ({pct:+.2f}%)"
    except Exception as e:
        print(f"[ERROR] {name}({symbol}) 수집 실패: {e}")
        return f"{prefix} {name}: 정보 없음"


# ── 지표/시세 수집 ────────────────────────────────────────

def get_us_index_proxies(api_key):
    proxies = {
        "다우존스 추종 ETF (DIA)": "DIA",
        "S&P500 추종 ETF (SPY)": "SPY",
        "나스닥100 추종 ETF (QQQ)": "QQQ",
    }
    out = []
    for name, sym in proxies.items():
        out.append(twelvedata_quote_line(name, sym, api_key, unit_fmt="${:.2f}"))
    return "\n".join(out)


def get_korea_proxy_twelve(api_key):
    return twelvedata_quote_line(
        "한국 대형주 ETF (EWY, 코스피 근사치)", "EWY", api_key, unit_fmt="${:.2f}"
    )


def get_crypto_prices():
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        "ids": "bitcoin,ethereum",
        "vs_currencies": "usd",
        "include_24hr_change": "true"
    }

    out = []
    try:
        j = http_get(url, params=params).json()

        btc_data = j.get("bitcoin", {})
        if btc_data:
            price = btc_data.get("usd", 0)
            pct_change = btc_data.get("usd_24h_change", 0)
            icon = "▲" if pct_change > 0 else "▼" if pct_change < 0 else "-"
            out.append(f"• ₿ 비트코인: ${price:,.0f} ({icon}{pct_change:+.2f}%)")

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
    """JPY 환율을 100엔당 KRW로 계산."""
    try:
        j = http_get(f"https://v6.exchangerate-api.com/v6/{EXCHANGE_KEY}/latest/USD").json()
        rates = j.get("conversion_rates", {})

        krw_rate = rates.get('KRW', 0)
        jpy_rate = rates.get('JPY', 0)
        jpy_to_krw_100 = (krw_rate / jpy_rate) * 100 if krw_rate and jpy_rate else 0

        return (
            f"USD: 1.00 기준\n"
            f"KRW: {krw_rate:.2f}\n"
            f"JPY (100엔): {jpy_to_krw_100:.2f}\n"
            f"EUR: {rates.get('EUR', 0):.2f}\n"
            f"CNY: {rates.get('CNY', 0):.2f}"
        )
    except Exception as e:
        print(f"[ERROR] 환율 수집 실패: {e}")
        return "환율 데이터 수집 오류"


def get_fred_data(api_key, series_id, name, unit=""):
    """FRED API에서 단일 시계열 데이터를 가져오는 범용 함수. (완전 무료, 사용량 사실상 무제한)"""
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


def get_10y_treasury_yield(api_key):
    """미국 10년물 국채 명목금리 (DGS10)"""
    return get_fred_data(api_key, "DGS10", "미국 10년물 국채금리", unit="%")


# ── 리스크 지표: 장단기 금리차 (설명 문구 + 역전 표기) ──────────────────
def get_yield_curve_spread(api_key):
    """
    장단기 금리차 10Y-2Y 스프레드 (T10Y2Y).
    마이너스(역전)일 때 "역전"으로 표기.
    """
    try:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": "T10Y2Y",
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 1
        }
        j = http_get(url, params=params).json()
        latest = j.get("observations", [{}])[0]
        value_str = latest.get("value")
        date = latest.get("date", "최신")

        if value_str and value_str != ".":
            value = float(value_str)
            status = " ⚠️ 역전" if value < 0 else ""
            line = f"• 장단기 금리차(10Y-2Y): {value:+.2f}%p (기준일: {date}){status}"
            note = ("※ 10년물 국채금리 - 2년물 국채금리. 마이너스(역전)면 시장이 향후 경기 둔화를 "
                    "경계한다는 신호로 흔히 해석됩니다. 다만 역전 이후 실제 침체까지는 통상 수개월~2년의 "
                    "시차가 있어 왔고, 이 지표 하나로 시점을 예측할 수는 없습니다.")
            return f"{line}\n{note}"
        else:
            return "• 장단기 금리차(10Y-2Y): 데이터 없음"
    except Exception as e:
        print(f"[ERROR] 장단기 금리차 수집 실패: {e}")
        return "• 장단기 금리차(10Y-2Y): API 연결 오류"


# ── 리스크 지표: 하이일드 신용스프레드 (구간 표기 + 설명 문구) ──────────
def _classify_high_yield_spread(value_pct):
    if value_pct < 3:
        return "매우 낮음"
    elif value_pct < 6:
        return "정상 범주"
    elif value_pct < 8:
        return "확대 국면(주의)"
    else:
        return "위기 수준"


def get_high_yield_spread(api_key):
    """
    하이일드 신용스프레드 (BAMLH0A0HYM2, ICE BofA US High Yield OAS).
    3%p 미만 매우 낮음 · 3~6%p 정상 범주 · 6~8%p 확대 국면(주의) · 8%p 이상 위기 수준
    """
    try:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": "BAMLH0A0HYM2",
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 1
        }
        j = http_get(url, params=params).json()
        latest = j.get("observations", [{}])[0]
        value_str = latest.get("value")
        date = latest.get("date", "최신")

        if value_str and value_str != ".":
            value = float(value_str)
            classification = _classify_high_yield_spread(value)
            line = f"• 하이일드 신용스프레드: {value:+.2f}% (기준일: {date}) - {classification}"
            note = ("※ 신용도 낮은 회사채(정크본드)와 국채 간 금리 차이. 3%p 미만 매우 낮음 · "
                    "3~6%p 평상시 정상 범주 · 6~8%p 확대 국면(주의) · 8%p 이상 위기 수준 "
                    "(2008년 금융위기·2020년 코로나 폭락 때 8%p를 크게 상회). "
                    "공식 기준이 아닌 시장에서 통용되는 대략적 참고치입니다.")
            return f"{line}\n{note}"
        else:
            return "• 하이일드 신용스프레드: 데이터 없음"
    except Exception as e:
        print(f"[ERROR] 하이일드 신용스프레드 수집 실패: {e}")
        return "• 하이일드 신용스프레드: API 연결 오류"


# ── VIX 근사 ETF (구간 표기 + 설명 문구) ────────────────────────────
def _classify_vix(price):
    if price < 15:
        return "낮음(안정)"
    elif price < 20:
        return "보통"
    elif price < 30:
        return "높음(경계)"
    else:
        return "매우 높음(공포)"


def get_vix_proxy(api_key):
    """
    VIX 지수 자체는 무료 플랜에서 404가 확인되어, VIX 선물 추종 ETF(VIXY)로 대체.
    15 미만 낮음(안정) · 15~20 보통 · 20~30 높음(경계) · 30 이상 매우 높음
    """
    try:
        j = twelvedata_get({"symbol": "VIXY", "apikey": api_key}).json()
        print(f"[DEBUG] VIXY 응답: {j}")

        if j.get('status') == 'error':
            raise RuntimeError(f"TwelveData Error: {j.get('message', '알 수 없는 오류')}")

        p = float(j["close"])
        c = float(j["change"])
        pct = float(j["percent_change"])
        icon = "▲" if c > 0 else "▼" if c < 0 else "-"
        classification = _classify_vix(p)

        line = f"<b>🔥 VIX 근사 ETF(VIXY):</b> ${p:.2f} {icon}{abs(c):.2f} ({pct:+.2f}%) - {classification}"
        note = ("※ 시장 변동성 기대치. 15 미만 낮음(안정) · 15~20 보통 · 20~30 높음(경계) · "
                "30 이상 매우 높음(공포). VIXY는 VIX 선물을 담는 ETF라 장기적으로 VIX 지수와 "
                "정확히 같은 스케일로 움직이지 않을 수 있습니다.")
        return f"{line}\n{note}"

    except Exception as e:
        print(f"[ERROR] VIX 근사 ETF 수집 실패: {e}")
        return "<b>🔥 VIX 근사 ETF(VIXY):</b> 정보 없음"


def get_sector_etf_changes(api_key):
    etfs = {"💻 기술": "XLK", "🏦 금융": "XLF", "💊 헬스케어": "XLV", "⚡ 에너지": "XLE", "🛒 소비재": "XLY"}
    out = []
    for name, sym in etfs.items():
        out.append(twelvedata_quote_line(name, sym, api_key))
    return "\n".join(out)


def get_risk_indicators_twelve(api_key):
    """
    금(GLD ETF로 대체) / 반도체 ETF(SOXX, SOX지수 추종) — 리스크오프·반도체 사이클 선행지표.
    """
    out = []
    out.append(twelvedata_quote_line("금(GLD ETF)", "GLD", api_key, unit_fmt="${:.2f}"))
    out.append(twelvedata_quote_line("반도체 ETF (SOXX, SOX지수 추종)", "SOXX", api_key, unit_fmt="${:.2f}"))
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
        out.append(twelvedata_quote_line(name, sym, api_key, unit_fmt="${:.2f}"))
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
    url = f"https://media.naver.com/press/{press_id}/ranking"
    result = f"📌 언론사 {press_id} 랭킹 뉴스 TOP {count}\n"
    anchors = []
    try:
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
    except Exception as e:
        print(f"[ERROR] Playwright 뉴스 수집 오류: {e}")
        return "📌 네이버 랭킹 뉴스: 수집 오류 발생"

    return result if anchors else f"• 현재 시점에 해당 언론사의 랭킹 뉴스가 없습니다.\n"


# ── 메시지/전송 ──────────────────────────────────────────

def build_message():
    fred_data = (
        f"<b>🇺🇸 주요 경제 지표 (FRED)</b>:\n"
        f"{get_tips_yield(FRED_API_KEY)}\n"
        f"{get_10y_treasury_yield(FRED_API_KEY)}\n"
        f"{get_cpi_index(FRED_API_KEY)}\n"
    )

    risk_bond_data = (
        f"<b>⚠️ 리스크 지표 (채권·신용시장)</b>:\n"
        f"{get_yield_curve_spread(FRED_API_KEY)}\n\n"
        f"{get_high_yield_spread(FRED_API_KEY)}\n"
    )

    korea_proxy = get_korea_proxy_twelve(TWELVEDATA_API)

    return (
        f"<b>📈 [{today}] 뉴스 요약 + 시장 지표</b>\n\n"
        f"<b>📊 미국 주요 지수 (ETF 프록시)</b>:\n{get_us_index_proxies(TWELVEDATA_API)}\n\n"
        f"<b>🇰🇷 한국 시장 (ETF 프록시)</b>:\n{korea_proxy}\n\n"
        f"<b>💱 환율</b>:\n{get_exchange_rates()}\n\n"
        f"{fred_data}\n"
        f"{risk_bond_data}\n"
        f"<b>🌐 주요 암호화폐 시세</b>:\n{get_crypto_prices()}\n\n"
        f"<b>📉 미국 섹터별 지수 변화</b>:\n{get_sector_etf_changes(TWELVEDATA_API)}\n\n"
        f"<b>🛡️ 리스크오프 지표 (금 · 반도체)</b>:\n{get_risk_indicators_twelve(TWELVEDATA_API)}\n\n"
        f"{get_vix_proxy(TWELVEDATA_API)}\n\n"
        f"{get_fear_greed_index()}\n\n"
        f"<b>📌 주요 종목 시세</b>:\n{get_stock_prices(TWELVEDATA_API)}"
    )


def send_to_telegram():
    part1 = build_message()
    part2 = fetch_media_press_ranking_playwright("215", 10)

    for chat_id in CHAT_IDS:
        for msg in (part1, part2):
            if len(msg) > 4000:
                msg = msg[:3990] + "\n(※ 일부 생략됨)"

            data = {"chat_id": chat_id.strip(), "text": msg, "parse_mode": "HTML"}
            res = http_post(TELEGRAM_URL, data=data)
            print(f"✅ {chat_id} 전송 완료 | 코드: {res.status_code if res else 'N/A'}")


# ── 실행 ──────────────────────────────────────────────────
# 스케줄은 main.yml(GitHub Actions cron)에서 관리하므로 1회만 실행합니다.
if __name__ == "__main__":
    send_to_telegram()
