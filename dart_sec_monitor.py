import requests
import feedparser
import json
import os
from datetime import datetime

DART_API_KEY = os.environ["DART_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# ─────────────────────────────
# 1. DART 공시 가져오기
# ─────────────────────────────
def get_dart_news():
    today = datetime.now().strftime("%Y%m%d")
    url = "https://opendart.fss.or.kr/api/list.json"
    params = {
        "crtfc_key": DART_API_KEY,
        "bgn_de": today,
        "end_de": today,
        "corp_cls": "Y",   # Y=코스피, K=코스닥
        "sort": "date",
        "sort_mth": "desc",
        "page_count": 20
    }
    res = requests.get(url, params=params).json()
    return res.get("list", [])

# ─────────────────────────────
# 2. SEC EDGAR 공시 가져오기
# ─────────────────────────────
# 관심 종목 CIK 번호 (예시)
WATCH_LIST = {
    "NVDA": "0001045810",
    "MSFT": "0000789019",
    "AVGO": "0001730168",
    "MU"  : "0000723254",
}

def get_sec_news(company, cik):
    url = (
        f"https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={cik}"
        f"&type=8-K&count=5&output=atom"
    )
    feed = feedparser.parse(url)
    results = []
    for entry in feed.entries:
        results.append({
            "company": company,
            "title": entry.title,
            "link": entry.link,
            "updated": entry.updated
        })
    return results

# ─────────────────────────────
# 3. 텔레그램 전송
# ─────────────────────────────
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": msg,
        "parse_mode": "HTML"
    })

# ─────────────────────────────
# 4. 중복 방지 (이미 보낸 것 제외)
# ─────────────────────────────
SENT_FILE = "sent_ids.json"

def load_sent():
    if os.path.exists(SENT_FILE):
        return json.load(open(SENT_FILE))
    return []

def save_sent(ids):
    json.dump(ids, open(SENT_FILE, "w"))

# ─────────────────────────────
# 5. 메인 실행
# ─────────────────────────────
def main():
    sent = load_sent()
    new_sent = sent.copy()

    # DART 체크
    dart_items = get_dart_news()
    for item in dart_items:
        uid = item["rcept_no"]  # 공시 고유번호
        if uid not in sent:
            msg = (
                f"📋 <b>[DART 공시]</b>\n"
                f"기업: {item['corp_name']}\n"
                f"내용: {item['report_nm']}\n"
                f"시간: {item['rcept_dt']}\n"
                f"🔗 https://dart.fss.or.kr/dsaf001/main.do"
                f"?rcpNo={uid}"
            )
            send_telegram(msg)
            new_sent.append(uid)

    # SEC 체크
    for company, cik in WATCH_LIST.items():
        sec_items = get_sec_news(company, cik)
        for item in sec_items:
            uid = item["link"]
            if uid not in sent:
                msg = (
                    f"🇺🇸 <b>[SEC 공시]</b>\n"
                    f"기업: {item['company']}\n"
                    f"내용: {item['title']}\n"
                    f"🔗 {item['link']}"
                )
                send_telegram(msg)
                new_sent.append(uid)

    # 최근 500개만 유지 (파일 무한 증가 방지)
    save_sent(new_sent[-500:])

if __name__ == "__main__":
    main()
