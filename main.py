import argparse
import json
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
    ("CodeTabs 프록시 우회", f"https://api.codetabs.com/v1/proxy?quest={quote(MARKET_URL, safe='')}"),
    ("AllOrigins 프록시 우회", f"https://api.allorigins.win/raw?url={quote(MARKET_URL, safe='')}"),
]

# 우회 경로 요청 헤더: 프록시가 캐시해 둔 오래된 페이지 대신 최신 페이지를 받도록 요청
# (X-No-Cache 는 Jina Reader 전용 헤더, 다른 프록시에는 영향 없음)
FALLBACK_HEADERS = {
    "Accept": "text/html,text/plain,*/*",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "X-No-Cache": "true",
}

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


MISSING_PRICE = "확인 불가"

# 현재가 형식: 숫자(천 단위 쉼표, 소수점 허용). 단위는 인게임 Cash 이므로 통화 기호는 저장하지 않음
PRICE_RE = re.compile(r"[0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?")
# 퍼센트 형식: 선택적 부호 + 숫자 + %
PERCENT_RE = re.compile(r"[+-]?[0-9][0-9,]*(?:\.[0-9]+)?%")
# 마크다운 이미지 태그: ![alt](url)
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def normalize_minus(text: str) -> str:
    """유니코드 마이너스(U+2212)와 en dash(U+2013)를 ASCII '-'로 정규화합니다."""
    return text.replace("\u2212", "-").replace("\u2013", "-")


def is_valid_price(value: str) -> bool:
    """현재가 문자열이 숫자 형식(예: 396,210)인지 검사합니다."""
    return bool(PRICE_RE.fullmatch(value.strip()))


# 변동률 항목: 키 -> 표시용 이름 (누락 안내에 사용)
CHANGE_LABELS = {
    "change_7d": "7일 변동률",
    "change_30d": "30일 변동률",
    "change_all": "전체 기간 변동률",
    "vs_avg_30d": "30일 평균 대비",
}


def missing_change_labels(data: dict) -> list:
    """값이 '-'(파싱 실패)로 남은 변동률 항목의 표시 이름 목록을 돌려줍니다."""
    return [label for key, label in CHANGE_LABELS.items() if not PERCENT_RE.fullmatch(normalize_minus(data[key]).strip())]


