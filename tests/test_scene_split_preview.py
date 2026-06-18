import tempfile
import unittest
from pathlib import Path

from scene_split_preview import parse_args, resolve_detection_options, rename_clips_with_prefix


class SceneSplitPreviewTests(unittest.TestCase):
    def test_outfit_change_preset_uses_more_sensitive_ffmpeg_detection(self):
        args = parse_args(["input.mp4", "--preset", "outfit-change"])

        options = resolve_detection_options(args)

        self.assertEqual(options["detector"], "clothing")
        self.assertEqual(options["backend"], "ffmpeg")

    def test_explicit_threshold_overrides_outfit_change_preset(self):
        args = parse_args(["input.mp4", "--preset", "outfit-change", "--detector", "scene", "--ffmpeg-scene-threshold", "0.08"])

        options = resolve_detection_options(args)

        self.assertEqual(options["detector"], "scene")
        self.assertEqual(options["ffmpeg_scene_threshold"], 0.08)

    def test_clothing_detector_options_are_parsed(self):
        args = parse_args(
            [
                "input.mp4",
                "--detector",
                "clothing",
                "--clothing-threshold",
                "0.4",
                "--clothing-sample-fps",
                "5",
                "--clothing-confirmation-frames",
                "3",
            ]
        )

        options = resolve_detection_options(args)

        self.assertEqual(options["detector"], "clothing")
        self.assertEqual(options["clothing_threshold"], 0.4)
        self.assertEqual(options["clothing_sample_fps"], 5)
        self.assertEqual(options["clothing_confirmation_frames"], 3)

    def test_outfit_change_uses_auto_person_detector_by_default(self):
        args = parse_args(["input.mp4", "--preset", "outfit-change"])

        options = resolve_detection_options(args)

        self.assertEqual(options["person_detector"], "auto")

    def test_yolo_options_are_parsed(self):
        args = parse_args(
            [
                "input.mp4",
                "--detector",
                "clothing",
                "--person-detector",
                "yolo",
                "--yolo-model",
                "person.pt",
                "--yolo-confidence",
                "0.45",
                "--yolo-imgsz",
                "512",
            ]
        )

        options = resolve_detection_options(args)

        self.assertEqual(options["person_detector"], "yolo")
        self.assertEqual(options["yolo_model"], "person.pt")
        self.assertEqual(options["yolo_confidence"], 0.45)
        self.assertEqual(options["yolo_imgsz"], 512)

    def test_rename_clips_with_prefix_outputs_independent_outfit_videos(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clips = []
            for index in range(1, 3):
                clip = root / f"scene_{index:03d}.mp4"
                clip.write_bytes(f"clip {index}".encode("utf-8"))
                clips.append(clip)

            renamed = rename_clips_with_prefix(clips, "outfit")

        self.assertEqual([clip.name for clip in renamed], ["outfit_001.mp4", "outfit_002.mp4"])


if __name__ == "__main__":
    unittest.main()
