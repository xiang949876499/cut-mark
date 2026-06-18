import json
import re
import tempfile
import unittest
from pathlib import Path

from comfy_client import ComfyPromptResult
from reference_resolver import SceneReferences
from video_replace_pipeline import (
    VideoReplaceConfig,
    build_job_id,
    process_video_replacement,
    write_concat_file,
)
from video_scene_splitter import SceneRange, VideoMetadata


class VideoReplacePipelineTests(unittest.TestCase):
    def test_build_job_id_changes_when_source_metadata_changes(self):
        first = build_job_id(Path("a.mp4"), 100, 1, Path("workflow.json"), Path("output"))
        second = build_job_id(Path("a.mp4"), 101, 1, Path("workflow.json"), Path("output"))

        self.assertRegex(first, re.compile(r"^[0-9a-f]{12}$"))
        self.assertNotEqual(first, second)

    def test_write_concat_file_preserves_processed_clip_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clips = [root / "scene 001.mp4", root / "scene_002.mp4"]
            concat_path = root / "concat.txt"

            write_concat_file(clips, concat_path)

            lines = concat_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(lines[0], f"file '{clips[0].as_posix()}'")
        self.assertEqual(lines[1], f"file '{clips[1].as_posix()}'")

    def test_process_video_replacement_splits_replaces_and_merges(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            refs = root / "refs"
            refs.mkdir()
            (refs / "default_person.png").write_bytes(b"person")
            (refs / "default_background.png").write_bytes(b"background")
            workflow = root / "workflow.json"
            workflow.write_text(
                json.dumps(
                    {
                        "10": {"inputs": {"video": ""}},
                        "11": {"inputs": {"image": ""}},
                        "12": {"inputs": {"image": ""}},
                        "13": {"inputs": {"filename_prefix": ""}},
                    }
                ),
                encoding="utf-8",
            )
            output_dir = root / "output"

            def fake_cut(src, ranges, out_dir):
                out_dir.mkdir(parents=True, exist_ok=True)
                clips = []
                for scene in ranges:
                    clip = out_dir / f"scene_{scene.index:03d}.mp4"
                    clip.write_bytes(f"clip {scene.index}".encode("utf-8"))
                    clips.append(clip)
                return clips

            class FakeClient:
                def submit_and_wait(self, patched_workflow, *, timeout_sec):
                    self.last_workflow = patched_workflow
                    return ComfyPromptResult("abc", {"abc": {"outputs": {}}})

            def fake_materialize(result, scene_clip, references, output_path):
                output_path.write_bytes(f"processed {references.scene_index}".encode("utf-8"))
                return output_path

            def fake_merge(processed_clips, source_video, output_path, concat_path):
                write_concat_file(processed_clips, concat_path)
                output_path.write_bytes(b"merged")

            result = process_video_replacement(
                source_video=source,
                refs_dir=refs,
                workflow_path=workflow,
                output_dir=output_dir,
                config=VideoReplaceConfig(),
                probe=lambda _: VideoMetadata(640, 360, 30, 2.0),
                detect=lambda _: [1.0],
                cut=fake_cut,
                client=FakeClient(),
                materialize_comfy_output=fake_materialize,
                merge=fake_merge,
                work_root=root / "work",
            )

            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            report = json.loads(result.report_path.read_text(encoding="utf-8"))
            output_exists = result.output_path.is_file()

        self.assertTrue(output_exists)
        self.assertEqual(len(manifest["scenes"]), 2)
        self.assertEqual([scene["status"] for scene in manifest["scenes"]], ["succeeded", "succeeded"])
        self.assertEqual(report["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
