import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ui import _build_paper_search_document, _normalize_search_text
from search_index import build_cached_search_documents


class SearchIndexTests(unittest.TestCase):
    def test_doi_prefix_and_unicode_are_normalized(self):
        document = _build_paper_search_document({
            "_paper_id": "P1__zh",
            "DOI": "https://doi.org/10.1000/ABC",
            "Title": "Ｆｕｌｌ Width Title",
            "Authors_Full": ["Ada Lovelace", "Alan Turing"],
            "Journal_Full": "Journal of Tests",
            "Keywords": ["Catalysis", "DFT"],
        })
        self.assertIn("10.1000/abc", document["DOI"])
        self.assertIn(
            _normalize_search_text("Full Width"), document["标题"])
        self.assertIn("alan turing", document["作者"])
        self.assertIn("catalysis", document["关键词"])
        self.assertIn("journal of tests", document["全部字段"])

    def test_persistent_search_cache_updates_changed_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "search.db")
            paper = {
                "_paper_id": "paper__zh",
                "Title": "Original title",
                "DOI": "10.1000/cache",
            }
            first = build_cached_search_documents(db_path, [paper])
            second = build_cached_search_documents(db_path, [paper])
            self.assertEqual(first, second)
            paper["Title"] = "Changed title"
            third = build_cached_search_documents(db_path, [paper])
            self.assertIn("changed title", third["paper__zh"]["标题"])
            connection = sqlite3.connect(db_path)
            count = connection.execute(
                "SELECT COUNT(*) FROM paper_search_cache").fetchone()[0]
            connection.close()
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
