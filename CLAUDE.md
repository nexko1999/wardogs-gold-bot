# CLAUDE.md

이 저장소에서 작업하는 Claude 세션(PC·클라우드 공통)이 따를 규칙입니다.

## 언어
- 모든 답변, 설명, 코드 주석, 커밋 메시지, 요약은 반드시 한국어로 작성한다.
- 코드 자체(변수명·함수명·명령어), 파일 경로, 에러 메시지 원문, 고유명사는 원래 표기를 유지한다.
- 서브에이전트에 작업을 맡길 때도 결과를 한국어로 보고하도록 지시한다.

## 사용자
- 저장소 소유자(GitHub `nexko1999`)는 비개발자다. 한 단계씩 쉬운 말로 안내한다.
- "앞으로 문제가 생기면 안 된다"를 중시한다. 변경 후에는 테스트 통과와 실제 사이트 응답을 확인한다.

## 프로젝트 개요
- `main.py`가 `https://metaforge.app/wardogs/market`에서 Wardogs 금괴 시세(인게임 Cash 단위, 달러 아님)를 수집해 디스코드 웹후크로 보낸다.
- 실행: 주 실행은 cron-job.org 가 KST 10:00·10:30 에 `workflow_dispatch` API 호출(사용자 요구: 알림은 반드시 KST 10~11시). 워크플로 첫 단계가 '오늘(KST) 전송 성공 실행(run-name `금괴 시세 일일 전송`)'이 있으면 건너뛰어 중복을 막는다. run-name 을 바꾸면 중복 방지가 깨지니 주의. 비상용 GitHub schedule 은 설정 KST 10:07(cron `7 1 * * *`)이지만 GitHub 예약 실행 지연(원인 추정)으로 실제 도착은 보통 오후 4~6시경(UTC 07~09시대)이며 시각은 보장되지 않는다. 수동 실행은 Actions 탭에서 `workflow_dispatch`로 한다.
- CI: `.github/workflows/ci.yml`이 push·PR마다 단위 테스트를 돌린다.
- 자세한 동작(직접 접속 → 우회 프록시 순서, 실패 알림, keepalive)은 `README.md`를 본다.

## 테스트
```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```
- `tests/test_parse.py`: 파싱, `tests/test_webhook.py`: 웹후크 전송, `tests/test_main.py`: 종료 코드·`--dry-run`·부분 누락 처리

## 확인 모드 (디스코드로 보내지 않고 확인)
```bash
python main.py --dry-run
```
- `DISCORD_WEBHOOK_URL` 없이 동작하며, 수집·파싱 후 보낼 payload(JSON)를 콘솔에 출력만 한다. 웹후크·실패 알림은 전송하지 않는다.
- 수집 성공 종료 코드 0, 실패 1.
- GitHub 서버에서 전송 없이 확인: `gh workflow run daily.yml -f dry_run=true` (Actions 탭의 '확인 모드' 체크와 동일). 예약·외부 실행은 항상 일반 전송(오늘 이미 보냈으면 건너뜀). `-f force=true` 는 중복 방지를 무시하고 전송.

## 파싱 시 주의
- 페이지의 첫 `span.tabular-nums`는 "Steam online: N"이다. 시세가 아니다.
- Jina Reader 응답에서는 `Current`와 숫자 사이에 마크다운 이미지 태그가 끼어 있다.
- 파싱 로직을 바꾸기 전에 실제 페이지 구조를 먼저 확인한다.

## 보안
- 웹후크 URL은 GitHub Secret `DISCORD_WEBHOOK_URL`로만 다룬다. 코드, 로그, 커밋에 절대 넣지 않는다.

## 장애 대응 방침 (사용자와 합의, 2026-11-02)
- 나중에 문제가 생기면 땜질 수정 대신, "metaforge에서 시세 수집 → 디스코드 웹후크 전송"이라는 뼈대만 남기고 새로 다시 만든다.
- 착수 전에 실제 사이트 구조부터 다시 확인한다.
- 파일 삭제, force push 같은 되돌리기 어려운 작업은 실행 전에 반드시 사용자에게 확인받는다.

## 여러 세션에서 작업할 때
- 이 저장소는 PC 세션과 모바일(클라우드) 세션 양쪽에서 수정될 수 있다. 작업 전에 `git pull`로 최신 상태를 받는다.
- 변경은 main에 직접 push하기 전에 사용자에게 먼저 확인받는다.
