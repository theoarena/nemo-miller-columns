"""gettext catalog for Miller Columns copy."""

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from miller.i18n import _, install, ngettext, reset


class I18nTests(unittest.TestCase):
    def tearDown(self):
        reset()

    def test_english_msgid_without_catalog(self):
        self.assertEqual(_("Empty folder"), "Empty folder")
        self.assertEqual(ngettext("%d item", "%d items", 1) % 1, "1 item")
        self.assertEqual(ngettext("%d item", "%d items", 2) % 2, "2 items")

    def test_pt_catalog_empty_folder_and_plurals(self):
        install(languages=["pt"])
        self.assertEqual(_("Empty folder"), "Pasta vazia")
        self.assertEqual(_("Loading…"), "Carregando…")
        self.assertEqual(ngettext("%d item", "%d items", 1) % 1, "1 item")
        self.assertEqual(ngettext("%d item", "%d items", 2) % 2, "2 itens")

    def test_pt_BR_uses_pt_catalog(self):
        install(languages=["pt_BR"])
        self.assertEqual(_("Empty folder"), "Pasta vazia")
        self.assertEqual(_("Loading…"), "Carregando…")

    def test_missing_language_returns_english_msgid(self):
        install(languages=["ru"])
        self.assertEqual(_("Empty folder"), "Empty folder")
        self.assertEqual(ngettext("%d item", "%d items", 2) % 2, "2 items")
