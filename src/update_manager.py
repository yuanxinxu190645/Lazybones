"""GitHub Release update checks and portable-package handoff."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from version import (APP_VERSION, GITHUB_LATEST_RELEASE_API,
                     GITHUB_RELEASES_URL, portable_asset_name)


USER_AGENT = "Lazybones-Updater/" + APP_VERSION
CHUNK_SIZE = 1024 * 1024


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    tag: str
    title: str
    notes: str
    release_url: str
    asset_name: str
    download_url: str
    size: int
    sha256: str


def _version_parts(value: str) -> tuple[int, ...]:
    text = str(value or "").strip().lstrip("vV")
    match = re.match(r"^(\d+(?:\.\d+)*)", text)
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer_version(candidate: str, current: str = APP_VERSION) -> bool:
    left = _version_parts(candidate)
    right = _version_parts(current)
    if not left or not right:
        return False
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def _request(url: str):
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _read_url(url: str, timeout: int = 30, attempts: int = 2) -> bytes:
    last_error = None
    for attempt in range(max(1, attempts)):
        try:
            with urllib.request.urlopen(
                    _request(url), timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError:
            raise
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5)
    raise last_error


def _asset_digest(asset: dict) -> str:
    digest = str(asset.get("digest") or "").strip().lower()
    if digest.startswith("sha256:"):
        value = digest.split(":", 1)[1]
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    return ""


def _sidecar_digest(assets: list[dict], asset_name: str) -> str:
    candidates = {asset_name + ".sha256", "SHA256SUMS.txt"}
    for asset in assets:
        if asset.get("name") not in candidates:
            continue
        url = str(asset.get("browser_download_url") or "")
        if not url:
            continue
        try:
            text = _read_url(url, timeout=10).decode("utf-8", "replace")
        except Exception:
            continue
        for line in text.splitlines():
            if asset_name in line:
                match = re.search(r"\b([0-9a-fA-F]{64})\b", line)
                if match:
                    return match.group(1).lower()
    return ""


def check_latest_release(current_version: str = APP_VERSION) -> UpdateInfo | None:
    """Return an installable newer stable release, or ``None``."""
    try:
        payload = json.loads(
            _read_url(GITHUB_LATEST_RELEASE_API).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise UpdateError("GitHub 返回 HTTP " + str(exc.code)) from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise UpdateError("无法连接 GitHub 检查更新：" + str(exc)) from exc

    if payload.get("draft") or payload.get("prerelease"):
        return None
    tag = str(payload.get("tag_name") or "")
    version = tag.lstrip("vV")
    if not is_newer_version(version, current_version):
        return None

    expected_name = portable_asset_name(version)
    assets = list(payload.get("assets") or [])
    asset = next(
        (item for item in assets if item.get("name") == expected_name),
        None,
    )
    if asset is None:
        raise UpdateError(
            "发现新版本 v" + version + "，但 Release 中缺少 " +
            expected_name)
    download_url = str(asset.get("browser_download_url") or "")
    if not download_url.startswith("https://github.com/"):
        raise UpdateError("Release 下载地址不是受支持的 GitHub HTTPS 地址")
    digest = _asset_digest(asset) or _sidecar_digest(assets, expected_name)
    if not digest:
        raise UpdateError(
            "新版本缺少 SHA-256 校验值，已拒绝自动安装；可手动打开发布页。")
    return UpdateInfo(
        version=version,
        tag=tag,
        title=str(payload.get("name") or tag),
        notes=str(payload.get("body") or ""),
        release_url=str(payload.get("html_url") or GITHUB_RELEASES_URL),
        asset_name=expected_name,
        download_url=download_url,
        size=int(asset.get("size") or 0),
        sha256=digest,
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_update(
        info: UpdateInfo,
        destination_dir: Path,
        progress: Callable[[int, int], None] | None = None) -> Path:
    """Download to a partial file, verify SHA-256, then publish atomically."""
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / info.asset_name
    partial = target.with_suffix(target.suffix + ".part")
    try:
        with urllib.request.urlopen(
                _request(info.download_url), timeout=30) as response:
            with open(partial, "wb") as stream:
                total = int(
                    response.headers.get("Content-Length") or info.size or 0)
                downloaded = 0
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    stream.write(chunk)
                    downloaded += len(chunk)
                    if progress:
                        progress(downloaded, total)
        actual = file_sha256(partial)
        if actual.lower() != info.sha256.lower():
            raise UpdateError("更新包 SHA-256 校验失败，文件已丢弃")
        os.replace(partial, target)
        return target
    except Exception:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def updates_dir() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return local / "Lazybones" / "updates"


def launch_portable_updater(package: Path, info: UpdateInfo) -> None:
    """Copy the updater out of the install directory and hand off update."""
    if not getattr(sys, "frozen", False):
        raise UpdateError("源码运行模式不会自动覆盖代码，请使用便携版测试更新。")
    install_dir = Path(sys.executable).resolve().parent
    candidates = [install_dir / "LazybonesUpdater.exe"]
    bundle_root = getattr(sys, "_MEIPASS", "")
    if bundle_root:
        candidates.append(Path(bundle_root) / "LazybonesUpdater.exe")
    updater = next((path for path in candidates if path.exists()), None)
    if updater is None:
        raise UpdateError("便携目录中缺少 LazybonesUpdater.exe")
    temp_updater = (
        Path(tempfile.gettempdir()) /
        ("LazybonesUpdater-" + uuid.uuid4().hex + ".exe")
    )
    shutil.copy2(updater, temp_updater)
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [
            str(temp_updater), "--apply",
            "--package", str(package.resolve()),
            "--install-dir", str(install_dir),
            "--main-exe", Path(sys.executable).name,
            "--expected-sha256", info.sha256,
            "--wait-pid", str(os.getpid()),
        ],
        close_fds=True,
        creationflags=creation_flags,
    )
