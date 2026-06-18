import unittest
from pathlib import Path

from kdenlive_timeline import (
    TimelineClip,
    TimelineProject,
    TimelineTrack,
    frames_from_seconds,
    split_ranges_from_markers,
)


class KdenliveTimelineTests(unittest.TestCase):
    def test_seconds_convert_to_frames_with_rounding(self):
        self.assertEqual(frames_from_seconds(4.5, 30), 135)
        self.assertEqual(frames_from_seconds(1 / 30, 30), 1)

    def test_markers_create_contiguous_frame_ranges(self):
        ranges = split_ranges_from_markers([0.0, 1.0, 2.5], count=3, fps=30, fallback_frames=60)

        self.assertEqual(ranges, [(0, 29), (30, 74), (75, 134)])

    def test_selector_matches_named_stickers(self):
        project = TimelineProject(width=1080, height=1920, fps=30)
        project.tracks.append(
            TimelineTrack(
                id="stickers",
                kind="video",
                role="sticker",
                clips=[
                    TimelineClip(
                        id="sticker-1",
                        source=Path("ring.png"),
                        start_frame=0,
                        duration_frames=60,
                        name="旋彩光圈",
                        role="sticker",
                    )
                ],
            )
        )

        self.assertEqual([clip.id for clip in project.select("named:光圈")], ["sticker-1"])

    def test_selector_matches_video_and_sticker_roles(self):
        project = TimelineProject(width=1080, height=1920, fps=30)
        project.tracks.append(
            TimelineTrack(
                id="video",
                kind="video",
                role="video",
                clips=[TimelineClip("video-1", Path("a.mp4"), 0, 30, role="video")],
            )
        )
        project.tracks.append(
            TimelineTrack(
                id="stickers",
                kind="video",
                role="sticker",
                clips=[TimelineClip("sticker-1", Path("s.png"), 0, 30, role="sticker")],
            )
        )

        self.assertEqual([clip.id for clip in project.select("all_video_segments")], ["video-1"])
        self.assertEqual([clip.id for clip in project.select("all_stickers")], ["sticker-1"])


if __name__ == "__main__":
    unittest.main()
