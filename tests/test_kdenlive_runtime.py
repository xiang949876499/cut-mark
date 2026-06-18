import tempfile
import unittest
from pathlib import Path

from kdenlive_runtime import (
    KDENLIVE_26_04_2,
    KdenliveRuntime,
    RuntimeIntegrityError,
)


class FakeResponse:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self.data) - self.offset
        chunk = self.data[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class KdenliveRuntimeTests(unittest.TestCase):
    def test_release_metadata_is_pinned(self):
        self.assertEqual(KDENLIVE_26_04_2.version, "26.04.2")
        self.assertEqual(KDENLIVE_26_04_2.size_bytes, 133_466_947)
        self.assertEqual(
            KDENLIVE_26_04_2.sha256,
            "4f5a9167a65fa7df411ca6655fa826f0d9ec502ddd157b3ddbf70cd77398dff4",
        )

    def test_verify_archive_rejects_wrong_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "runtime.exe"
            archive.write_bytes(b"not kdenlive")
            runtime = KdenliveRuntime(Path(tmp), auto_download=False)

            with self.assertRaises(RuntimeIntegrityError):
                runtime.verify_archive(archive)

    def test_resolve_reuses_existing_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = root / "kdenlive-26.04.2"
            (install / "bin").mkdir(parents=True)
            (install / "bin" / "kdenlive.exe").write_bytes(b"")
            (install / "bin" / "melt.exe").write_bytes(b"")

            paths = KdenliveRuntime(root, auto_download=False).resolve()

        self.assertEqual(paths.kdenlive_exe.name, "kdenlive.exe")
        self.assertEqual(paths.melt_exe.name, "melt.exe")

    def test_download_removes_part_file_after_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = KdenliveRuntime(
                root,
                auto_download=True,
                opener=lambda request, timeout=60: (_ for _ in ()).throw(OSError("network down")),
            )

            with self.assertRaises(OSError):
                runtime.download()

            self.assertFalse((root / f"{KDENLIVE_26_04_2.filename}.part").exists())

    def test_download_rejects_wrong_size_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = KdenliveRuntime(root, auto_download=True, opener=lambda request, timeout=60: FakeResponse(b"small"))

            with self.assertRaises(RuntimeIntegrityError):
                runtime.download()

            self.assertFalse((root / KDENLIVE_26_04_2.filename).exists())

    def test_extract_uses_sfx_output_directory_and_discovers_executables(self):
        commands = []

        def fake_runner(command, **kwargs):
            commands.append(command)
            out_arg = [item for item in command if str(item).startswith("-o")][0]
            staging = Path(str(out_arg)[2:])
            (staging / "bin").mkdir(parents=True)
            (staging / "bin" / "kdenlive.exe").write_bytes(b"")
            (staging / "bin" / "melt.exe").write_bytes(b"")
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / KDENLIVE_26_04_2.filename
            archive.write_bytes(b"fake archive")
            runtime = KdenliveRuntime(root, auto_download=False, runner=fake_runner)

            paths = runtime.extract(archive)

        self.assertEqual(paths.kdenlive_exe.name, "kdenlive.exe")
        self.assertEqual(paths.melt_exe.name, "melt.exe")
        self.assertIn("-y", commands[0])
        self.assertTrue(any(str(item).startswith("-o") for item in commands[0]))

    def test_discovery_rejects_duplicate_melt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = root / "kdenlive-26.04.2"
            (install / "bin").mkdir(parents=True)
            (install / "other").mkdir()
            (install / "bin" / "kdenlive.exe").write_bytes(b"")
            (install / "bin" / "melt.exe").write_bytes(b"")
            (install / "other" / "melt.exe").write_bytes(b"")
            runtime = KdenliveRuntime(root, auto_download=False)

            with self.assertRaises(RuntimeError):
                runtime.resolve()


if __name__ == "__main__":
    unittest.main()
