import unittest

from glasstest.phone_operations import feature_tap_point, home_can_scroll


def _node(text, rid, bounds, children=""):
    return (
        f'<node text="{text}" resource-id="{rid}" bounds="{bounds}">{children}</node>'
    )


class FeatureTapTests(unittest.TestCase):
    def test_ignores_home_capture_button(self):
        xml = (
            "<hierarchy>"
            + _node("录音", "", "[825,1367][891,1418]")
            + _node("", "com.moyoung.glasses:id/home_scroll_view", "[0,0][1080,2340]")
            + "</hierarchy>"
        )
        self.assertIsNone(feature_tap_point(xml, "录音"))
        self.assertTrue(home_can_scroll(xml))

    def test_ignores_home_capture_even_when_discovery_missing(self):
        # Only the home capture「录音」(no tv_feature) — must not tap it.
        xml = (
            "<hierarchy>"
            + _node("录音", "", "[553,926][599,956]")
            + _node("", "com.moyoung.glasses:id/home_scroll_view", "[0,0][720,1600]")
            + "</hierarchy>"
        )
        self.assertIsNone(feature_tap_point(xml, "录音"))

    def test_taps_visible_discovery_label(self):
        xml = (
            "<hierarchy>"
            + _node("录音", "", "[825,1367][891,1418]")
            + _node(
                "录音",
                "com.moyoung.glasses:id/tv_feature",
                "[80,1500][320,1720]",
            )
            + "</hierarchy>"
        )
        self.assertEqual(feature_tap_point(xml, "录音"), (200, 1610))

    def test_taps_short_redmi_discovery_label(self):
        # Real Xiaomi 720x1600 dump: tv_feature「录音」height is 32px.
        xml = (
            '<hierarchy bounds="[0,0][720,1600]">'
            + _node("录音", "", "[553,373][599,403]")
            + _node(
                "录音",
                "com.moyoung.glasses:id/tv_feature",
                "[103,1111][149,1143]",
            )
            + "</hierarchy>"
        )
        self.assertEqual(feature_tap_point(xml, "录音"), (126, 1127))


if __name__ == "__main__":
    unittest.main()
