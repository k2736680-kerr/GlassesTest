import os
import unittest

from glasstest.device_config_api import soft_version_from_mcu_firmware
from glasstest.run_resolution_check import _case_detail, _video_process_evidence
from glasstest.media_measure import sizes_match
from glasstest.resolution_spec import (
    expected_photo_size,
    expected_video_size,
    resolve_chip,
    resolve_pixel_key,
)


class ResolutionSpecTests(unittest.TestCase):
    def test_a1v_800_to_3200_photo_interp(self):
        cfg = {
            "soft_version_name": "A1V",
            "video_type": "standard_video",
            "camera": {"value": "800", "pixels": "800", "size": "3280x2464"},
        }
        self.assertEqual(expected_photo_size(cfg, "landscape"), (6560, 4928))
        self.assertEqual(expected_photo_size(cfg, "portrait"), (3696, 4928))
        self.assertEqual(expected_video_size(cfg, "landscape"), (1600, 1200))
        self.assertEqual(expected_video_size(cfg, "portrait"), (1440, 1920))

    def test_a09_800_stays_native_photo(self):
        cfg = {
            "soft_version_name": "A09",
            "video_type": "standard_video",
            "camera": {"value": "800", "size": "3280x2464"},
        }
        self.assertEqual(expected_photo_size(cfg, "landscape"), (3280, 2464))
        self.assertEqual(expected_photo_size(cfg, "portrait"), (1848, 2464))

    def test_allwinner_800_from_video_type(self):
        cfg = {"video_type": "standard_video", "camera": {"value": "800", "size": "3280x2464"}}
        self.assertEqual(resolve_chip(cfg)["chip"], "allwinner")
        self.assertEqual(resolve_chip(cfg)["source"], "video_type")
        self.assertEqual(resolve_pixel_key(cfg)["pixel_key"], "800")
        self.assertEqual(expected_video_size(cfg, "landscape"), (1600, 1200))
        self.assertEqual(expected_video_size(cfg, "portrait"), (1440, 1920))
        self.assertEqual(expected_photo_size(cfg, "landscape"), (3280, 2464))
        self.assertEqual(expected_photo_size(cfg, "portrait"), (1848, 2464))

    def test_bk_800_from_video_type(self):
        cfg = {"video_type": "bk_video", "camera": {"value": "800"}}
        self.assertTrue(resolve_chip(cfg)["is_bk"])
        self.assertEqual(expected_video_size(cfg, "landscape"), (1280, 720))
        self.assertEqual(expected_video_size(cfg, "portrait"), (816, 1088))
        self.assertEqual(expected_photo_size(cfg, "landscape"), (3280, 2464))

    def test_bk_from_camera_value_prefix(self):
        cfg = {"video_type": "", "camera": {"value": "bk800"}}
        self.assertEqual(resolve_chip(cfg)["source"], "camera.value")
        self.assertTrue(resolve_chip(cfg)["is_bk"])
        self.assertEqual(expected_video_size(cfg, "landscape"), (1280, 720))

    def test_empty_api_falls_back_to_table(self):
        cfg = {"video_type": "", "camera": {}}
        chip = resolve_chip(cfg)
        self.assertEqual(chip["chip"], "allwinner")
        self.assertEqual(chip["source"], "table_default")
        self.assertEqual(resolve_pixel_key(cfg)["source"], "table_default")
        self.assertEqual(expected_video_size(cfg, "landscape"), (1600, 1200))

    def test_value_beats_pixels(self):
        cfg = {"video_type": "", "camera": {"value": "1300", "pixels": "800", "size": "4160x3120"}}
        self.assertEqual(resolve_pixel_key(cfg)["pixel_key"], "1300")
        self.assertEqual(expected_video_size(cfg, "landscape"), (1920, 1440))
        self.assertEqual(expected_photo_size(cfg, "landscape"), (4160, 3120))

    def test_photo_api_size_overrides_table(self):
        cfg = {"camera": {"value": "1200", "size": "4032x3024"}}
        self.assertEqual(expected_photo_size(cfg, "landscape"), (4032, 3024))
        self.assertEqual(expected_photo_size(cfg, "portrait"), (2329, 3120))

    def test_video_custom_diff_overrides_table(self):
        cfg = {
            "video_type": "bk_video",
            "bk_video_diff": "1111x222",
            "bk_video_diff_portrait": "333x444",
            "camera": {"value": "bk800"},
        }
        self.assertEqual(expected_video_size(cfg, "landscape"), (1111, 222))
        self.assertEqual(expected_video_size(cfg, "portrait"), (333, 444))

    def test_mcu_firmware_to_soft_version(self):
        self.assertEqual(soft_version_from_mcu_firmware("MOY-A093-0.1.9"), "A09")
        self.assertEqual(soft_version_from_mcu_firmware("MOY-A1V3-0.2.5"), "A1V")
        self.assertEqual(soft_version_from_mcu_firmware(""), "")

    def test_video_mismatch_evidence_is_visible_in_report(self):
        log_path = os.path.join(
            "tmp",
            "20261110_140910_分辨率检查",
            "日志",
            "20261110_141031_导入.log",
        )
        evidence = _video_process_evidence(log_path)
        self.assertEqual(evidence["detected_size"], "816x1088")
        case = {
            "orientation": "landscape",
            "media": "video",
            "expected": [1920, 1080],
            "actual": [816, 1088],
            "passed": False,
            "measured_source": "raw_import",
            "video_process": evidence,
            "detail": "",
        }
        detail = _case_detail(case, (1920, 1080), (816, 1088))
        self.assertIn("816x1088", detail)
        self.assertIn("1920×1080", detail)
        self.assertIn("resolution_mismatch:816x1088", detail)
        self.assertIn("859948", detail)
        self.assertIn("鉴权 OK", detail)

    def test_sizes_match_no_swap_for_resolution(self):
        self.assertTrue(sizes_match((1600, 1200), (1600, 1200), swap_ok=False))
        self.assertFalse(sizes_match((1200, 1600), (1600, 1200), swap_ok=False))
        self.assertTrue(sizes_match((1200, 1600), (1600, 1200), swap_ok=True))


if __name__ == "__main__":
    unittest.main()
