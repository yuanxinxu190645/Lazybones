import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from database import (
    export_zip,
    get_all_papers,
    import_zip,
    init_db,
    insert_paper,
    inspect_backup,
)


def make_paper_database(path, paper_id, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    init_db(str(path))
    insert_paper(
        str(path), paper_id, data,
        schema_ver="v1", model_used="test", lang="zh")


class BackupCompatibilityTests(unittest.TestCase):
    def test_legacy_minimal_schema_is_upgraded_additively(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_db = root / "old" / "project" / "database" / "main.db"
            old_db.parent.mkdir(parents=True)
            connection = sqlite3.connect(old_db)
            connection.execute(
                "CREATE TABLE papers "
                "(paper_id TEXT PRIMARY KEY, data_json TEXT)")
            connection.execute(
                "INSERT INTO papers VALUES (?, ?)",
                ("legacy__zh", json.dumps({"Title": "Legacy"})))
            connection.commit()
            connection.close()
            old_zip = root / "legacy.zip"
            with zipfile.ZipFile(old_zip, "w") as archive:
                archive.write(old_db, "project/database/main.db")

            destination = root / "destination"
            import_zip(
                str(old_zip), str(destination),
                mode="new_library", new_db_name="legacy")
            imported = destination / "database" / "legacy.db"
            connection = sqlite3.connect(imported)
            columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(papers)").fetchall()
            }
            connection.close()
            self.assertEqual(
                {
                    "paper_id", "added_at", "schema_ver", "model_used",
                    "lang", "data_json", "edit_log",
                },
                columns,
            )
            connection = sqlite3.connect(imported)
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                2)
            connection.close()
            self.assertEqual(
                get_all_papers(str(imported))[0]["Title"], "Legacy")

    def test_old_zip_imports_and_merges_by_doi_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target_project = root / "target"
            target_db = target_project / "database" / "main.db"
            make_paper_database(
                target_db,
                "existing__zh",
                {
                    "Title": "Kept title",
                    "DOI": "https://doi.org/10.1000/SAME",
                    "Year": "2024",
                    "Journal_Full": "",
                },
            )

            old_root = root / "old" / "project" / "database"
            old_root.mkdir(parents=True)
            source_db = old_root / "main.db"
            make_paper_database(
                source_db,
                "other__zh",
                {
                    "Title": "Should not overwrite",
                    "DOI": "doi:10.1000/same",
                    "Year": "2024",
                    "Journal_Full": "Imported Journal",
                },
            )
            old_zip = root / "old_backup.zip"
            with zipfile.ZipFile(
                    old_zip, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(
                    source_db, "project/database/main.db")

            info = inspect_backup(str(old_zip))
            self.assertEqual(info["format_version"], 1)
            self.assertFalse(info["has_manifest"])
            result = import_zip(
                str(old_zip), str(target_project),
                mode="merge", target_db_name="main")
            self.assertEqual(result["inserted"], 0)
            self.assertEqual(result["merged"], 1)
            papers = get_all_papers(str(target_db))
            self.assertEqual(len(papers), 1)
            self.assertEqual(papers[0]["Title"], "Kept title")
            self.assertEqual(
                papers[0]["Journal_Full"], "Imported Journal")

    def test_new_backup_roundtrip_includes_schemes_and_dedup_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_project = root / "source"
            make_paper_database(
                source_project / "database" / "library.db",
                "paper__zh",
                {"Title": "Round trip", "DOI": "10.1000/roundtrip"},
            )
            (source_project / "citation_schemes.json").write_text(
                json.dumps({
                    "version": 1,
                    "default_scheme_id": "custom",
                    "schemes": [{
                        "id": "custom",
                        "name": "Custom",
                        "number_format": "[{n}]",
                        "template": "{Title}",
                    }],
                }),
                encoding="utf-8",
            )
            (source_project / "dedup_index.json").write_text(
                json.dumps({
                    "version": 1,
                    "records": [{
                        "paper_id": "paper",
                        "sha256": "abc",
                        "doi": "10.1000/roundtrip",
                    }],
                }),
                encoding="utf-8",
            )
            session_dir = source_project / "citation_sessions"
            session_dir.mkdir(parents=True)
            (session_dir / "document.json").write_text(
                json.dumps({"version": 1, "citations": {"paper": {
                    "number": 1, "token": "LB-safe"}}}),
                encoding="utf-8")
            backup = root / "new_backup.zip"
            exported = export_zip(str(source_project), str(backup))
            self.assertEqual(exported["format_version"], 2)
            info = inspect_backup(str(backup))
            self.assertTrue(info["has_manifest"])
            self.assertTrue(info["has_citation_schemes"])
            self.assertTrue(info["has_citation_sessions"])
            self.assertIn("library", info["databases"])

            destination = root / "destination"
            result = import_zip(
                str(backup), str(destination), mode="new_library")
            self.assertIn("library", result["imported_databases"])
            papers = get_all_papers(
                str(destination / "database" / "library.db"))
            self.assertEqual(papers[0]["Title"], "Round trip")
            self.assertTrue(
                (destination / "citation_schemes.json").exists())
            self.assertTrue((destination / "dedup_index.json").exists())
            self.assertTrue(
                (destination / "citation_sessions" /
                 "document.json").exists())

            with zipfile.ZipFile(backup, "r") as archive:
                manifest = json.loads(
                    archive.read(
                        "project/backup_manifest.json").decode("utf-8"))
                self.assertEqual(manifest["format_version"], 2)
                paths = {item["path"] for item in manifest["files"]}
                self.assertIn(
                    "project/citation_schemes.json", paths)
                self.assertIn("project/dedup_index.json", paths)
                self.assertIn(
                    "project/citation_sessions/document.json", paths)

    def test_replace_keeps_automatic_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            make_paper_database(
                target / "database" / "main.db",
                "old__zh", {"Title": "Old"})
            source = root / "source"
            make_paper_database(
                source / "database" / "main.db",
                "new__zh", {"Title": "New"})
            backup = root / "replace.zip"
            export_zip(str(source), str(backup))

            result = import_zip(
                str(backup), str(target),
                mode="replace", target_db_name="main")
            self.assertEqual(len(result["backups"]), 1)
            self.assertTrue(Path(result["backups"][0]).exists())
            self.assertEqual(
                get_all_papers(
                    str(target / "database" / "main.db"))[0]["Title"],
                "New",
            )

    def test_tampered_manifest_file_is_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            make_paper_database(
                source / "database" / "main.db",
                "source__zh", {"Title": "Source"})
            backup = root / "backup.zip"
            export_zip(str(source), str(backup))
            tampered = root / "tampered.zip"
            with zipfile.ZipFile(backup, "r") as original:
                with zipfile.ZipFile(tampered, "w") as output:
                    for info in original.infolist():
                        data = original.read(info.filename)
                        if info.filename == "project/database/main.db":
                            data = data + b"tampered"
                        output.writestr(info, data)

            destination = root / "destination"
            with self.assertRaises(ValueError):
                import_zip(
                    str(tampered), str(destination),
                    mode="new_library")
            self.assertFalse((destination / "database" / "main.db").exists())

    def test_failed_asset_copy_rolls_database_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            target_db = target / "database" / "main.db"
            make_paper_database(
                target_db, "old__zh", {"Title": "Old"})
            source = root / "source"
            make_paper_database(
                source / "database" / "main.db",
                "new__zh", {"Title": "New"})
            backup = root / "backup.zip"
            export_zip(str(source), str(backup))

            with patch(
                    "database._copy_cache_tree",
                    side_effect=OSError("simulated disk full")):
                with self.assertRaises(RuntimeError):
                    import_zip(
                        str(backup), str(target),
                        mode="merge", target_db_name="main")
            papers = get_all_papers(str(target_db))
            self.assertEqual(
                [paper["Title"] for paper in papers], ["Old"])


if __name__ == "__main__":
    unittest.main()
