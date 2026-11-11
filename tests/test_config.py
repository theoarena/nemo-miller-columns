"""Contract tests for miller.config timing and layout knobs."""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from miller import config


class ConfigContractTest(unittest.TestCase):
    """Freeze the public knob values consumers rely on."""

    def test_timing_knobs(self):
        self.assertEqual(config.NAVIGATE_DELAY_MS, 180)
        self.assertEqual(config.KEYBOARD_DIR_DELAY_MS, 180)
        self.assertEqual(config.KEYBOARD_PREVIEW_DELAY_MS, 180)
        self.assertEqual(config.MONITOR_DEBOUNCE_MS, 180)
        self.assertEqual(config.ROW_BUILD_INTERVAL_MS, 16)
        self.assertEqual(config.ROW_BUILD_CHUNK, 40)

    def test_layout_knobs(self):
        self.assertEqual(config.COLUMN_WIDTH, 240)
        self.assertEqual(config.COLUMN_MIN_WIDTH, 140)
        self.assertEqual(config.COLUMN_MAX_WIDTH, 600)
        self.assertEqual(config.COLUMN_RESIZE_HANDLE_WIDTH, 6)
        self.assertEqual(config.BOARD_MIN_SIZE, 100)
        self.assertEqual(config.PREVIEW_CONTENT_WIDTH, 360)
        self.assertEqual(config.PREVIEW_CONTENT_MARGIN, 24)
        self.assertEqual(config.IMAGE_FIT_WIDTH, 320)
        self.assertEqual(config.IMAGE_FIT_HEIGHT, 240)
        self.assertEqual(config.IMAGE_CORNER_RADIUS, 12)
        self.assertEqual(config.TILE_SIZE, 160)
        self.assertEqual(config.LARGE_ICON, 96)
        self.assertEqual(config.CAPTION_WIDTH, 128)
        self.assertEqual(config.ROW_ICON_SIZE, 16)
        self.assertEqual(config.EMBLEM_ICON_SIZE, 8)
        self.assertEqual(config.MENU_ICON_SIZE, 16)


if __name__ == "__main__":
    unittest.main()
