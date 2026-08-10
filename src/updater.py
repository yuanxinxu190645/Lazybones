"""Standalone updater executable for Lazybones portable releases."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path


CHUNK_SIZE = 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract(package: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(package) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise RuntimeError("更新包包含越界路径") from exc
        archive.extractall(destination)


def _load_manifest(root: Path) -> tuple[Path, dict]:
    manifests = list(root.rglob("portable_manifest.json"))
    if len(manifests) != 1:
        raise RuntimeError("更新包必须包含且仅包含一个 portable_manifest.json")
    manifest_path = manifests[0]
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("product") != "Lazybones":
        raise RuntimeError("更新包产品标识不正确")
    if not data.get("version") or not data.get("files"):
        raise RuntimeError("更新包清单不完整")
    return manifest_path.parent, data


def _safe_relative(value: str) -> Path:
    path = Path(str(value).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise RuntimeError("清单包含非法路径：" + str(value))
    return path


def _validate_files(package_root: Path, manifest: dict) -> list[Path]:
    paths = []
    for item in manifest["files"]:
        relative = _safe_relative(item.get("path", ""))
        source = package_root / relative
        if not source.is_file():
            raise RuntimeError("更新包缺少文件：" + str(relative))
        expected = str(item.get("sha256") or "").lower()
        if len(expected) != 64 or _sha256(source) != expected:
            raise RuntimeError("更新包内部校验失败：" + str(relative))
        paths.append(relative)
    return paths


def _wait_for_exit(pid: int, timeout: int = 90) -> None:
    if pid <= 0:
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.25)
    raise RuntimeError("主程序未能在限定时间内退出")


def _read_old_paths(install_dir: Path) -> list[Path]:
    manifest_path = install_dir / "portable_manifest.json"
    if not manifest_path.exists():
        return []
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return [_safe_relative(item.get("path", ""))
                for item in data.get("files", [])]
    except Exception:
        return []


def _local_state_root() -> Path:
    return (Path(os.environ.get("LOCALAPPDATA", Path.home())) /
            "Lazybones")


def _write_status(ok: bool, message: str, version: str = "") -> None:
    root = _local_state_root()
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": ok,
        "message": message,
        "version": version,
        "time": datetime.now().isoformat(timespec="seconds"),
    }
    (root / "last_update.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _message(title: str, body: str, error: bool = False) -> None:
    try:
        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, body, title, flags)
    except Exception:
        pass


def apply_update(package: Path, install_dir: Path, main_exe: str,
                 expected_sha256: str, wait_pid: int) -> None:
    package = package.resolve()
    install_dir = install_dir.resolve()
    if not package.is_file() or package.suffix.lower() != ".zip":
        raise RuntimeError("更新包不存在或格式不正确")
    if _sha256(package).lower() != expected_sha256.lower():
        raise RuntimeError("更新包 SHA-256 校验失败")
    if install_dir == Path(install_dir.anchor):
        raise RuntimeError("拒绝更新磁盘根目录")
    _wait_for_exit(wait_pid)

    with tempfile.TemporaryDirectory(prefix="Lazybones-update-") as temp:
        extract_dir = Path(temp) / "extract"
        extract_dir.mkdir()
        _safe_extract(package, extract_dir)
        package_root, manifest = _load_manifest(extract_dir)
        new_paths = _validate_files(package_root, manifest)
        manifest_relative = Path("portable_manifest.json")
        new_paths.append(manifest_relative)
        if Path(main_exe).name != main_exe or Path(main_exe) not in new_paths:
            raise RuntimeError("更新清单缺少主程序")

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_root = _local_state_root() / "update_backups" / stamp
        old_paths = _read_old_paths(install_dir)
        if (install_dir / manifest_relative).exists():
            old_paths.append(manifest_relative)
        copied_new = []
        try:
            for relative in old_paths:
                source = install_dir / relative
                if source.is_file():
                    target = backup_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)

            for relative in new_paths:
                source = package_root / relative
                target = install_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                staged = target.with_name(target.name + ".lazybones-new")
                shutil.copy2(source, staged)
                os.replace(staged, target)
                copied_new.append(relative)

            obsolete = set(old_paths) - set(new_paths)
            for relative in obsolete:
                target = install_dir / relative
                if target.is_file():
                    target.unlink()
        except Exception:
            for relative in set(copied_new) | set(old_paths):
                target = install_dir / relative
                backup = backup_root / relative
                try:
                    if backup.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(backup, target)
                    else:
                        target.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

        version = str(manifest.get("version"))
        _write_status(True, "已更新到 v" + version, version)
        subprocess.Popen(
            [str(install_dir / main_exe)],
            cwd=str(install_dir),
            close_fds=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--package", required=True)
    parser.add_argument("--install-dir", required=True)
    parser.add_argument("--main-exe", required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--wait-pid", type=int, default=0)
    args = parser.parse_args()
    try:
        apply_update(
            Path(args.package), Path(args.install_dir), args.main_exe,
            args.expected_sha256, args.wait_pid)
        return 0
    except Exception as exc:
        message = "自动更新失败：" + str(exc)
        _write_status(False, message)
        _message("Lazybones 更新失败", message, error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
