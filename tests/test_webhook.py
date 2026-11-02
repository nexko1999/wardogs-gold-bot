"""웹후크 전송·실패 알림·수집 오류 집계 테스트 (requests/time 모킹, 네트워크 불필요)."""
import os
import sys
import unittest
from unittest import mock

# `python -m unittest discover -s tests` 로 실행해도 저장소 루트의 main.py 를 import 할 수 있게 함
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402

FAKE_URL = "https://discord.example/api/webhooks/123/secret-token"


def make_response(status, json_body=None):
    """지정한 상태 코드를 가진 가짜 응답. 4xx/5xx 면 raise_for_status 가 예외를 냄."""
    res = mock.Mock()
    res.status_code = status
    res.json.return_value = json_body if json_body is not None else {}
    if status >= 400:
        res.raise_for_status.side_effect = RuntimeError(f"HTTP {status}")
    else:
        res.raise_for_status.return_value = None
    return res


@mock.patch("main.WEBHOOK_URL", FAKE_URL)
@mock.patch("main.time.sleep")
@mock.patch("main.requests.post")
class PostWebhookTest(unittest.TestCase):
    def test_success_first_try(self, post, sleep):
        post.return_value = make_response(204)
        main.post_webhook({"a": 1})
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()

    def test_429_then_success(self, post, sleep):
        post.side_effect = [make_response(429, {"retry_after": 0.5}), make_response(204)]
        main.post_webhook({"a": 1})
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(0.5)

    def test_429_retry_after_capped_at_30(self, post, sleep):
        post.side_effect = [make_response(429, {"retry_after": 999}), make_response(204)]
        main.post_webhook({"a": 1})
        sleep.assert_called_once_with(30.0)

    def test_503_three_times_raises(self, post, sleep):
        post.side_effect = [make_response(503) for _ in range(3)]
        with self.assertRaises(RuntimeError) as ctx:
            main.post_webhook({"a": 1})
        self.assertIn("503", str(ctx.exception))
        self.assertEqual(post.call_count, 3)

    def test_network_error_twice_then_success(self, post, sleep):
        post.side_effect = [ConnectionError("x"), TimeoutError("y"), make_response(204)]
        main.post_webhook({"a": 1})
        self.assertEqual(post.call_count, 3)

    def test_network_error_three_times_raises(self, post, sleep):
        post.side_effect = [ConnectionError("a"), ConnectionError("b"), ConnectionError("c")]
        with self.assertRaises(ConnectionError) as ctx:
            main.post_webhook({"a": 1})
        self.assertEqual(str(ctx.exception), "c")
        self.assertEqual(post.call_count, 3)


class MaskSecretTest(unittest.TestCase):
    def test_mask(self):
        with mock.patch("main.WEBHOOK_URL", FAKE_URL):
            self.assertEqual(main.mask_secret(f"error at {FAKE_URL} !"), "error at *** !")

    def test_no_url_configured(self):
        with mock.patch("main.WEBHOOK_URL", ""):
            self.assertEqual(main.mask_secret("plain"), "plain")


class SendFailureNoticeTest(unittest.TestCase):
    def test_swallows_post_error(self):
        with mock.patch("main.WEBHOOK_URL", FAKE_URL), mock.patch(
            "main.post_webhook", side_effect=RuntimeError(f"boom {FAKE_URL}")
        ):
            main.send_failure_notice(RuntimeError("수집 실패"))  # 예외가 새면 테스트 실패

    def test_masks_url_in_payload(self):
        with mock.patch("main.WEBHOOK_URL", FAKE_URL), mock.patch("main.post_webhook") as post:
            main.send_failure_notice(RuntimeError(f"fail {FAKE_URL}"))
        payload = post.call_args[0][0]
        description = payload["embeds"][0]["description"]
        self.assertNotIn(FAKE_URL, description)
        self.assertIn("***", description)


class FetchMarketDataTest(unittest.TestCase):
    def test_all_paths_fail_collects_errors(self):
        session = mock.MagicMock()
        session.__enter__.return_value = session
        session.get.return_value = mock.Mock(status_code=403, text="")
        with mock.patch("main.requests.Session", return_value=session), mock.patch(
            "main.requests.get", return_value=mock.Mock(status_code=500, text="")
        ), mock.patch("main.time.sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                main.fetch_market_data()

        lines = str(ctx.exception).split("\n")[1:]  # 첫 줄은 요약 문구
        self.assertEqual(len(lines), len(main.BROWSER_PROFILES) + len(main.FALLBACK_URLS))
        self.assertEqual(len(lines), 7)
        for profile in main.BROWSER_PROFILES:
            self.assertIn(f"직접 접속({profile}): HTTP 403", lines)
        for name, _ in main.FALLBACK_URLS:
            self.assertIn(f"{name}: HTTP 500", lines)

    def test_http_200_parse_failure_is_distinguished(self):
        session = mock.MagicMock()
        session.__enter__.return_value = session
        session.get.return_value = mock.Mock(status_code=200, text="<html>nothing</html>")
        with mock.patch("main.requests.Session", return_value=session), mock.patch(
            "main.requests.get", side_effect=ConnectionError("down")
        ), mock.patch("main.time.sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                main.fetch_market_data()
        self.assertIn("HTTP 200이지만 시세 파싱 실패", str(ctx.exception))

    def test_success_sets_source(self):
        html = "<div>Current 396,210 7 days -1% 30 days +2% All time +3% vs 30-day average -4%</div>"
        session = mock.MagicMock()
        session.__enter__.return_value = session
        session.get.return_value = mock.Mock(status_code=200, text=html)
        with mock.patch("main.requests.Session", return_value=session), mock.patch("main.time.sleep"):
            data = main.fetch_market_data()
        self.assertEqual(data["current"], "396,210")
        self.assertEqual(data["source"], "직접 접속(chrome)")


if __name__ == "__main__":
    unittest.main()
