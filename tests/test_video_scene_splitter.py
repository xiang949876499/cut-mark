import json
import tempfile
import unittest
from pathlib import Path

from video_scene_splitter import (
    SceneRange,
    cut_scene_clips,
    normalize_scene_ranges,
    probe_video,
)


class VideoSceneSplitterTests(unittest.TestCase):
    def test_normalize_scene_ranges_covers_full_duration_and_merges_short_ranges(self):
        ranges = normalize_scene_ranges(
            [0.0, 0.3, 2.0, 5.0],
            duration=5.0,
            min_scene_duration=0.8,
        )

        self.assertEqual(ranges, [SceneRange(1, 0.0, 2.0), SceneRange(2, 2.0, 5.0)])

    def test_no_cut_points_returns_single_scene(self):
        ranges = normalize_scene_ranges([], duration=4.2, min_scene_duration=0.8)

        self.assertEqual(ranges, [SceneRange(1, 0.0, 4.2)])

    def test_probe_video_reads_ffprobe_json(self):
        def fake_runner(command, **kwargs):
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": json.dumps(
                        {
                            "streams": [
                                {
                                    "codec_type": "video",
                                    "width": 1920,
                                    "height": 1080,
                                    "avg_frame_rate": "30000/1001",
                                }
                            ],
                            "format": {"duration": "12.5"},
                        }
                    ),
                    "stderr": "",
                },
            )()

        metadata = probe_video(Path("input.mp4"), runner=fake_runner)

        self.assertEqual(metadata.width, 1920)
        self.assertEqual(metadata.height, 1080)
        self.assertAlmostEqual(metadata.fps, 29.97, places=2)
        self.assertEqual(metadata.duration, 12.5)

    def test_cut_scene_clips_uses_ffmpeg_without_shell(self):
        calls = []

        def fake_runner(command, **kwargs):
            calls.append(command)
            Path(command[-1]).write_bytes(b"scene")
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            output_dir = root / "scenes"
            clips = cut_scene_clips(
                source,
                [SceneRange(1, 0.0, 1.5), SceneRange(2, 1.5, 3.0)],
                output_dir,
                runner=fake_runner,
            )

        self.assertEqual([clip.name for clip in clips], ["scene_001.mp4", "scene_002.mp4"])
        self.assertTrue(all(call[0] == "ffmpeg" for call in calls))
        self.assertTrue(all("-ss" in call for call in calls))


if __name__ == "__main__":
    unittest.main()
