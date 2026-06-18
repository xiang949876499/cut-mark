import unittest

from kdenlive_effects import EffectCatalog, KdenliveOperationMapper
from kdenlive_timeline import TimelineClip, TimelineEffect, TimelineProject, TimelineTrack, copy_clip_attributes


class KdenliveEffectsTests(unittest.TestCase):
    def test_motion_blur_uses_first_available_candidate(self):
        catalog = EffectCatalog(filters={"avfilter.avgblur", "frei0r.pixeliz0r"}, transitions=set())
        mapper = KdenliveOperationMapper(catalog)

        result = mapper.resolve_effect("运动模糊")

        self.assertEqual(result.status, "approximated")
        self.assertEqual(result.service, "avfilter.avgblur")

    def test_unknown_effect_is_reported_unsupported(self):
        mapper = KdenliveOperationMapper(EffectCatalog(filters=set(), transitions=set()))
        result = mapper.resolve_effect("不存在的效果")
        self.assertEqual(result.status, "unsupported")

    def test_named_selector_only_changes_matching_sticker(self):
        project = TimelineProject(width=1080, height=1920, fps=30)
        project.tracks.append(
            TimelineTrack(
                id="stickers",
                kind="video",
                role="sticker",
                clips=[
                    TimelineClip("ring", None, 0, 30, name="旋彩光圈", role="sticker"),
                    TimelineClip("ball", None, 0, 30, name="蓝色球体", role="sticker"),
                ],
            )
        )
        mapper = KdenliveOperationMapper(EffectCatalog(filters={"qtblend"}, transitions=set()))

        mapper.apply(project, [{"type": "set_scale", "value": 0.2, "selector": "named:光圈"}])

        self.assertEqual(len(project.select("named:光圈")[0].effects), 1)
        self.assertEqual(len(project.select("named:球体")[0].effects), 0)

    def test_copy_attributes_clones_effects_without_moving_clip(self):
        source = TimelineClip("source", None, 0, 30)
        source.effects.append(TimelineEffect("qtblend", {"rect": "0 0 20% 20% 1"}))
        target = TimelineClip("target", None, 30, 30)

        copy_clip_attributes(source, [target])

        self.assertEqual(target.start_frame, 30)
        self.assertEqual(target.effects, source.effects)
        self.assertIsNot(target.effects[0], source.effects[0])

    def test_compound_clip_creates_nested_sequence(self):
        project = _project_with_two_stickers()
        mapper = KdenliveOperationMapper(EffectCatalog(filters=set(), transitions=set()))

        mapper.apply(
            project,
            [
                {"type": "select", "selector": "all_stickers"},
                {"type": "compound_clip", "selector": "all_stickers"},
            ],
        )

        self.assertEqual(len(project.sequences), 1)
        self.assertEqual(
            sum(1 for track in project.tracks for clip in track.clips if clip.nested_sequence_id),
            1,
        )


def _project_with_two_stickers() -> TimelineProject:
    project = TimelineProject(width=1080, height=1920, fps=30)
    project.tracks.append(
        TimelineTrack(
            id="stickers",
            kind="video",
            role="sticker",
            clips=[
                TimelineClip("ring-1", None, 0, 30, name="旋彩光圈", role="sticker"),
                TimelineClip("ring-2", None, 30, 30, name="旋彩光圈", role="sticker"),
            ],
        )
    )
    return project


if __name__ == "__main__":
    unittest.main()
