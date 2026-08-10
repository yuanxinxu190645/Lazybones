import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import update_manager
import updater


class UpdateManagerTests(unittest.TestCase):
    def test_semantic_versions_are_compared_numerically(self):
        self.assertTrue(update_manager.is_newer_version("v1.10.0", "1.9.9"))
        self.assertFalse(update_manager.is_newer_version("1.4", "1.4.0"))
        self.assertFalse(update_manager.is_newer_version("unknown", "1.0"))

    def test_latest_release_requires_expected_asset_and_digest(self):
        digest = "a" * 64
        payload = {
            "tag_name": "v1.5.0",
            "name": "Lazybones 1.5",
            "body": "notes",
            "html_url": "https://github.com/example/release",
            "draft": False,
            "prerelease": False,
            "assets": [{
                "name": "Lazybones-v1.5.0-portable-win64.zip",
                "browser_download_url": (
                    "https://github.com/example/download/package.zip"),
                "size": 123,
                "digest": "sha256:" + digest,
            }],
        }
        with patch.object(
                update_manager, "_read_url",
                return_value=json.dumps(payload).encode("utf-8")):
            info = update_manager.check_latest_release("1.4.0")
        self.assertEqual(info.version, "1.5.0")
        self.assertEqual(info.sha256, digest)

    def test_current_release_returns_none(self):
        payload = {
            "tag_name": "v1.4.0", "assets": [],
            "draft": False, "prerelease": False,
        }
        with patch.object(
                update_manager, "_read_url",
                return_value=json.dumps(payload).encode("utf-8")):
            self.assertIsNone(update_manager.check_latest_release("1.4.0"))


class StandaloneUpdaterTests(unittest.TestCase):
    @staticmethod
    def _digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def test_apply_replaces_manifest_files_but_preserves_user_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            install = root / "install"
            package_root = root / "package" / "Lazybones-v1.5.0"
            install.mkdir()
            package_root.mkdir(parents=True)

            (install / "Lazybones.exe").write_bytes(b"old-app")
            (install / "old-runtime.dll").write_bytes(b"old-runtime")
            (install / "user-library.db").write_bytes(b"user-data")
            old_manifest = {
                "product": "Lazybones", "version": "1.4.0",
                "files": [
                    {"path": "Lazybones.exe", "sha256": "0" * 64},
                    {"path": "old-runtime.dll", "sha256": "0" * 64},
                ],
            }
            (install / "portable_manifest.json").write_text(
                json.dumps(old_manifest), encoding="utf-8")

            new_files = {
                "Lazybones.exe": b"new-app",
                "_internal/runtime.dll": b"new-runtime",
            }
            entries = []
            for name, content in new_files.items():
                target = package_root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                entries.append({
                    "path": name,
                    "size": len(content),
                    "sha256": self._digest(content),
                })
            manifest = {
                "product": "Lazybones", "version": "1.5.0",
                "entry_point": "Lazybones.exe", "files": entries,
            }
            (package_root / "portable_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8")
            package = root / "update.zip"
            with zipfile.ZipFile(package, "w") as archive:
                for path in package_root.rglob("*"):
                    if path.is_file():
                        archive.write(path, path.relative_to(root / "package"))

            with patch.object(updater, "_local_state_root",
                              return_value=root / "state"), \
                    patch.object(updater.subprocess, "Popen") as popen:
                updater.apply_update(
                    package, install, "Lazybones.exe",
                    updater._sha256(package), 0)

            self.assertEqual(
                (install / "Lazybones.exe").read_bytes(), b"new-app")
            self.assertTrue((install / "_internal/runtime.dll").exists())
            self.assertFalse((install / "old-runtime.dll").exists())
            self.assertEqual(
                (install / "user-library.db").read_bytes(), b"user-data")
            installed_manifest = json.loads(
                (install / "portable_manifest.json").read_text("utf-8"))
            self.assertEqual(installed_manifest["version"], "1.5.0")
            popen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
