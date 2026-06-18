from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Keyframe:
    frame: int
    value: str


@dataclass
class TimelineEffect:
    service: str
    properties: dict[str, str]
    keyframes: list[Keyframe] = field(default_factory=list)
    status: str = "exact"
    source_name: str = ""


@dataclass
class TimelineClip:
    id: str
    source: Path | None
    start_frame: int
    duration_frames: int
    source_in_frame: int = 0
    name: str = ""
    role: str = "video"
    effects: list[TimelineEffect] = field(default_factory=list)
    nested_sequence_id: str | None = None

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.duration_frames - 1


@dataclass
class TimelineTrack:
    id: str
    kind: str
    role: str
    clips: list[TimelineClip] = field(default_factory=list)


@dataclass
class TimelineProject:
    width: int
    height: int
    fps: int
    tracks: list[TimelineTrack] = field(default_factory=list)
    markers: list[int] = field(default_factory=list)
    sequences: dict[str, "TimelineProject"] = field(default_factory=dict)

    def select(self, selector: str) -> list[TimelineClip]:
        selector = selector or "selected"
        if selector == "all_video_segments":
            return [clip for track in self.tracks for clip in track.clips if clip.role == "video"]
        if selector == "all_stickers":
            return [clip for track in self.tracks for clip in track.clips if clip.role == "sticker"]
        if selector.startswith("named:"):
            query = selector.split(":", 1)[1].casefold()
            return [
                clip
                for track in self.tracks
                for clip in track.clips
                if query and query in clip.name.casefold()
            ]
        return [clip for track in self.tracks for clip in track.clips]


def frames_from_seconds(seconds: float, fps: int) -> int:
    return max(0, int(round(seconds * fps)))


def split_ranges_from_markers(
    marker_times: list[float],
    *,
    count: int,
    fps: int,
    fallback_frames: int,
) -> list[tuple[int, int]]:
    marker_frames = [frames_from_seconds(value, fps) for value in marker_times]
    ranges: list[tuple[int, int]] = []
    for index in range(count):
        if index < len(marker_frames):
            start = marker_frames[index]
        elif ranges:
            start = ranges[-1][1] + 1
        else:
            start = 0

        if index + 1 < len(marker_frames):
            end = max(start, marker_frames[index + 1] - 1)
        else:
            end = start + max(1, fallback_frames) - 1
        ranges.append((start, end))
    return ranges


def copy_clip_attributes(source: TimelineClip, targets: list[TimelineClip]) -> None:
    for target in targets:
        target.effects = copy.deepcopy(source.effects)


def duplicate_clips(clips: list[TimelineClip], offset_frames: int) -> list[TimelineClip]:
    duplicates = copy.deepcopy(clips)
    for index, clip in enumerate(duplicates):
        clip.id = f"{clip.id}-copy-{index + 1}"
        clip.start_frame += offset_frames
    return duplicates


def create_nested_sequence(project: TimelineProject, clips: list[TimelineClip]) -> str | None:
    if not clips:
        return None
    selected_ids = {clip.id for clip in clips}
    start_frame = min(clip.start_frame for clip in clips)
    end_frame = max(clip.end_frame for clip in clips)
    sequence_id = f"sequence-{len(project.sequences) + 1}"
    child = TimelineProject(width=project.width, height=project.height, fps=project.fps)

    insertion_track: TimelineTrack | None = None
    insertion_role = clips[0].role
    for track in project.tracks:
        selected_on_track = [clip for clip in track.clips if clip.id in selected_ids]
        if not selected_on_track:
            continue
        if insertion_track is None:
            insertion_track = track
            insertion_role = track.role
        child_track = TimelineTrack(
            id=f"{sequence_id}-{track.id}",
            kind=track.kind,
            role=track.role,
            clips=copy.deepcopy(selected_on_track),
        )
        for child_clip in child_track.clips:
            child_clip.start_frame -= start_frame
        child.tracks.append(child_track)
        track.clips = [clip for clip in track.clips if clip.id not in selected_ids]

    if insertion_track is None:
        return None
    project.sequences[sequence_id] = child
    compound_name = clips[0].name or sequence_id
    insertion_track.clips.append(
        TimelineClip(
            id=f"{sequence_id}-clip",
            source=None,
            start_frame=start_frame,
            duration_frames=end_frame - start_frame + 1,
            name=compound_name,
            role=insertion_role,
            nested_sequence_id=sequence_id,
        )
    )
    insertion_track.clips.sort(key=lambda clip: clip.start_frame)
    return sequence_id
