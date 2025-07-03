# main.py

import os                  # ← 이게 반드시 있어야 함!
import datetime
import re
import requests
import schedule
import time
import openai              # ← os 다음에 import openai
from collections import Counter
from bs4 import BeautifulSoup
from googletrans import Translator


# 환경 변수
TOKEN = os.environ['TOKEN']
CHAT_ID = os.environ['CHAT_ID']
EXCHANGE_KEY = os.environ['EXCHANGEAPI']
TWELVE_API_KEY = os.environ["TWELVEDATA_API"]
TELEGRAM_URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
today = datetime.datetime.now().strftime('%Y년 %m월 %d일')

translator = Translator()

# ✅ 미국 지수 크롤링
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
            now = float(row.select_one("td:nth-child(3)").text.strip().replace(",", ""))
            prev = float(row.select_one("td:nth-child(4)").text.strip().replace(",", ""))
            diff = now - prev
            rate = (diff / prev) * 100
            icon = "▲" if diff > 0 else "▼" if diff < 0 else "-"
            results.append(f"{name}: {now:,.2f} {icon}{abs(diff):,.2f} ({rate:+.2f}%)")
        except:
            results.append(f"{name}: 데이터 오류")
    return "\n".join(results)

# ✅ 환율 (KRW 포함)
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

# ✅ 미국 ETF 섹터별 지수
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
        try:
            url = f"https://api.twelvedata.com/quote?symbol={symbol}&apikey={api_key}"
            res = requests.get(url).json()
            price = float(res["close"])
            change = float(res["change"])
            percent = float(res["percent_change"])
            icon = "▲" if change > 0 else "▼" if change < 0 else "-"
            result.append(f"{name}: {price:.2f} {icon}{abs(change):.2f} ({percent:+.2f}%)")
        except:
            result.append(f"{name}: 정보 없음")
    return "\n".join(result)

# ✅ 미국 증시 뉴스 수집 (Investopedia 기준)
def fetch_us_market_news_titles():
    try:
        url = "https://finance.yahoo.com/"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers)
        soup = BeautifulSoup(res.text, "html.parser")

        # 주요 뉴스 섹션
        articles = soup.select("li.js-stream-content a.js-content-viewer")[:3]
        results = []

        for tag in articles:
            title = tag.get_text(strip=True)
            link = tag.get("href")
            if not link.startswith("http"):
                link = "https://finance.yahoo.com" + link
            results.append(f"• {title}\n👉 {link}")

        return "\n".join(results) if results else "(기사 없음)"
    except Exception as e:
        return f"(뉴스 수집 실패: {e})"




# ✅ GPT-4o mini 요약
# def summarize_news_with_gpt(news_titles):
#     if "❗" in news_titles:
#         return "(미국 뉴스 요약 실패)"
#     prompt = f"""다음은 미국 증시 관련 기사 제목들입니다. 이를 바탕으로 한국어로 간결한 아침 뉴스 요약을 작성해 주세요.\n\n{news_titles}"""
#     try:
#         response = openai.ChatCompletion.create(
#             model="gpt-4o",
#             messages=[{"role": "user", "content": prompt}],
#             temperature=0.3,
#             max_tokens=300
#         )
#         return response.choices[0].message.content.strip()
#     except Exception as e:
#         return f"(GPT 요약 실패: {e})"





# ✅ 네이버 한국 뉴스 (랭킹)
def fetch_naver_top10_news():
    try:
        url = "https://news.naver.com/main/ranking/popularDay.naver"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers)
        res.encoding = "utf-8"  # ✅ 강제 인코딩 설정
        soup = BeautifulSoup(res.text, "html.parser")

        # 전체 랭킹에서 주요 기사 a 태그 수집
        news_links = soup.select("div.rankingnews_box a")[:10]
        result = "📌 네이버 랭킹 뉴스 TOP 10\n"

        for a in news_links:
            title = a.text.strip()  # ✅ .get("title") 대신 text 사용
            link = a["href"]
            if not link.startswith("http"):
                link = "https://news.naver.com" + link
            result += f"• {title}\n👉 {link}\n"

        return result
    except Exception as e:
        return f"(랭킹 뉴스 수집 실패: {e})"







# ✅ 전체 메시지 작성
def build_message():
    message = f"📈 [{today}] 뉴스 요약 + 시장 지표\n\n"
    # ✅ GPT 요약 대신 뉴스 제목만 출력
    headlines = fetch_us_market_news_titles()
    message += f"📊 미국 주요 지수:\n{get_us_indices()}\n\n"
    message += f"💱 환율:\n{get_exchange_rates()}\n\n"
    message += f"📉 미국 섹터별 지수 변화:\n{get_sector_etf_changes(TWELVE_API_KEY)}\n\n"
    message += f"📰 미국 증시 주요 기사:\n{headlines}\n\n"
    message += f"📰 네이버 랭킹 뉴스:\n{fetch_naver_top10_news()}\n"
    return message



# ✅ 텔레그램 전송 함수 (안정화 적용 완료)
def send_to_telegram():
    message = build_message()

    # ✅ 해결 방법 1: 길이 제한 적용 (최대 4096자, 여유 있게 3990자)
    if len(message) > 4000:
        message = message[:3990] + "\n(※ 메시지 길이 초과로 일부 생략됨)"

    # ✅ 해결 방법 2: parse_mode 제거
    res = requests.post(TELEGRAM_URL, data={
        "chat_id": CHAT_ID,
        "text": message
    })

    print("✅ 응답 코드:", res.status_code)
    print("📨 응답 내용:", res.text)


# ✅ 예약 실행 (Replit 또는 로컬 테스트용)
schedule.every().day.at("07:00").do(send_to_telegram)
schedule.every().day.at("15:00").do(send_to_telegram)

# ✅ main.py 끝부분만 이렇게
if __name__ == "__main__":
    send_to_telegram()
