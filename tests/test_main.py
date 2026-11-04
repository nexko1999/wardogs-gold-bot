"""main() 종료 코드, --dry-run, 부분 누락 처리, 우회 경로, 웹후크 재시도 경계 테스트 (네트워크 모킹)."""
import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

# `python -m unittest discover -s tests` 로 실행해도 저장소 루트의 main.py 를 import 할 수 있게 함
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402

FAKE_URL = "https://discord.example/api/webhooks/123/secret-token"
COMPLETE = "<div>Current 396,210 7 days -1% 30 days +2% All time +3% vs 30-day average -4%</div>"
PARTIAL = "<div>Current 396,210 7 days -1% All time +3%</div>"  # 30일 변동률·30일 평균 대비 누락
PARTIAL_OTHER = "<div>Current 111,111 7 days -9%</div>"  # 더 뒤에 나온 불완전 결과(채택되면 안 됨)


def make_response(item):
    """int 면 해당 HTTP 상태(본문 없음), str 이면 200 + 본문인 가짜 GET 응답."""
    if isinstance(item, str):
        return mock.Mock(status_code=200, text=item)
    return mock.Mock(status_code=item, text="")


def make_session(*items):
    """직접 접속용 Session 모킹. 호출 순서대로 items 를 응답으로 돌려줌."""
    session = mock.MagicMock()
    session.__enter__.return_value = session
    session.get.side_effect = [make_response(i) for i in items]
    return session


def make_post_response(status):
    """웹후크 POST 가짜 응답. 4xx/5xx 면 raise_for_status 가 예외를 냄."""
    res = mock.Mock()
    res.status_code = status
    res.json.return_value = {}
    if status >= 400:
        res.raise_for_status.side_effect = RuntimeError(f"HTTP {status}")
    return res


def complete_data():
    data = main.extract_metrics_from_raw(COMPLETE)
    data.update(source="t", missing=[])
    return data


