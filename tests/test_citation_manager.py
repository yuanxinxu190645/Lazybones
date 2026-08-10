import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from citation_manager import (
    BUILTIN_SCHEMES,
    DEFAULT_SCHEME,
    delete_citation_scheme,
    format_citation,
    format_inline_citation,
    load_citation_schemes,
    save_citation_schemes,
    select_citation_record,
    upsert_citation_scheme,
)


class CitationManagerTests(unittest.TestCase):
    def test_default_format_hides_missing_optional_punctuation(self):
        citation = format_citation(
            {
                "Authors_Full": "Ada Lovelace",
                "Title": "Analytical Engine",
                "Journal_Full": "Computing",
                "Year": "1843",
                "Volume": "N/A",
                "Issue": "",
                "Pages": None,
                "DOI": "10.1000/test",
            },
            DEFAULT_SCHEME,
            1,
        )
        self.assertEqual(
            citation,
            "[1] Ada Lovelace. Analytical Engine. Computing. "
            "1843. DOI: 10.1000/test",
        )
        self.assertNotIn("()", citation)
        self.assertNotIn(",,", citation)

    def test_number_formats_include_circled_and_custom(self):
        paper = {"Title": "A paper"}
        circled = dict(
            DEFAULT_SCHEME,
            number_format="{circled}",
            template="[[{Title}]]",
        )
        custom = dict(
            DEFAULT_SCHEME,
            number_format="Ref-{n}:",
            template="[[{Title}]]",
        )
        self.assertEqual(format_citation(paper, circled, 2), "② A paper")
        self.assertEqual(format_citation(paper, custom, 7), "Ref-7: A paper")

    def test_scheme_persistence_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = load_citation_schemes(tmp)
            saved = upsert_citation_scheme(
                tmp,
                payload,
                "My Style",
                "({n})",
                "[[{Title}. ]][[{Year}]]",
            )
            reloaded = load_citation_schemes(tmp)
            names = [item["name"] for item in reloaded["schemes"]]
            self.assertIn("My Style", names)
            self.assertEqual(
                reloaded["default_scheme_id"], DEFAULT_SCHEME["id"])
            reloaded["default_scheme_id"] = saved["id"]
            save_citation_schemes(tmp, reloaded)
            self.assertEqual(
                load_citation_schemes(tmp)["default_scheme_id"],
                saved["id"])
            self.assertTrue(
                delete_citation_scheme(tmp, reloaded, saved["id"]))
            final = load_citation_schemes(tmp)
            self.assertNotIn(
                "My Style", [item["name"] for item in final["schemes"]])
            self.assertTrue(Path(tmp, "citation_schemes.json").exists())

    def test_na_primary_fields_use_valid_legacy_fallbacks(self):
        citation = format_citation(
            {
                "Authors_Full": "N/A",
                "Authors": "Grace Hopper",
                "Title": "Compiler",
                "Journal_Full": "N/A",
                "Journal": "Computing Journal",
                "Year": "1952",
            },
            DEFAULT_SCHEME,
            1,
        )
        self.assertIn("Grace Hopper", citation)
        self.assertIn("Computing Journal", citation)

    def test_doi_url_is_not_duplicated_in_apa_scheme(self):
        apa = next(
            scheme for scheme in BUILTIN_SCHEMES
            if scheme["id"] == "builtin_apa")
        citation = format_citation(
            {
                "Title": "Paper",
                "DOI": "https://doi.org/10.1000/example",
            },
            apa,
            1,
        )
        self.assertEqual(
            citation.count("https://doi.org/"), 1)

    def test_duplicate_scheme_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = load_citation_schemes(tmp)
            upsert_citation_scheme(
                tmp, payload, "Unique", "[{n}]", "{Title}")
            with self.assertRaises(ValueError):
                upsert_citation_scheme(
                    tmp, payload, " unique ", "[{n}]", "{Title}")

    def test_corrupt_scheme_file_is_preserved_and_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "citation_schemes.json"
            path.write_text("{broken", encoding="utf-8")
            payload = load_citation_schemes(tmp)
            self.assertTrue(payload["warnings"])
            self.assertGreaterEqual(len(payload["schemes"]), 3)
            self.assertTrue(list(Path(tmp).glob(
                "citation_schemes.corrupt_*.json")))

    def test_best_language_mode_merges_missing_fields(self):
        selected = select_citation_record([
            {
                "_lang": "zh",
                "Title": "标题",
                "DOI": "N/A",
                "Journal_Full": "中文期刊",
            },
            {
                "_lang": "en",
                "Title": "English title",
                "DOI": "10.1000/merged",
                "Journal_Full": "",
            },
        ])
        self.assertEqual(selected["DOI"], "10.1000/merged")
        self.assertEqual(selected["Journal_Full"], "中文期刊")
        english = select_citation_record([
            {"_lang": "zh", "Title": "中文"},
            {"_lang": "en", "Title": "English"},
        ], "英文记录优先")
        self.assertEqual(english["Title"], "English")

    def test_legacy_scheme_gains_backward_compatible_inline_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "citation_schemes.json").write_text(
                '{"version":1,"schemes":[{"id":"legacy",'
                '"name":"Legacy","template":"{Title}"}]}',
                encoding="utf-8")
            payload = load_citation_schemes(tmp)
            legacy = next(item for item in payload["schemes"]
                          if item["id"] == "legacy")
            self.assertEqual(legacy["inline_style"], "numeric")
            self.assertEqual(legacy["inline_template"], "[{numbers}]")

    def test_numeric_inline_uses_document_numbers_and_compresses_ranges(self):
        papers = [
            {"_citation_base_id": "a"},
            {"_citation_base_id": "b"},
            {"_citation_base_id": "c"},
            {"_citation_base_id": "e"},
        ]
        rendered = format_inline_citation(
            papers, DEFAULT_SCHEME,
            {"a": 1, "b": 2, "c": 3, "e": 5})
        self.assertEqual(rendered, "[1–3,5]")

    def test_author_year_inline_uses_scheme_template(self):
        scheme = {
            "inline_style": "author_year",
            "inline_template": "({items})",
            "inline_item_template": "{author}, {year}",
            "inline_separator": "; ",
        }
        rendered = format_inline_citation([
            {"First_Author": "Wang", "Year": "2024"},
            {"First_Author": "Li", "Year": "2023"},
        ], scheme)
        self.assertEqual(rendered, "(Wang, 2024; Li, 2023)")


if __name__ == "__main__":
    unittest.main()
