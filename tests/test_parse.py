"""파싱 로직 단위 테스트 (외부 네트워크 불필요, 합성 픽스처 사용)."""
import os
import sys
import unittest

# `python -m unittest discover -s tests` 로 실행해도 저장소 루트의 main.py 를 import 할 수 있게 함
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


class ExtractMetricsTest(unittest.TestCase):
    def assert_metrics(self, data, current, d7, d30, all_time, vs_avg):
        self.assertEqual(data["current"], current)
        self.assertEqual(data["change_7d"], d7)
        self.assertEqual(data["change_30d"], d30)
        self.assertEqual(data["change_all"], all_time)
        self.assertEqual(data["vs_avg_30d"], vs_avg)

    def test_html_text(self):
        html = (
            "<html><body><div>Current 396,210 7 days -42.94% 30 days +138.68% "
            "All time +12.37% vs 30-day average -26.60% Cash per gold bar</div></body></html>"
        )
        data = main.extract_metrics_from_raw(html)
        self.assert_metrics(data, "396,210", "-42.94%", "+138.68%", "+12.37%", "-26.60%")

    def test_jina_markdown(self):
        md = (
            "Current![Image 25](https://x/gold.webp)![Image 26](https://x/cash.webp) 396,210\n\n"
            "7 days-42.94%\n\n30 days+138.68%\n\nAll time+12.37%\n\nvs 30-day average-26.60%"
        )
        data = main.extract_metrics_from_raw(md)
        self.assert_metrics(data, "396,210", "-42.94%", "+138.68%", "+12.37%", "-26.60%")

    def test_span_fallback(self):
        html = (
            '<span class="tabular-nums">Steam online: 90,251</span>'
            '<span class="tabular-nums">$396,210</span>'
            '<span class="tabular-nums">-42.94%</span>'
            '<span class="tabular-nums">+138.68%</span>'
            '<span class="tabular-nums">+12.37%</span>'
            '<span class="tabular-nums">-26.60%</span>'
            '<span class="tabular-nums">27 cosmetics</span>'
        )
        data = main.extract_metrics_from_raw(html)
        self.assert_metrics(data, "396,210", "-42.94%", "+138.68%", "+12.37%", "-26.60%")

    def test_span_fallback_rejects_non_matching(self):
        html = (
            '<span class="tabular-nums">Steam online: 90,251</span>'
            '<span class="tabular-nums">27 cosmetics</span>'
        )
        self.assertEqual(main.extract_metrics_from_raw(html)["current"], main.MISSING_PRICE)

    def test_unicode_minus_and_en_dash(self):
        text = "Current 1,000 7 days −1.50% 30 days –2.25% All time +3% vs 30 day average −0.5%"
        data = main.extract_metrics_from_raw(text)
        self.assert_metrics(data, "1,000", "-1.50%", "-2.25%", "+3%", "-0.5%")

    def test_dollar_prefix_stripped(self):
        data = main.extract_metrics_from_raw("Current $1,234.5 7 days +1%")
        self.assertEqual(data["current"], "1,234.5")

    def test_days_not_mismatched(self):
        text = "Current 10 17 days +9% 130 days +8% 7 days +1%"
        self.assertEqual(main.extract_metrics_from_raw(text)["change_7d"], "+1%")

    def test_proxy_error_page(self):
        html = "<html><body><h1>502 Bad Gateway</h1><p>Retry in 7 days.</p></body></html>"
        data = main.extract_metrics_from_raw(html)
        self.assertEqual(data["current"], main.MISSING_PRICE)
        self.assertEqual(data["change_7d"], "-")

    def test_comma_only_not_matched(self):
        data = main.extract_metrics_from_raw("Current , 7 days +1%")
        self.assertEqual(data["current"], main.MISSING_PRICE)


class ValidatorTest(unittest.TestCase):
    def test_is_valid_price(self):
        for ok in ["396,210", "1234", "1,234.56"]:
            self.assertTrue(main.is_valid_price(ok), ok)
        for bad in ["", "$", "$396,210", "1,234,", "abc", main.MISSING_PRICE, "-"]:
            self.assertFalse(main.is_valid_price(bad), bad)

    def test_pick_embed_color(self):
        self.assertEqual(main.pick_embed_color("-26.60%"), 0x10B981)
        self.assertEqual(main.pick_embed_color("−1.0%"), 0x10B981)
        self.assertEqual(main.pick_embed_color("+5%"), 0xF59E0B)
        self.assertEqual(main.pick_embed_color("0%"), 0xF59E0B)
        self.assertEqual(main.pick_embed_color("-"), 0x6B7280)


if __name__ == "__main__":
    unittest.main()
