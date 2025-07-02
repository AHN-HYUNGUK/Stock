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


# ✅ 기사 요약
def get_article_text(url):
    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(res.text, "html.parser")
        paragraphs = soup.find_all('p')
        text = "\n".join([p.text for p in paragraphs])
        return text.strip()
    except:
        return "(본문 수집 실패)"


def summarize_text(text, max_sentences=3):
    sentences = re.split(r'(?<=[.?!])\s+', text)
    if len(sentences) <= max_sentences:
        return text
    words = re.findall(r'\w+', text.lower())
    freq = Counter(words)
    ranked = sorted(sentences,
                    key=lambda s: sum(freq[w]
                                      for w in re.findall(r'\w+', s.lower())),
                    reverse=True)
    return " ".join(ranked[:max_sentences])


def get_translated_summary(url):
    text = get_article_text(url)
    if text.startswith("(본문 수집 실패)"):
        return text
    summary_en = summarize_text(text)
    try:
        return translator.translate(summary_en, src='en', dest='ko').text
    except:
        return "(번역 실패)"


# ✅ 뉴스 수집
def fetch_news(keyword, lang="en"):
    from_date = (datetime.datetime.now() -
                 datetime.timedelta(days=2)).strftime("%Y-%m-%d")
    to_date = datetime.datetime.now().strftime("%Y-%m-%d")
    url = (f"https://newsapi.org/v2/everything?"
           f"q={keyword}&language={lang}&sortBy=publishedAt&pageSize=5"
           f"&from={from_date}&to={to_date}&apiKey={NEWS_API_KEY}")
    return requests.get(url).json()


def get_filtered_articles(articles):
    return articles[:2]  # 필터 제거 (신뢰 언론만 제한하지 않음)


# ✅ 업종별 뉴스 분류
sector_keywords_en = {
    "📈 시장전반": [
        "stock market", "Dow", "Nasdaq", "S&P", "Fed", "inflation",
        "interest rate", "bond yields", "rate hike", "treasury", "US economy",
        "economy news", "market news", "stock news", "JP Morgan", "goldman",
        "wall street", "powell", "yellen"
    ],
    "💻 기술": [
        "technology", "semiconductor", "AI", "Apple", "Nvidia", "Microsoft",
        "Google", "Tesla", "big tech", "tech stocks", "tech sector",
        "tech news", "tech trends", "IONQ", "Palantir"
    ],
    "🏦 금융": [
        "finance", "bank", "JP Morgan", "Goldman Sachs", "credit",
        "earnings report", "loan", "insurance", "financial news"
    ],
    "국제이슈":
    ["tramp", "china", "iran", "ukraine", "EU", "NATO", "war", "nuclear"]
}

sector_keywords_kr = {
    "📈 한국증시": ["코스피", "코스닥", "환율", "금리", "무역수지", "외국인 매수", "외환보유액"],
    "💻 IT·반도체": ["삼성전자", "반도체", "AI", "SK하이닉스", "이차전지", "OLED", "DDR5", "AI"],
    "🚗 자동차·모빌리티": ["현대차", "기아", "전기차", "자율주행", "배터리", "UAM", "친환경차"],
    "정치이슈": ["이재명", "윤석열", "국회", "특검"]
}


def fetch_sector_news(sector_dict, lang="en"):
    message = ""
    for sector, keywords in sector_dict.items():
        articles = []
        for kw in keywords:
            res = fetch_news(kw, lang)
            if res.get("status") == "ok":
                articles += get_filtered_articles(res["articles"])
        unique = {a["title"]: a for a in articles}.values()
        if unique:
            message += f"{sector}\n"
            for a in list(unique)[:1]:
                url = a["url"]
                summary = get_translated_summary(
                    url) if lang == "en" else a["title"]
                message += f"• {summary}\n👉 {url}\n"
            message += "\n"
    return message or "(관련 뉴스 없음)\n"


# ✅ 네이버 한국뉴스 크롤링
def fetch_korean_finance_news():
    url = "https://finance.naver.com/news/mainnews.naver"
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers)
    soup = BeautifulSoup(res.text, "html.parser")
    items = soup.select(".mainNewsList li a")
    titles = [a.text.strip() for a in items if a.text.strip()]
    return "\n".join(f"• {t}" for t in titles[:3]) or "(한국 뉴스 없음)"


# ✅ 메시지 작성 및 전송
message = f"📈 [{today}] 뉴스 요약 + 시장 지표\n\n"
message += f"📊 미국 주요 지수:\n{get_us_indices()}\n\n"
message += f"💱 환율:\n{get_exchange_rates()}\n\n"
message += f"📉 미국 섹터별 지수 변화:\n{get_sector_etf_changes(TWELVE_API_KEY)}\n\n"
message += f"🇺🇸 미국 증시 뉴스 (업종별):\n{fetch_sector_news(sector_keywords_en, 'en')}"
message += f"🇰🇷 한국 증시 뉴스 (업종별):\n{fetch_sector_news(sector_keywords_kr, 'ko')}"
message += f"\n🇰🇷 한국 주요 뉴스:\n{fetch_korean_finance_news()}\n"
message += "\n출처: newsapi.org / investing.com / twelvedata.com / exchangerate-api.com"

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
