import os
import datetime
import requests
import schedule
import time
from collections import Counter
from bs4 import BeautifulSoup
from googletrans import Translator
from playwright.sync_api import sync_playwright
import openai

load_dotenv = None  # dotenv 로딩이 필요 없으면 주석 처리

# 환경 변수
TOKEN           = os.environ['TOKEN']
CHAT_ID         = os.environ['CHAT_ID']
EXCHANGE_KEY    = os.environ['EXCHANGEAPI']
TWELVE_API_KEY  = os.environ["TWELVEDATA_API"]
TELEGRAM_URL    = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
today           = datetime.datetime.now().strftime('%Y년 %m월 %d일')

translator = Translator()


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
            icon = "▲" if diff>0 else "▼" if diff<0 else "-"
            out.append(f"{name}: {now:,.2f} {icon}{abs(diff):.2f} ({pct:+.2f}%)")
        except:
            out.append(f"{name}: 데이터 오류")
    return "\n".join(out)


def get_exchange_rates():
    res = requests.get(f"https://v6.exchangerate-api.com/v6/{EXCHANGE_KEY}/latest/USD").json()
    rates = res.get("conversion_rates", {})
    return (
        f"USD: 1.00 기준\n"
        f"KRW: {rates.get('KRW',0):.2f}\n"
        f"JPY (100엔): {rates.get('JPY',0)*100:.2f}\n"
        f"EUR: {rates.get('EUR',0):.2f}\n"
        f"CNY: {rates.get('CNY',0):.2f}"
    )


def get_sector_etf_changes(api_key):
    etfs = {"💻 기술":"XLK","🏦 금융":"XLF","💊 헬스케어":"XLV","⚡ 에너지":"XLE","🛒 소비재":"XLY"}
    out = []
    for name,sym in etfs.items():
        try:
            j = requests.get(f"https://api.twelvedata.com/quote?symbol={sym}&apikey={api_key}").json()
            p = float(j["close"]); c= float(j["change"]); pct=float(j["percent_change"])
            icon = "▲" if c>0 else "▼" if c<0 else "-"
            out.append(f"{name}: {p:.2f} {icon}{abs(c):.2f} ({pct:+.2f}%)")
        except:
            out.append(f"{name}: 정보 없음")
    return "\n".join(out)


def fetch_us_market_news_titles():
    try:
        url = "https://finance.yahoo.com/"
        soup = BeautifulSoup(requests.get(url,headers={"User-Agent":"Mozilla/5.0"}).text, "html.parser")
        arts = soup.select("li.js-stream-content a.js-content-viewer")[:3]
        return "\n".join(
            f"• {a.get_text(strip=True)}\n👉 { (a['href'] if a['href'].startswith('http') else 'https://finance.yahoo.com'+a['href']) }"
            for a in arts
        ) or "(기사 없음)"
    except:
        return "(뉴스 수집 실패)"


def fetch_media_press_ranking_playwright(press_id="215", count=10):
    url    = f"https://media.naver.com/press/{press_id}/ranking"
    result = f"📌 언론사 {press_id} 랭킹 뉴스 TOP {count}\n"
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page    = browser.new_page()
        page.goto(url); page.wait_for_load_state("networkidle"); page.wait_for_timeout(2000)
        anchors = page.query_selector_all(f"a[href*='/article/{press_id}/']")[:count]
        for a in anchors:
            img = a.query_selector("img")
            title = img.get_attribute("alt").strip() if img and img.get_attribute("alt") else a.inner_text().split("조회수")[0].strip()
            href  = a.get_attribute("href")
            if not href.startswith("http"):
                href = "https://n.news.naver.com" + href
            result += f"• {title}\n👉 {href}\n"
        browser.close()
    return result if anchors else f"(press/{press_id} 랭킹 뉴스 없음)"


 def build_message():
     return (
         f"📈 [{today}] 뉴스 요약 + 시장 지표\n\n"
         f"📊 미국 주요 지수:\n{get_us_indices()}\n\n"
         f"💱 환율:\n{get_exchange_rates()}\n\n"
         f"📉 미국 섹터별 지수 변화:\n{get_sector_etf_changes(TWELVE_API_KEY)}\n\n"
         f"📰 미국 증시 주요 기사:\n{fetch_us_market_news_titles()}\n\n"
-        # (랭킹 누락)
+        # 언론사 215 랭킹 TOP10 추가
+        f"{fetch_media_press_ranking_playwright('215',10)}"
     )



 def send_to_telegram():
-    msg = build_message()
-    if len(msg)>4000:
-        msg = msg[:3990] + "\n(※ 일부 생략됨)"
-    r = requests.post(TELEGRAM_URL, data={"chat_id":CHAT_ID,"text":msg})
-    print("✅ 응답 코드:", r.status_code, "| 📨", r.text)
+    # 1) 시장 지표 + 미국 증시 뉴스
+    part1 = build_message()
+
+    # 2) 언론사 215 랭킹 TOP10
+    part2 = fetch_media_press_ranking_playwright("215", 10)
+
+    for msg in [part1, part2]:
+        if len(msg) > 4000:
+            msg = msg[:3990] + "\n(※ 일부 생략됨)"
+        r = requests.post(TELEGRAM_URL, data={"chat_id":CHAT_ID,"text":msg})
+        print("✅ 응답 코드:", r.status_code, "| 📨", r.text)


# 매일 07:00, 15:00 KST 실행
schedule.every().day.at("07:00").do(send_to_telegram)
schedule.every().day.at("15:00").do(send_to_telegram)

if __name__=="__main__":
    send_to_telegram()
