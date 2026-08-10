"""Safe best-effort insertion bridge for active Microsoft Word and WPS docs."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path


class OfficeBridgeError(RuntimeError):
    pass


@dataclass
class OfficeTarget:
    app_id: str
    app_name: str
    prog_id: str
    document_name: str
    full_name: str
    application: object
    document: object

    def public(self) -> dict:
        return {
            "app_id": self.app_id,
            "app_name": self.app_name,
            "document_name": self.document_name,
            "full_name": self.full_name,
        }


class OfficeBridge:
    APPLICATIONS = (
        ("word", "Microsoft Word", "Word.Application"),
        ("wps", "WPS 文字", "KWPS.Application"),
    )

    def __init__(self):
        self._client = None

    def _load_client(self):
        if self._client is not None:
            return self._client
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            self._client = win32com.client
            return self._client
        except ImportError as error:
            raise OfficeBridgeError(
                "缺少 Word/WPS 连接组件 pywin32，请重新安装程序环境。") from error

    @staticmethod
    def _target_from_application(app_id, app_name, prog_id, application):
        try:
            if int(application.Documents.Count) < 1:
                return None
            document = application.ActiveDocument
            name = str(document.Name or "未命名文档")
            try:
                full_name = str(document.FullName or "")
            except Exception:
                full_name = ""
            return OfficeTarget(
                app_id, app_name, prog_id, name, full_name,
                application, document)
        except Exception:
            return None

    def list_targets(self) -> list[OfficeTarget]:
        client = self._load_client()
        targets = []
        for app_id, app_name, prog_id in self.APPLICATIONS:
            try:
                application = client.GetActiveObject(prog_id)
            except Exception:
                continue
            target = self._target_from_application(
                app_id, app_name, prog_id, application)
            if target is not None:
                targets.append(target)
        return targets

    def get_target(self, app_id: str = "auto") -> OfficeTarget:
        targets = self.list_targets()
        if app_id and app_id != "auto":
            targets = [target for target in targets
                       if target.app_id == app_id]
        if not targets:
            raise OfficeBridgeError(
                "未检测到已打开且包含文档的 Microsoft Word 或 WPS 文字。")
        return targets[0]

    def insert(self, text: str, app_id: str = "auto",
               smart_token: str = "") -> dict:
        if not str(text):
            raise OfficeBridgeError("没有可插入的引用内容。")
        target = self.get_target(app_id)
        selection = target.application.Selection
        rendered = str(text).replace("\r\n", "\n").replace("\n", "\r")
        # Collapse to the end so an accidental selection is never overwritten.
        try:
            selection.Collapse(0)
        except Exception:
            pass
        smart_applied = False
        if smart_token:
            control = None
            try:
                control = target.document.ContentControls.Add(
                    0, selection.Range)
                control.Title = "Lazybones 引用"
                control.Tag = "Lazybones:" + smart_token
                control.Range.Text = rendered
                try:
                    control.Appearance = 2
                except Exception:
                    pass
                try:
                    selection.SetRange(control.Range.End, control.Range.End)
                except Exception:
                    pass
                smart_applied = True
            except Exception:
                smart_applied = False
                if control is not None:
                    try:
                        control.Delete(True)
                    except Exception:
                        pass
        if not smart_applied:
            selection.TypeText(rendered)
        return {
            "target": target.public(),
            "smart_applied": smart_applied,
        }

    @staticmethod
    def _remove_lazybones_metadata(document) -> int:
        removed = 0
        try:
            controls = document.ContentControls
            for index in range(int(controls.Count), 0, -1):
                control = controls.Item(index)
                tag = str(getattr(control, "Tag", "") or "")
                title = str(getattr(control, "Title", "") or "")
                if tag.startswith("Lazybones:") or title == "Lazybones 引用":
                    control.Delete(False)
                    removed += 1
        except Exception:
            pass
        try:
            bookmarks = document.Bookmarks
            for index in range(int(bookmarks.Count), 0, -1):
                bookmark = bookmarks.Item(index)
                if str(bookmark.Name).upper().startswith("_LB_"):
                    bookmark.Delete()
                    removed += 1
        except Exception:
            pass
        try:
            properties = document.CustomDocumentProperties
            for index in range(int(properties.Count), 0, -1):
                prop = properties.Item(index)
                if str(prop.Name).casefold().startswith("lazybones"):
                    prop.Delete()
                    removed += 1
        except Exception:
            pass
        return removed

    @staticmethod
    def verify_clean_docx(path: str) -> dict:
        candidate = Path(path)
        hits = []
        if candidate.suffix.casefold() == ".docx" and zipfile.is_zipfile(candidate):
            with zipfile.ZipFile(candidate, "r") as archive:
                for name in archive.namelist():
                    if not name.casefold().endswith((".xml", ".rels")):
                        continue
                    data = archive.read(name).lower()
                    if b"lazybones:" in data or b"lazybones citation" in data:
                        hits.append(name)
        return {"clean": not hits, "marker_files": hits}

    def create_clean_copy(self, output_path: str,
                          app_id: str = "auto") -> dict:
        output = Path(output_path).resolve()
        target = self.get_target(app_id)
        if target.full_name:
            try:
                if output == Path(target.full_name).resolve():
                    raise OfficeBridgeError("投稿干净版不能覆盖当前编辑原稿。")
            except OSError:
                pass
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            try:
                # 16 = the default modern Word document format (.docx).
                target.document.SaveCopyAs(str(output), 16)
            except Exception:
                target.document.SaveCopyAs(str(output))
            clean_document = target.application.Documents.Open(
                str(output), False, False, False)
            removed = self._remove_lazybones_metadata(clean_document)
            clean_document.Save()
            clean_document.Close(False)
        except OfficeBridgeError:
            raise
        except Exception as error:
            raise OfficeBridgeError(
                "生成投稿干净版失败：" + str(error)) from error
        verification = self.verify_clean_docx(str(output))
        return {
            "output_path": str(output),
            "removed": removed,
            **verification,
        }
