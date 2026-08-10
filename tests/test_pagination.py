import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pagination import normalize_page_size, paginate_rows


class PaginationTests(unittest.TestCase):
    def test_page_size_accepts_saved_strings_and_rejects_unknown_values(self):
        self.assertEqual(normalize_page_size("200"), 200)
        self.assertEqual(normalize_page_size(73), 100)
        self.assertEqual(normalize_page_size(None), 100)

    def test_page_is_clamped_and_rows_are_sliced(self):
        rows = list(range(235))
        page_rows, page, total = paginate_rows(rows, 3, 100)
        self.assertEqual(page_rows, list(range(200, 235)))
        self.assertEqual((page, total), (3, 3))

        page_rows, page, total = paginate_rows(rows, 99, 100)
        self.assertEqual(page_rows, list(range(200, 235)))
        self.assertEqual((page, total), (3, 3))

    def test_empty_result_still_has_one_safe_page(self):
        self.assertEqual(paginate_rows([], 2, 50), ([], 1, 1))


if __name__ == "__main__":
    unittest.main()
