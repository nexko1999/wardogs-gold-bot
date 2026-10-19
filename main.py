import os
import re
import sys
import time
from urllib.parse import quote
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from curl_cffi import requests

MARKET_URL = "https://metaforge.app/wardogs/market"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

# 1차: 직접 접속 시 사용할 브라우저 프로필 목록
BROWSER_PROFILES = ["chrome", "safari", "chrome124", "edge101"]

# 2차: GitHub 데이터센터 IP가 Cloudflare 403에 막혔을 때 사용할 우회 경로 목록
FALLBACK_URLS = [
    ("Jina Reader 우회", f"https://r.jina.ai/{MARKET_URL}"),
    ("CodeTabs 프록시 우회", f"https://api.codetabs.com/v1/proxy?quest={MARKET_URL}"),
    ("AllOrigins 프록시 우회", f"https://api.allorigins.win/raw?url={quote(MARKET_URL, safe='')}"),
]

BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
    "Referer": "https://www.google.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def extract_metrics_from_raw(raw_content: str) -> dict:
    """HTML 또는 텍스트 응답에서 금괴 시세와 변동률을 추출합니다."""
    soup = BeautifulSoup(raw_content, "html.parser")
    full_text = " ".join(soup.get_text(separator=" ").split())

    data = {
        "current": "확인 불가",
        "change_7d": "-",
        "change_30d": "-",
        "change_all": "-",
        "vs_avg_30d": "-",
    }

    patterns = {
        "current": r"Current\s*\$?\s*([0-9,]+)",
        "change_7d": r"7\s*days\s*([+-]?[0-9.]+%)",
        "change_30d": r"30\s*days\s*([+-]?[0-9.]+%)",
        "change_all": r"All\s*time\s*([+-]?[0-9.]+%)",
        "vs_avg_30d": r"vs\s*30-day\s*average\s*([+-]?[0-9.]+%)",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            val = match.group(1).strip()
            data[key] = f"${val}" if key == "current" and not val.startswith("$") else val

    if data["current"] == "확인 불가":
        spans = [
            s.get_text(strip=True)
            for s in soup.select("span.tabular-nums")
            if s.get_text(strip=True)
        ]
        if len(spans) >= 5:
            data["current"] = spans[0]
            data["change_7d"] = spans[1]
            data["change_30d"] = spans[2]
            data["change_all"] = spans[3]
            data["vs_avg_30d"] = spans[4]

    return data


def fetch_market_data() -> dict:
    """여러 브라우저 프로필과 3중 우회 프록시를 순차 시도해 시세 데이터를 가져옵니다."""
    last_error = None

    # 1단계: 직접 접속 시도
    for idx, profile in enumerate(BROWSER_PROFILES, start=1):
        try:
            print(f"[직접 접속 {idx}/{len(BROWSER_PROFILES)}] {profile} 프로필 시도 중...")
            session = requests.Session(impersonate=profile)
            res = session.get(MARKET_URL, headers=BROWSER_HEADERS, timeout=25)
            if res.status_code == 200:
                parsed = extract_metrics_from_raw(res.text)
                if parsed["current"] != "확인 불가":
                    print(f"[성공] {profile} 직접 접속으로 시세 확인: {parsed['current']}")
                    return parsed
            print(f"[안내] {profile} 응답 코드: {res.status_code}")
            last_error = f"HTTP {res.status_code}"
        except Exception as exc:
            print(f"[안내] {profile} 접속 예외: {exc}")
            last_error = str(exc)
        time.sleep(2)

    # 2단계: 우회 경로 순차 시도 (GitHub IP가 Cloudflare 403에 차단된 경우)
    for name, url in FALLBACK_URLS:
        try:
            print(f"[우회 접속] {name} 경로로 시세 조회 시도 중...")
            res = requests.get(
                url,
                impersonate="chrome",
                headers={"Accept": "text/html,text/plain,*/*"},
                timeout=35,
            )
            if res.status_code == 200:
                parsed = extract_metrics_from_raw(res.text)
                if parsed["current"] != "확인 불가":
                    print(f"[성공] {name} 경로로 시세 확인 완료: {parsed['current']}")
                    return parsed
            print(f"[안내] {name} 응답 코드: {res.status_code}")
            last_error = f"{name} HTTP {res.status_code}"
        except Exception as exc:
            print(f"[안내] {name} 접속 예외: {exc}")
            last_error = str(exc)
        time.sleep(2)

    raise RuntimeError(f"모든 접속 및 우회 경로가 실패했습니다 (마지막 오류: {last_error})")


def send_discord_webhook(data: dict) -> None:
    """추출한 금괴 시세를 디스코드 웹후크 Embed 메시지로 전송합니다."""
    if not WEBHOOK_URL:
        raise ValueError("DISCORD_WEBHOOK_URL 환경변수가 설정되지 않았습니다.")

    kst = timezone(timedelta(hours=9))
    now_kst = datetime.now(kst).strftime("%Y-%m-%d %H:%M KST")

    embed_color = 0x10B981 if data["vs_avg_30d"].startswith("-") else 0xF59E0B

    payload = {
        "username": "워독스 금괴 시세 알림봇",
        "embeds": [
            {
                "title": "🪙 WARDOGS 일일 금괴(Gold Bar) 시세 리포트",
                "url": MARKET_URL,
                "description": (
                    f"현재 인게임 금괴 1개당 교환 시세는 **{data['current']}** 입니다.\n"
                    f"데이터 출처: [MetaForge Wardogs Gold Bar Market]({MARKET_URL})"
                ),
                "color": embed_color,
                "fields": [
                    {
                        "name": "💰 현재 시세 (Cash / Bar)",
                        "value": f"**{data['current']}**",
                        "inline": False,
                    },
                    {
                        "name": "📈 7일 변동률",
                        "value": data["change_7d"],
                        "inline": True,
                    },
                    {
                        "name": "📊 30일 변동률",
                        "value": data["change_30d"],
                        "inline": True,
                    },
                    {
                        "name": "⚖️ 30일 평균 대비",
                        "value": data["vs_avg_30d"],
                        "inline": True,
                    },
                    {
                        "name": "🌐 전체 기간 변동률",
                        "value": data["change_all"],
                        "inline": True,
                    },
                ],
                "footer": {
                    "text": f"기준 시각: {now_kst} • Source: metaforge.app/wardogs/market"
                },
            }
        ],
    }

    res = requests.post(WEBHOOK_URL, json=payload, timeout=15)
    res.raise_for_status()
    print(f"[성공] 디스코드 웹후크 전송 완료: {data['current']}")


if __name__ == "__main__":
    try:
        market_info = fetch_market_data()
        print("추출된 시세 데이터:", market_info)
        send_discord_webhook(market_info)
    except Exception as exc:
        print(f"[오류 발생] {exc}", file=sys.stderr)
        sys.exit(1)
