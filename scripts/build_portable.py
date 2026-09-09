"""Build, verify and zip a reproducible Lazybones Windows portable release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from version import APP_VERSION, portable_asset_name  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked_target(path: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(ROOT.resolve())
    return resolved


def clean_dir(path: Path) -> None:
    target = checked_target(path)
    if target.exists():
        shutil.rmtree(target)


def run(*args: str) -> None:
    environment = os.environ.copy()
    # A venv created from Conda can import extensions via Conda's DLL setup,
    # while PyInstaller's dependency scan cannot find the same DLLs on PATH.
    # Scope these search paths to build subprocesses; do not change user settings.
    runtime = Path(sys.base_prefix)
    dll_dirs = [runtime, runtime / "Library" / "bin", runtime / "DLLs"]
    environment["PATH"] = os.pathsep.join(
        [str(path) for path in dll_dirs if path.is_dir()]
        + [environment.get("PATH", "")])
    subprocess.run(list(args), cwd=ROOT, check=True, env=environment)


def write_manifest(portable_dir: Path) -> Path:
    files = []
    for path in sorted(portable_dir.rglob("*")):
        if not path.is_file() or path.name == "portable_manifest.json":
            continue
        relative = path.relative_to(portable_dir).as_posix()
        files.append({
            "path": relative,
            "size": path.stat().st_size,
            "sha256": sha256(path),
        })
    manifest = {
        "product": "Lazybones",
        "version": APP_VERSION,
        "entry_point": "Lazybones.exe",
        "architecture": "win64",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }
    target = portable_dir / "portable_manifest.json"
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def create_zip(portable_dir: Path, release_dir: Path) -> Path:
    release_dir.mkdir(parents=True, exist_ok=True)
    asset = release_dir / portable_asset_name(APP_VERSION)
    if asset.exists():
        raise FileExistsError("拒绝覆盖已有便携包，请使用新版本号：" + str(asset))
    root_name = asset.stem
    with zipfile.ZipFile(asset, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for path in sorted(portable_dir.rglob("*")):
            if path.is_file():
                archive.write(
                    path,
                    (Path(root_name) / path.relative_to(portable_dir)).as_posix(),
                )
    digest = sha256(asset)
    sidecar = asset.with_name(asset.name + ".sha256")
    sidecar.write_text(digest + "  " + asset.name + "\n", encoding="ascii")
    (release_dir / "SHA256SUMS.txt").write_text(
        digest + "  " + asset.name + "\n", encoding="ascii")
    return asset


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    if not args.skip_tests:
        run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v")

    clean_dir(ROOT / "build" / "updater")
    clean_dir(ROOT / "build" / "portable")
    clean_dir(ROOT / "dist-tools")
    clean_dir(ROOT / "dist" / "Lazybones")
    # Preserve historical release ZIPs and version-specific checksum sidecars.
    (ROOT / "release").mkdir(exist_ok=True)

    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", "dist-tools", "--workpath", "build/updater",
        "updater.spec")
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", "dist", "--workpath", "build/portable",
        "build.spec")

    portable_dir = ROOT / "dist" / "Lazybones"
    required = [
        portable_dir / "Lazybones.exe",
        portable_dir / "_internal" / "LazybonesUpdater.exe",
        portable_dir / "_internal" / "prompts" / "v1.0_basic.yaml",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError("便携版缺少必要文件：" + ", ".join(missing))

    write_manifest(portable_dir)
    asset = create_zip(portable_dir, ROOT / "release")
    print("PORTABLE_ASSET=" + str(asset))
    print("SHA256=" + sha256(asset))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
