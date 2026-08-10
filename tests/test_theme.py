import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from theme import (DENSITY_LABELS, PALETTES, THEME_LABELS,
                   normalize_appearance, resolve_theme_id)


class ThemeConfigurationTests(unittest.TestCase):
    def test_all_public_theme_choices_exist(self):
        self.assertEqual(
            set(THEME_LABELS),
            {"speed", "codex_dark", "codex_light", "system", "classic"})
        self.assertIn("speed", PALETTES)
        self.assertIn("codex_dark", PALETTES)
        self.assertIn("codex_light", PALETTES)
        self.assertIn("classic", PALETTES)

    def test_invalid_old_settings_get_safe_defaults(self):
        appearance = normalize_appearance({
            "theme": "missing", "font_scale": "bad",
            "ui_density": "huge",
        })
        self.assertEqual(appearance["theme"], "classic")
        self.assertEqual(appearance["font_scale"], 100)
        self.assertEqual(appearance["ui_density"], "comfortable")

    def test_font_scale_is_bounded(self):
        self.assertEqual(
            normalize_appearance({"font_scale": 10})["font_scale"], 80)
        self.assertEqual(
            normalize_appearance({"font_scale": 500})["font_scale"], 140)

    def test_classic_is_default_and_labels_are_product_neutral(self):
        self.assertEqual(normalize_appearance({})["theme"], "classic")
        self.assertNotIn("Codex", "".join(THEME_LABELS.values()))
        self.assertEqual(THEME_LABELS["speed"], "极速")

    def test_system_theme_resolves_to_current_windows_mode(self):
        with patch("theme.windows_uses_dark_theme", return_value=True):
            self.assertEqual(resolve_theme_id("system"), "codex_dark")
        with patch("theme.windows_uses_dark_theme", return_value=False):
            self.assertEqual(resolve_theme_id("system"), "codex_light")

    def test_density_labels_are_stable_for_settings_compatibility(self):
        self.assertEqual(DENSITY_LABELS["compact"], "紧凑")
        self.assertEqual(DENSITY_LABELS["comfortable"], "舒适")


if __name__ == "__main__":
    unittest.main()
