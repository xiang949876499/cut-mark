from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class RuntimeIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeSpec:
    version: str
    url: str
    filename: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    kdenlive_exe: Path
    melt_exe: Path
    ffmpeg_exe: Path | None = None
    ffprobe_exe: Path | None = None


KDENLIVE_26_04_2 = RuntimeSpec(
    version="26.04.2",
    url="https://download.kde.org/stable/kdenlive/26.04/windows/kdenlive-26.04.2_standalone.exe",
    filename="kdenlive-26.04.2_standalone.exe",
    size_bytes=133_466_947,
    sha256="4f5a9167a65fa7df411ca6655fa826f0d9ec502ddd157b3ddbf70cd77398dff4",
)


class KdenliveRuntime:
    def __init__(
        self,
        runtime_dir: Path,
        *,
        auto_download: bool = True,
        opener=urllib.request.urlopen,
        runner=subprocess.run,
        spec: RuntimeSpec = KDENLIVE_26_04_2,
        download_attempts: int = 3,
    ) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.auto_download = auto_download
        self.opener = opener
        self.runner = runner
        self.spec = spec
        self.download_attempts = max(1, download_attempts)

    @property
    def install_dir(self) -> Path:
        return self.runtime_dir / f"kdenlive-{self.spec.version}"

    def resolve(self) -> RuntimePaths:
        if self.install_dir.exists():
            return self._discover_runtime(self.install_dir)
        if not self.auto_download:
            raise RuntimeError(f"Kdenlive runtime not found: {self.install_dir}")
        archive = self.download()
        return self.extract(archive)

    def verify_archive(self, path: Path, spec: RuntimeSpec | None = None) -> None:
        spec = spec or self.spec
        actual_size = path.stat().st_size
        if actual_size != spec.size_bytes:
            raise RuntimeIntegrityError(
                f"Kdenlive archive size mismatch: expected {spec.size_bytes}, got {actual_size}"
            )
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        actual_hash = digest.hexdigest()
        if actual_hash.lower() != spec.sha256.lower():
            raise RuntimeIntegrityError(
                f"Kdenlive archive SHA-256 mismatch: expected {spec.sha256}, got {actual_hash}"
            )

    def download(self, spec: RuntimeSpec | None = None) -> Path:
        spec = spec or self.spec
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        archive = self.runtime_dir / spec.filename
        part = archive.with_suffix(archive.suffix + ".part")
        last_error: Exception | None = None
        for _ in range(self.download_attempts):
            try:
                request = urllib.request.Request(spec.url, headers={"User-Agent": "cut-mark/1.0"})
                with self.opener(request, timeout=60) as response, part.open("wb") as output:
                    shutil.copyfileobj(response, output, length=1024 * 1024)
                self.verify_archive(part, spec)
                part.replace(archive)
                return archive
            except Exception as exc:
                last_error = exc
                part.unlink(missing_ok=True)
        raise last_error

    def extract(self, archive: Path) -> RuntimePaths:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        staging = self.runtime_dir / f".extracting-kdenlive-{self.spec.version}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        result = self.runner(
            [str(archive), "-y", f"-o{staging}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if getattr(result, "returncode", 0) != 0:
            shutil.rmtree(staging, ignore_errors=True)
            raise RuntimeError(f"Kdenlive extraction failed: {getattr(result, 'stderr', '')}")

        self._verify_tree_within(staging)
        if self.install_dir.exists():
            shutil.rmtree(self.install_dir)
        staging.replace(self.install_dir)
        paths = self._discover_runtime(self.install_dir)
        self._write_manifest(paths, archive)
        return paths

    def _discover_runtime(self, root: Path) -> RuntimePaths:
        kdenlive_exe = _find_unique(root, "kdenlive.exe")
        melt_exe = _find_unique(root, "melt.exe")
        ffmpeg_exe = _find_optional_unique(root, "ffmpeg.exe")
        ffprobe_exe = _find_optional_unique(root, "ffprobe.exe")
        paths = RuntimePaths(
            root=root.resolve(),
            kdenlive_exe=kdenlive_exe.resolve(),
            melt_exe=melt_exe.resolve(),
            ffmpeg_exe=ffmpeg_exe.resolve() if ffmpeg_exe else None,
            ffprobe_exe=ffprobe_exe.resolve() if ffprobe_exe else None,
        )
        for path in [paths.kdenlive_exe, paths.melt_exe, paths.ffmpeg_exe, paths.ffprobe_exe]:
            if path and not _is_relative_to(path, paths.root):
                raise RuntimeError(f"Runtime executable escapes install root: {path}")
        return paths

    def _verify_tree_within(self, root: Path) -> None:
        resolved_root = root.resolve()
        for path in root.rglob("*"):
            if not _is_relative_to(path.resolve(), resolved_root):
                raise RuntimeError(f"Extracted path escapes runtime directory: {path}")

    def _write_manifest(self, paths: RuntimePaths, archive: Path) -> None:
        manifest = {
            "version": self.spec.version,
            "archive": str(archive.resolve()),
            "sha256": self.spec.sha256,
            "kdenlive": str(paths.kdenlive_exe.relative_to(paths.root)),
            "melt": str(paths.melt_exe.relative_to(paths.root)),
            "ffmpeg": str(paths.ffmpeg_exe.relative_to(paths.root)) if paths.ffmpeg_exe else None,
            "ffprobe": str(paths.ffprobe_exe.relative_to(paths.root)) if paths.ffprobe_exe else None,
        }
        (paths.root / "runtime.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _find_unique(root: Path, name: str) -> Path:
    matches = list(root.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one {name} under {root}, found {len(matches)}")
    return matches[0]


def _find_optional_unique(root: Path, name: str) -> Path | None:
    matches = list(root.rglob(name))
    if len(matches) > 1:
        raise RuntimeError(f"Expected at most one {name} under {root}, found {len(matches)}")
    return matches[0] if matches else None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