def extract_metrics_from_raw(raw_content: str) -> dict:
    """HTML 또는 텍스트 응답에서 금괴 시세와 변동률을 추출합니다."""
    soup = BeautifulSoup(raw_content, "html.parser")
    full_text = normalize_minus(" ".join(soup.get_text(separator=" ").split()))
    # Jina Reader 마크다운 응답은 "Current![Image](...)![Image](...) 396,210"처럼
    # 라벨과 숫자 사이에 이미지 태그가 끼므로 먼저 제거
    full_text = MD_IMAGE_RE.sub(" ", full_text)

    data = {
        "current": MISSING_PRICE,
        "change_7d": "-",
        "change_30d": "-",
        "change_all": "-",
        "vs_avg_30d": "-",
    }

    # PERCENT_RE 와 같은 형식으로 통일
    pct = r"([+-]?[0-9][0-9,]*(?:\.[0-9]+)?%)"
    patterns = {
        "current": r"Current\s*\$?\s*([0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?)",
        # (?<![0-9.]) : "17 days", "130 days", "1.7 days" 같은 오매칭 방지
        "change_7d": r"(?<![0-9.])7\s*days?\s*" + pct,
        "change_30d": r"(?<![0-9.])30[\s-]*days?\s*" + pct,
        "change_all": r"All\s*time\s*" + pct,
        "vs_avg_30d": r"vs\s*30[\s-]*days?\s*average\s*" + pct,
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            val = match.group(1).strip()
            data[key] = val

    if data["current"] == MISSING_PRICE:
        spans = [
            normalize_minus(s.get_text(strip=True))
            for s in soup.select("span.tabular-nums")
            if s.get_text(strip=True)
        ]
        # 실제 페이지는 첫 span이 "Steam online: 90,251" 같은 다른 지표이므로,
        # "가격 1개 + 퍼센트 4개"가 연속으로 나오는 위치를 찾아서만 채택
        for i in range(len(spans) - 4):
            price, pcts = spans[i], spans[i + 1 : i + 5]
            price = price.lstrip("$").strip()  # 텍스트에 $ 가 붙어 와도 숫자만 남김
            if is_valid_price(price) and all(PERCENT_RE.fullmatch(p) for p in pcts):
                data["current"] = price
                data["change_7d"], data["change_30d"], data["change_all"], data["vs_avg_30d"] = pcts
                break

    return data


def fetch_market_data() -> dict:
    """여러 브라우저 프로필과 3중 우회 프록시를 순차 시도해 시세 데이터를 가져옵니다.

    완전한 데이터(현재가 + 변동률 4개)를 준 첫 경로를 채택합니다.
    끝까지 완전한 결과가 없으면 현재가가 유효했던 가장 먼저 얻은 불완전 결과를 사용합니다.
    """
    errors: list[str] = []  # "경로: 오류" 목록
    partial: dict | None = None  # 가장 먼저 얻은 '현재가만 유효한' 불완전 결과

    def evaluate(label: str, text: str):
        """응답을 파싱해 완전하면 결과를 반환, 불완전하면 보관하고 None 반환."""
        nonlocal partial
        parsed = extract_metrics_from_raw(text)
        if not is_valid_price(parsed["current"]):
            print(f"[안내] {label} HTTP 200이지만 시세 파싱 실패")
            errors.append(f"{label}: HTTP 200이지만 시세 파싱 실패")
            return None
        parsed["source"] = label
        parsed["missing"] = missing_change_labels(parsed)
        if not parsed["missing"]:
            print(f"[성공] {label}로 시세 확인: {parsed['current']} Cash")
            return parsed
        print(f"[안내] {label} 현재가는 확인했지만 일부 변동률 누락({', '.join(parsed['missing'])}), 다음 경로 시도")
        errors.append(f"{label}: 일부 변동률 누락({', '.join(parsed['missing'])})")
        if partial is None:
            partial = parsed
        return None

    # 1단계: 직접 접속 시도
    for idx, profile in enumerate(BROWSER_PROFILES, start=1):
        label = f"직접 접속({profile})"
        try:
            print(f"[직접 접속 {idx}/{len(BROWSER_PROFILES)}] {profile} 프로필 시도 중...")
            with requests.Session(impersonate=profile) as session:
                res = session.get(MARKET_URL, headers=BROWSER_HEADERS, timeout=25)
            if res.status_code == 200:
                result = evaluate(label, res.text)
                if result:
                    return result
            else:
                print(f"[안내] {profile} 응답 코드: {res.status_code}")
                errors.append(f"{label}: HTTP {res.status_code}")
        except Exception as exc:
            print(f"[안내] {profile} 접속 예외: {exc}")
            errors.append(f"{label}: {exc}")
        if idx < len(BROWSER_PROFILES):
            time.sleep(2)

    # 2단계: 우회 경로 순차 시도 (GitHub IP가 Cloudflare 403에 차단된 경우)
    for idx, (name, url) in enumerate(FALLBACK_URLS, start=1):
        try:
            print(f"[우회 접속] {name} 경로로 시세 조회 시도 중...")
            res = requests.get(
                url,
                impersonate="chrome",
                headers=FALLBACK_HEADERS,
                timeout=35,
            )
            if res.status_code == 200:
                result = evaluate(name, res.text)
                if result:
                    return result
            else:
                print(f"[안내] {name} 응답 코드: {res.status_code}")
                errors.append(f"{name}: HTTP {res.status_code}")
        except Exception as exc:
            print(f"[안내] {name} 접속 예외: {exc}")
            errors.append(f"{name}: {exc}")
        if idx < len(FALLBACK_URLS):
            time.sleep(2)

    if partial is not None:
        # 현재가는 있으므로 알림은 보내되, GitHub Actions 경고로 누락 항목을 남김
        print(f"::warning::모든 경로에서 완전한 변동률을 얻지 못했습니다. 누락 항목: {', '.join(partial['missing'])} (출처: {partial['source']})")
        return partial

    detail = "\n".join(errors)
    raise RuntimeError(f"모든 접속 및 우회 경로가 실패했습니다\n{detail}")


def pick_embed_color(vs_avg_30d: str) -> int:
    """30일 평균 대비 값으로 임베드 색을 정합니다. 음수=초록, 양수/0=주황, 파싱 불가=회색."""
    text = normalize_minus(vs_avg_30d).strip()
    if not PERCENT_RE.fullmatch(text):
        return 0x6B7280
    value = float(text.rstrip("%").replace(",", ""))
    return 0x10B981 if value < 0 else 0xF59E0B


def post_webhook(payload: dict, max_attempts: int = 3) -> None:
    """웹후크로 전송합니다. 429(retry_after 준수, 상한 30초)·5xx·네트워크 예외는 재시도합니다."""
    for attempt in range(1, max_attempts + 1):
        is_last = attempt == max_attempts
        try:
            res = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        except Exception as exc:
            if is_last:
                raise
            print(f"[안내] 디스코드 전송 예외, 재시도 ({attempt}/{max_attempts}): {mask_secret(str(exc))}")
            time.sleep(2)
            continue
        if not is_last:
            if res.status_code == 429:
                try:
                    wait = float(res.json().get("retry_after", 1))
                except Exception:
                    wait = 1.0
                wait = min(max(wait, 0.0), 30.0)
                print(f"[안내] 디스코드 429 응답, {wait:.1f}초 후 재시도 ({attempt}/{max_attempts})")
                time.sleep(wait)
                continue
            if 500 <= res.status_code < 600:
                print(f"[안내] 디스코드 {res.status_code} 응답, 재시도 ({attempt}/{max_attempts})")
                time.sleep(2)
                continue
        res.raise_for_status()
        return


def mask_secret(text: str) -> str:
    """문자열에 웹후크 URL이 포함되어 있으면 마스킹합니다."""
    if WEBHOOK_URL:
        text = text.replace(WEBHOOK_URL, "***")
    return text


def send_failure_notice(error: Exception) -> None:
    """수집 실패 알림을 디스코드로 보냅니다(best-effort, 전송 예외는 삼킴)."""
    try:
        summary = mask_secret(str(error))[:1000] or error.__class__.__name__
        description = f"시세 수집에 실패했습니다.\n```\n{summary}\n```"
        server = os.environ.get("GITHUB_SERVER_URL")
        repo = os.environ.get("GITHUB_REPOSITORY")
        run_id = os.environ.get("GITHUB_RUN_ID")
        if server and repo and run_id:
            description += f"\n[Actions 실행 로그]({server}/{repo}/actions/runs/{run_id})"
        payload = {
            "username": "워독스 금괴 시세 알림봇",
            "embeds": [
                {
                    "title": "⚠️ WARDOGS 금괴 시세 수집 실패",
                    "description": description,
                    "color": 0xEF4444,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ],
        }
        post_webhook(payload)
    except Exception as exc:
        print(f"[안내] 실패 알림 전송 실패: {mask_secret(str(exc))}", file=sys.stderr)


def build_payload(data: dict) -> dict:
    """추출한 금괴 시세로 디스코드 웹후크 Embed payload를 만듭니다(전송은 하지 않음)."""
    kst = timezone(timedelta(hours=9))
    now_kst = datetime.now(kst).strftime("%Y-%m-%d %H:%M KST")

    embed_color = pick_embed_color(data["vs_avg_30d"])
    source = data.get("source", "알 수 없음")
    footer_text = f"기준 시각: {now_kst} • 수집 경로: {source} • Source: metaforge.app/wardogs/market"
    missing = data.get("missing") or []
    if missing:
        footer_text = f"일부 변동률 확인 불가: {', '.join(missing)}\n" + footer_text

    payload = {
        "username": "워독스 금괴 시세 알림봇",
        "embeds": [
            {
                "title": "🪙 WARDOGS 일일 금괴(Gold Bar) 시세 리포트",
                "url": MARKET_URL,
                "description": (
                    f"현재 인게임 금괴 1개당 교환 시세는 **{data['current']} Cash** 입니다.\n"
                    f"데이터 출처: [MetaForge Wardogs Gold Bar Market]({MARKET_URL})"
                ),
                "color": embed_color,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "fields": [
                    {
                        "name": "💰 현재 시세 (Cash / Bar)",
                        "value": f"**{data['current']} Cash**",
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
                "footer": {"text": footer_text},
            }
        ],
    }

    return payload


def send_discord_webhook(data: dict) -> None:
    """추출한 금괴 시세를 디스코드 웹후크 Embed 메시지로 전송합니다."""
    if not WEBHOOK_URL:
        raise ValueError("DISCORD_WEBHOOK_URL 환경변수가 설정되지 않았습니다.")

    post_webhook(build_payload(data))
    print(f"[성공] 디스코드 웹후크 전송 완료: {data['current']} Cash")


def main(argv=None) -> int:
    """진입점. 성공 0, 수집 실패·전송 실패 1. --dry-run 이면 전송 없이 payload만 출력합니다."""
    parser = argparse.ArgumentParser(
        allow_abbrev=False,  # "--dry" 같은 약어 허용 안 함
        description="Wardogs 금괴 시세를 수집해 디스코드 웹후크로 전송합니다.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="디스코드로 전송하지 않고 payload만 콘솔에 출력합니다.",
    )
    # 알 수 없는 인자(오타 등)는 argparse 가 에러 출력 후 exit 2 로 종료 (수집·전송 시도 없음)
    dry_run = parser.parse_args(argv).dry_run

    # 웹후크 URL 검증은 수집 이전에 먼저 수행 (dry-run 은 URL 불필요)
    if not dry_run and not WEBHOOK_URL:
        print("[오류 발생] DISCORD_WEBHOOK_URL 환경변수가 설정되지 않았습니다.", file=sys.stderr)
        return 1

    try:
        market_info = fetch_market_data()
    except Exception as exc:
        print(f"[오류 발생] {mask_secret(str(exc))}", file=sys.stderr)
        if not dry_run:
            send_failure_notice(exc)
        return 1

    try:
        print("추출된 시세 데이터:", market_info)
        if dry_run:
            print("[확인 모드] 디스코드로 전송하지 않고 payload만 출력합니다.")
            print(json.dumps(build_payload(market_info), ensure_ascii=False, indent=2))
        else:
            send_discord_webhook(market_info)
    except Exception as exc:
        print(f"[오류 발생] {mask_secret(str(exc))}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    # Windows 콘솔(cp949)에서도 이모지·한글 payload 출력이 깨지지 않게 함
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(main())
