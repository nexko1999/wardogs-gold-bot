# wardogs-gold-bot

metaforge.app 의 Wardogs 금괴(Gold Bar) 시장 시세를 매일 스크래핑해 디스코드 웹후크로 전송하는 봇입니다. GitHub Actions 에서 `main.py` 를 하루 한 번 실행합니다.

## 동작 흐름

1. **직접 접속**: `curl_cffi` 로 여러 브라우저 프로필(chrome, safari, chrome124, edge101)을 흉내 내어 `https://metaforge.app/wardogs/market` 에 접속합니다.
2. **우회 프록시**: 직접 접속이 모두 실패하면(예: GitHub 데이터센터 IP 의 Cloudflare 403) Jina Reader, CodeTabs, AllOrigins 를 차례로 시도합니다. 2026-11-02 점검 시 CodeTabs/AllOrigins 두 서비스 모두 오류 응답(522/500)이 관찰되었습니다. 우회 요청에는 캐시 방지 헤더(`Cache-Control: no-cache`, Jina 전용 `X-No-Cache: true`)를 붙여 프록시에 캐시된 오래된 페이지 대신 최신 페이지를 받도록 합니다.
3. 응답에서 현재 시세, 7일/30일/전체 기간 변동률, 30일 평균 대비 값을 추출해 디스코드 Embed 로 전송합니다. 푸터에 수집 경로(직접 접속 또는 어떤 우회 경로인지)가 표시됩니다.
4. 모든 경로가 실패하면 경로별 오류를 모아 디스코드에 실패 알림(빨간색 Embed, Actions 실행 링크 포함)을 보내고 종료 코드 1 로 끝납니다.

### 시세 단위

현재가는 달러가 아니라 **인게임 Cash 단위**입니다(금괴 1개당 교환 시세). 예: `396,210 Cash`.

## Secret 설정

1. 디스코드 채널 설정 > 연동 > 웹후크에서 웹후크 URL 을 만듭니다.
2. GitHub 저장소의 **Settings > Secrets and variables > Actions > New repository secret** 로 이동합니다.
3. 이름을 `DISCORD_WEBHOOK_URL`, 값을 웹후크 URL 로 저장합니다.

## 스케줄

- 매일 **KST 10:07** (UTC 01:07) 에 자동 실행됩니다. (`.github/workflows/daily.yml` 의 cron `7 1 * * *`)
- 정각 시각의 실행 지연을 피하려고 일부러 7분을 어긋나게 두었습니다. GitHub 예약 실행은 몇 분 지연될 수 있습니다.

## 수동 실행

저장소의 **Actions** 탭에서 `Daily Wardogs Gold Price Webhook` 워크플로를 선택하고 **Run workflow** 버튼(`workflow_dispatch`)을 누르면 즉시 실행됩니다.

## 로컬 실행

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."   # Windows PowerShell: $env:DISCORD_WEBHOOK_URL="..."
python main.py
```

## 테스트

네트워크 없이 파싱 로직만 검증하는 단위 테스트(`tests/test_parse.py`)가 있습니다.

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

## CI 와 의존성 업데이트

- `.github/workflows/ci.yml`: main 브랜치 push 와 pull_request 에서 위 단위 테스트를 실행합니다(같은 ref 의 이전 실행은 취소).
- `requirements.txt` 는 하위 의존성까지 정확한 버전으로 고정되어 있습니다.
- `.github/dependabot.yml`: pip 와 github-actions 의존성을 매월(monthly) 점검해 업데이트 PR 을 만듭니다. 이 PR 은 CI 테스트로 검증됩니다. 단, 테스트는 합성 픽스처 기준이라 실제 사이트 변화나 `curl_cffi` 의 접속 동작 변화까지는 잡지 못하므로, 병합 후 수동 실행으로 한 번 확인하는 것을 권장합니다.

## 주의사항

- GitHub 는 저장소에 **60일간 활동이 없으면 예약(schedule) 워크플로를 자동 비활성화**합니다. 이를 늦추려고 daily 워크플로에 별도 `keepalive` job(`actions: write` 권한은 이 job 에만 부여, `gh api -X PUT .../workflows/daily.yml/enable`)을 두었지만, enable API 호출이 60일 타이머를 늦춘다는 것은 공식 문서로 보장되지 않는 best-effort 조치입니다. keepalive 가 실패해도 워크플로 결과는 수집 job 결과만 따릅니다. 비활성화되었다면 Actions 탭에서 워크플로를 다시 활성화해 주세요.
- 우회 경로는 **제3자 프록시**(r.jina.ai, api.codetabs.com, api.allorigins.win)에 의존합니다. 해당 서비스의 중단, 제한, 응답 변조 가능성이 있습니다.
- 사이트 HTML 구조나 문구가 바뀌면 **파싱이 실패**할 수 있습니다. 이 경우 실패 알림이 오며 `main.py` 의 `extract_metrics_from_raw` 를 수정해야 합니다.
