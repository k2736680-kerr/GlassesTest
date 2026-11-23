import os
import tempfile
import unittest

import glasstest.config as app_config


class RunOutputTests(unittest.TestCase):
    def test_use_run_output_keeps_logs_and_shots_apart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = app_config.use_run_output(tmp)
            self.assertEqual(root, os.path.abspath(tmp))
            self.assertTrue(os.path.isdir(os.path.join(root, "日志")))
            self.assertTrue(os.path.isdir(os.path.join(root, "截图")))
            self.assertEqual(app_config.RUN_LOG_DIR, os.path.join(root, "日志"))
            self.assertEqual(app_config.SCREENSHOT_DIR, os.path.join(root, "截图"))


if __name__ == "__main__":
    unittest.main()