def run_main(argv):
    """main() 을 실행해 (종료 코드, stdout, stderr) 를 돌려줌."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main.main(argv)
    return code, out.getvalue(), err.getvalue()


@mock.patch("main.time.sleep")
class FetchPartialTest(unittest.TestCase):
    def test_incomplete_then_complete_from_next_source(self, sleep):
        session = make_session(PARTIAL, COMPLETE)
        with mock.patch("main.requests.Session", return_value=session):
            data = main.fetch_market_data()
        self.assertEqual(data["source"], "직접 접속(safari)")
        self.assertEqual(data["missing"], [])
        self.assertEqual(data["change_30d"], "+2%")

    def test_all_incomplete_uses_first_partial_with_warning(self, sleep):
        # 첫 소스는 PARTIAL, 이후 모든 소스는 PARTIAL_OTHER -> 가장 먼저 얻은 PARTIAL 채택
        session = make_session(PARTIAL, PARTIAL_OTHER, PARTIAL_OTHER, PARTIAL_OTHER)
        out = io.StringIO()
        with mock.patch("main.requests.Session", return_value=session), mock.patch(
            "main.requests.get", return_value=make_response(PARTIAL_OTHER)
        ), contextlib.redirect_stdout(out):
            data = main.fetch_market_data()
        self.assertEqual(data["current"], "396,210")
        self.assertEqual(data["source"], "직접 접속(chrome)")
        self.assertEqual(data["missing"], ["30일 변동률", "30일 평균 대비"])
        warnings = [line for line in out.getvalue().splitlines() if line.startswith("::warning::")]
        self.assertEqual(len(warnings), 1)
        self.assertIn("30일 변동률", warnings[0])
        self.assertIn("30일 평균 대비", warnings[0])

    def test_fallback_proxy_success(self, sleep):
        # 직접 접속 4개는 모두 403, Jina 우회로 완전한 데이터 확보
        session = make_session(403, 403, 403, 403)
        with mock.patch("main.requests.Session", return_value=session), mock.patch(
            "main.requests.get", return_value=make_response(COMPLETE)
        ) as get:
            data = main.fetch_market_data()
        self.assertEqual(data["source"], "Jina Reader 우회")
        self.assertEqual(data["current"], "396,210")
        self.assertEqual(data["missing"], [])
        self.assertEqual(get.call_count, 1)

    def test_no_price_still_fails(self, sleep):
        session = make_session("<html>nothing</html>", "x", "x", "x")
        with mock.patch("main.requests.Session", return_value=session), mock.patch(
            "main.requests.get", return_value=make_response("nothing")
        ):
            with self.assertRaises(RuntimeError):
                main.fetch_market_data()


class BuildPayloadTest(unittest.TestCase):
    def test_complete_has_no_missing_note(self):
        footer = main.build_payload(complete_data())["embeds"][0]["footer"]["text"]
        self.assertNotIn("확인 불가", footer)

    def test_partial_footer_note(self):
        data = main.extract_metrics_from_raw(PARTIAL)
        data["source"] = "테스트"
        data["missing"] = main.missing_change_labels(data)
        footer = main.build_payload(data)["embeds"][0]["footer"]["text"]
        self.assertIn("일부 변동률 확인 불가: 30일 변동률, 30일 평균 대비", footer)
        self.assertIn("수집 경로: 테스트", footer)


@mock.patch("main.WEBHOOK_URL", FAKE_URL)
class MainExitCodeTest(unittest.TestCase):
    def test_success_returns_0(self):
        with mock.patch("main.fetch_market_data", return_value=complete_data()), mock.patch(
            "main.requests.post", return_value=make_post_response(204)
        ) as post:
            code, _, _ = run_main([])
        self.assertEqual(code, 0)
        self.assertEqual(post.call_count, 1)

    def test_fetch_failure_returns_1_and_sends_notice(self):
        with mock.patch("main.fetch_market_data", side_effect=RuntimeError("실패")), mock.patch(
            "main.requests.post", return_value=make_post_response(204)
        ) as post:
            code, _, _ = run_main([])
        self.assertEqual(code, 1)
        self.assertEqual(post.call_count, 1)  # 실패 알림 1회

    def test_webhook_failure_returns_1(self):
        with mock.patch("main.fetch_market_data", return_value=complete_data()), mock.patch(
            "main.requests.post", return_value=make_post_response(404)
        ), mock.patch("main.time.sleep"):
            code, _, _ = run_main([])
        self.assertEqual(code, 1)

    def test_missing_webhook_url_returns_1(self):
        with mock.patch("main.WEBHOOK_URL", ""), mock.patch("main.fetch_market_data") as fetch:
            code, _, _ = run_main([])
        self.assertEqual(code, 1)
        fetch.assert_not_called()

    def test_partial_result_still_exit_0(self):
        data = main.extract_metrics_from_raw(PARTIAL)
        data.update(source="t", missing=["30일 변동률", "30일 평균 대비"])
        with mock.patch("main.fetch_market_data", return_value=data), mock.patch(
            "main.requests.post", return_value=make_post_response(204)
        ) as post:
            code, _, _ = run_main([])
        self.assertEqual(code, 0)
        footer = post.call_args.kwargs["json"]["embeds"][0]["footer"]["text"]
        self.assertIn("일부 변동률 확인 불가", footer)


class DryRunTest(unittest.TestCase):
    def test_dry_run_prints_payload_without_webhook(self):
        # 웹후크 URL 이 없어도 동작해야 함
        with mock.patch("main.WEBHOOK_URL", ""), mock.patch(
            "main.fetch_market_data", return_value=complete_data()
        ), mock.patch("main.requests.post") as post:
            code, out, _ = run_main(["--dry-run"])
        self.assertEqual(code, 0)
        post.assert_not_called()
        # 출력의 JSON 블록(줄 첫머리 '{' 부터 끝까지)을 파싱해 payload 확인
        payload = json.loads(out[out.index("\n{") + 1:])
        self.assertEqual(payload["embeds"][0]["fields"][0]["value"], "**396,210 Cash**")
        self.assertIn("워독스", out)  # ensure_ascii=False

    def test_dry_run_failure_exit_1_without_notice(self):
        with mock.patch("main.WEBHOOK_URL", FAKE_URL), mock.patch(
            "main.fetch_market_data", side_effect=RuntimeError("실패")
        ), mock.patch("main.requests.post") as post:
            code, _, _ = run_main(["--dry-run"])
        self.assertEqual(code, 1)
        post.assert_not_called()  # 실패 알림도 보내지 않음


class UnknownArgTest(unittest.TestCase):
    def test_typo_arg_exits_2_without_fetch_or_post(self):
        with mock.patch("main.WEBHOOK_URL", FAKE_URL), mock.patch(
            "main.fetch_market_data"
        ) as fetch, mock.patch("main.requests.post") as post:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as ctx:
                    main.main(["--dryrun"])
        self.assertEqual(ctx.exception.code, 2)
        fetch.assert_not_called()
        post.assert_not_called()


@mock.patch("main.WEBHOOK_URL", FAKE_URL)
@mock.patch("main.time.sleep")
@mock.patch("main.requests.post")
class WebhookStatusBoundaryTest(unittest.TestCase):
    def test_404_fails_immediately_without_retry(self, post, sleep):
        post.return_value = make_post_response(404)
        with self.assertRaises(RuntimeError):
            main.post_webhook({"a": 1})
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()

    def test_429_on_last_attempt_raises(self, post, sleep):
        post.side_effect = [make_post_response(429) for _ in range(3)]
        with self.assertRaises(RuntimeError) as ctx:
            main.post_webhook({"a": 1})
        self.assertIn("429", str(ctx.exception))
        self.assertEqual(post.call_count, 3)
        self.assertEqual(sleep.call_count, 2)  # 마지막 시도 뒤에는 대기하지 않음


if __name__ == "__main__":
    unittest.main()
