import tempfile
import unittest
import zipfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from citation_session import CitationSessionStore
from office_bridge import OfficeBridge


class CitationSessionTests(unittest.TestCase):
    def test_numbers_and_opaque_tokens_remain_stable(self):
        target = {
            "app_id": "word",
            "document_name": "paper.docx",
            "full_name": "C:/work/paper.docx",
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = CitationSessionStore(tmp)
            session, numbers, tokens = store.prepare(
                target, ["paper-a", "paper-b"])
            self.assertEqual(numbers, {"paper-a": 1, "paper-b": 2})
            self.assertTrue(tokens["paper-a"].startswith("LB-"))
            self.assertNotIn("paper-a", tokens["paper-a"])
            _, repeated_numbers, repeated_tokens = store.prepare(
                target, ["paper-b", "paper-c"])
            self.assertEqual(repeated_numbers["paper-b"], 2)
            self.assertEqual(repeated_numbers["paper-c"], 3)
            self.assertEqual(repeated_tokens["paper-b"], tokens["paper-b"])
            self.assertIn("citations", session)
            group = store.create_smart_group(
                target, session, ["paper-a", "paper-b"])
            self.assertLess(len(group), 50)
            self.assertNotIn("paper-a", group)
            reloaded = store.load(target)
            self.assertEqual(
                reloaded["smart_groups"][group]["paper_ids"],
                ["paper-a", "paper-b"])


class _FakeRange:
    End = 7


class _FakeSelection:
    def __init__(self):
        self.Range = _FakeRange()
        self.collapsed = None
        self.typed = []

    def Collapse(self, direction):
        self.collapsed = direction

    def TypeText(self, text):
        self.typed.append(text)

    def SetRange(self, start, end):
        self.range_after = (start, end)


class _FakeControl:
    def __init__(self):
        self.Title = ""
        self.Tag = ""
        self.Range = _FakeRange()


class _FakeControls:
    def __init__(self):
        self.created = []

    def Add(self, kind, target_range):
        control = _FakeControl()
        self.created.append(control)
        return control


class _FakeDocument:
    Name = "paper.docx"
    FullName = "C:/work/paper.docx"

    def __init__(self):
        self.ContentControls = _FakeControls()


class _FakeDocuments:
    Count = 1


class _FakeApplication:
    def __init__(self):
        self.Documents = _FakeDocuments()
        self.ActiveDocument = _FakeDocument()
        self.Selection = _FakeSelection()


class _FakeClient:
    def __init__(self, app):
        self.app = app

    def GetActiveObject(self, prog_id):
        if prog_id == "Word.Application":
            return self.app
        raise RuntimeError("not running")


class OfficeBridgeTests(unittest.TestCase):
    def test_privacy_mode_inserts_plain_text_without_document_marker(self):
        app = _FakeApplication()
        bridge = OfficeBridge()
        bridge._client = _FakeClient(app)
        result = bridge.insert("[1]\nReference")
        self.assertFalse(result["smart_applied"])
        self.assertEqual(app.Selection.collapsed, 0)
        self.assertEqual(app.Selection.typed, ["[1]\rReference"])
        self.assertFalse(app.ActiveDocument.ContentControls.created)

    def test_smart_mode_embeds_only_supplied_opaque_token(self):
        app = _FakeApplication()
        bridge = OfficeBridge()
        bridge._client = _FakeClient(app)
        result = bridge.insert("[1]", smart_token="LB-opaque")
        self.assertTrue(result["smart_applied"])
        control = app.ActiveDocument.ContentControls.created[0]
        self.assertEqual(control.Tag, "Lazybones:LB-opaque")
        self.assertNotIn("paper_id", control.Tag)
        self.assertFalse(app.Selection.typed)

    def test_clean_docx_verification_detects_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            marked = Path(tmp) / "marked.docx"
            with zipfile.ZipFile(marked, "w") as archive:
                archive.writestr(
                    "word/document.xml", "<tag>Lazybones:LB-x</tag>")
            clean = Path(tmp) / "clean.docx"
            with zipfile.ZipFile(clean, "w") as archive:
                archive.writestr("word/document.xml", "<w:p>text</w:p>")
            self.assertFalse(OfficeBridge.verify_clean_docx(marked)["clean"])
            self.assertTrue(OfficeBridge.verify_clean_docx(clean)["clean"])


if __name__ == "__main__":
    unittest.main()
