import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from workflow import (can_auto_continue_pairs, next_item_index,
                      normalize_pending_files)


class WorkflowAutomationTests(unittest.TestCase):
    def test_pending_queue_restores_only_existing_supported_unique_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "paper.pdf"
            docx = root / "support.docx"
            text = root / "notes.txt"
            pdf.write_bytes(b"pdf")
            docx.write_bytes(b"docx")
            text.write_text("ignored", encoding="utf-8")

            result = normalize_pending_files([
                pdf, str(pdf), docx, text, root / "missing.pdf"])
            self.assertEqual(result, [os.path.abspath(pdf),
                                      os.path.abspath(docx)])

    def test_existing_queue_is_not_added_twice(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "paper.pdf"
            path.write_bytes(b"pdf")
            self.assertEqual(
                normalize_pending_files([path], existing=[str(path)]), [])

    def test_pair_dialog_is_skipped_only_for_clean_nonempty_result(self):
        pairs = {"main.pdf": ["si.pdf"]}
        self.assertTrue(can_auto_continue_pairs(pairs, [], True))
        self.assertFalse(can_auto_continue_pairs(pairs, ["orphan.pdf"], True))
        self.assertFalse(can_auto_continue_pairs(pairs, [], False))
        self.assertFalse(can_auto_continue_pairs({}, [], True))

    def test_next_review_position_is_kept_after_removal(self):
        self.assertEqual(next_item_index(3, 5), 3)
        self.assertEqual(next_item_index(3, 3), 2)
        self.assertEqual(next_item_index(0, 0), 0)


if __name__ == "__main__":
    unittest.main()
