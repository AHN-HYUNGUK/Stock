import datetime, os, re, requests
from collections import Counter
from bs4 import BeautifulSoup
from googletrans import Translator


# ✅ 환경 변수
TOKEN = os.environ['TOKEN']
CHAT_ID = os.environ['CHAT_ID']
NEWS_API_KEY = os.environ['NEWSAPI']
EXCHANGE_KEY = os.environ['EXCHANGEAPI']
TWELVE_API_KEY = os.environ["TWELVEDATA_API"]
TELEGRAM_URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
today = datetime.datetime.now().strftime('%Y년 %m월 %d일')

translator = Translator()


# ✅ 미국 지수 크롤링 (Investing.com)
def get_us_indices():
    url = "https://www.investing.com/indices/major-indices"
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers)
    soup = BeautifulSoup(res.text, "html.parser")
    rows = soup.select("table tbody tr")[:3]
    results = []
    for row in rows:
        try:
            name = row.select_one("td:nth-child(2)").text.strip()
            now = float(
                row.select_one("td:nth-child(3)").text.strip().replace(
                    ",", ""))
            prev = float(
                row.select_one("td:nth-child(4)").text.strip().replace(
                    ",", ""))
            diff = now - prev
            rate = (diff / prev) * 100
            icon = "▲" if diff > 0 else "▼" if diff < 0 else "-"
            results.append(
                f"{name}: {now:,.2f} {icon}{abs(diff):,.2f} ({rate:+.2f}%)")
        except:
            results.append(f"{name}: 데이터 오류")
    return "\n".join(results)


# ✅ 환율
def get_exchange_rates():
    url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_KEY}/latest/USD"
    res = requests.get(url).json()
    try:
        rates = res["conversion_rates"]
        return (f"USD: 1.00 기준\n"
                f"KRW: {rates['KRW']:.2f}\n"
                f"JPY (100엔): {rates['JPY'] * 100:.2f}\n"
                f"EUR: {rates['EUR']:.2f}\n"
                f"CNY: {rates['CNY']:.2f}")
    except:
        return "(환율 정보 없음)"


# ✅ 섹터별 ETF
def get_sector_etf_changes(api_key):
    etfs = {
        "💻 기술": "XLK",
        "🏦 금융": "XLF",
        "💊 헬스케어": "XLV",
        "⚡ 에너지": "XLE",
        "🛒 소비재": "XLY"
    }
    result = []
    for name, symbol in etfs.items():
        url = f"https://api.twelvedata.com/quote?symbol={symbol}&apikey={api_key}"
        try:
            res = requests.get(url).json()
            price = float(res["close"])
            change = float(res["change"])
            percent = float(res["percent_change"])
            icon = "▲" if change > 0 else "▼" if change < 0 else "-"
            result.append(
                f"{name}: {price:.2f} {icon}{abs(change):.2f} ({percent:+.2f}%)"
            )
        except:
            result.append(f"{name}: 정보 없음")
    return "\n".join(result)



# ✅ 네이버 한국뉴스 크롤링
def fetch_naver_sector_news(sector_dict):
    headers = {"User-Agent": "Mozilla/5.0"}
    message = ""
    for sector, keywords in sector_dict.items():
        news_items = []
        for kw in keywords:
            url = f"https://search.naver.com/search.naver?where=news&query={kw}"
            try:
                res = requests.get(url, headers=headers, timeout=5)
                soup = BeautifulSoup(res.text, "html.parser")
                articles = soup.select("ul.list_news div.news_area a.tit")[:2]
                for a in articles:
                    title = a.text.strip()
                    link = a['href']
                    news_items.append(f"• {title}\n👉 {link}")
            except:
                continue
        if news_items:
            message += f"{sector}\n" + "\n".join(news_items[:2]) + "\n\n"
    return message or "(관련 뉴스 없음)\n"

# ✅ 네이버 미국뉴스 크롤링
def fetch_us_world_news():
    url = "https://search.naver.com/search.naver?where=news&query=미국 증시 OR 미국 경제"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers)
        soup = BeautifulSoup(res.text, "html.parser")
        items = soup.select("ul.list_news div.news_area a.tit")[:3]
        return "\n".join(f"• {a.text.strip()}\n👉 {a['href']}" for a in items)
    except:
        return "(미국 관련 세계 뉴스 없음)"



# ✅ 메시지 작성 및 전송
message = f"📈 [{today}] 뉴스 요약 + 시장 지표\n\n"
message += f"📊 미국 주요 지수:\n{get_us_indices()}\n\n"
message += f"💱 환율:\n{get_exchange_rates()}\n\n"
message += f"📉 미국 섹터별 지수 변화:\n{get_sector_etf_changes(TWELVE_API_KEY)}\n\n"
message += f"🇰🇷 한국 증시 뉴스 (업종별):\n{fetch_naver_sector_news(sector_keywords_kr)}"
message += f"🌎 미국 관련 세계 뉴스:\n{fetch_us_world_news()}\n"


res = requests.post(TELEGRAM_URL, data={"chat_id": CHAT_ID, "text": message})
print("✅ 응답 코드:", res.status_code)
print("📨 응답 내용:", res.text)

import schedule
import time


def send_to_telegram():
    # 여기에 기존 텔레그램 메시지 생성 + 전송 로직을 통째로 넣으면 됨
    print("🕖 뉴스 전송 시작")
    res = requests.post(TELEGRAM_URL,
                        data={
                            "chat_id": CHAT_ID,
                            "text": message
                        })
    print("✅ 응답 코드:", res.status_code)
    print("📨 응답 내용:", res.text)


# ✅ 매일 오전 7시, 오후 3시에 실행되도록 예약
schedule.every().day.at("07:00").do(send_to_telegram)
schedule.every().day.at("15:00").do(send_to_telegram)

# ✅ 계속 실행하면서 시간 체크
while True:
    schedule.run_pending()
    time.sleep(30)  # 30초마다 체크
