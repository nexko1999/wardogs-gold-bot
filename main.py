import os
import re
import sys
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from curl_cffi import requests

MARKET_URL = "https://metaforge.app/wardogs/market"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()


def fetch_market_data() -> dict:
    """MetaForge 워독스 금괴 마켓 페이지에서 시세 데이터를 추출합니다."""
    response = requests.get(
        MARKET_URL,
        impersonate="chrome120",
        timeout=30,
        headers={
            "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
        },
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    full_text = " ".join(soup.get_text(separator=" ").split())

    data = {
        "current": "확인 불가",
        "change_7d": "-",
        "change_30d": "-",
        "change_all": "-",
        "vs_avg_30d": "-",
    }

    # 1차 추출: 텍스트 패턴 매칭 (CSS 클래스가 바뀌어도 안정적으로 동작)
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

    # 2차 보완 추출: tabular-nums 클래스를 가진 span 태그 순서 기반
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


def send_discord_webhook(data: dict) -> None:
    """추출한 금괴 시세를 디스코드 웹후크 Embed 메시지로 전송합니다."""
    if not WEBHOOK_URL:
        raise ValueError("DISCORD_WEBHOOK_URL 환경변수가 설정되지 않았습니다.")

    kst = timezone(timedelta(hours=9))
    now_kst = datetime.now(kst).strftime("%Y-%m-%d %H:%M KST")

    # 30일 평균 대비 낮으면 매수 유리(초록색), 높으면 주의(주황색) 색상 표시
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
