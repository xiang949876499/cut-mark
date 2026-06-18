from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from kdenlive_timeline import TimelineClip, TimelineProject, TimelineTrack


IMAGE_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"}
AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".wma"}


@dataclass(frozen=True)
class _ProducerRef:
    id: str
    source: Path | None
    service: str
    resource: str
    name: str


class KdenliveProjectBuilder:
    def write(self, project: TimelineProject, output_path: Path) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        root = self._build_xml(project)
        tree = ET.ElementTree(root)
        ET.indent(tree, space="  ")
        xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        validate_project_xml(xml_bytes)

        fd, temp_name = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent)
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            temp_path.write_bytes(xml_bytes)
            os.replace(temp_path, output_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
        return output_path

    def _build_xml(self, project: TimelineProject) -> ET.Element:
        root = ET.Element(
            "mlt",
            {
                "LC_NUMERIC": "C",
                "version": "7.32.0",
                "title": "cut-mark",
                "producer": "main_bin",
            },
        )
        self._add_profile(root, project)
        self._add_markers(root, project)

        producers, producer_by_source, producer_by_clip = self._collect_producers(_all_tracks(project))
        for ref in producers:
            self._add_producer(root, ref)

        for sequence_id, sequence in project.sequences.items():
            for track in sequence.tracks:
                self._add_playlist(root, track, producer_by_source, producer_by_clip)
            self._add_tractor(root, sequence.tracks, tractor_id=sequence_id)

        for track in project.tracks:
            self._add_playlist(root, track, producer_by_source, producer_by_clip)

        self._add_tractor(root, project.tracks, tractor_id="main_tractor")
        return root

    def _add_profile(self, root: ET.Element, project: TimelineProject) -> None:
        ET.SubElement(
            root,
            "profile",
            {
                "description": f"{project.width}x{project.height} {project.fps} fps",
                "width": str(project.width),
                "height": str(project.height),
                "progressive": "1",
                "sample_aspect_num": "1",
                "sample_aspect_den": "1",
                "display_aspect_num": str(project.width),
                "display_aspect_den": str(project.height),
                "frame_rate_num": str(project.fps),
                "frame_rate_den": "1",
                "colorspace": "709",
            },
        )

    def _add_markers(self, root: ET.Element, project: TimelineProject) -> None:
        for index, marker in enumerate(project.markers):
            _property(root, f"kdenlive:guide.{index}", f"{marker}:Marker {index + 1}")

    def _collect_producers(self, tracks: Iterable[TimelineTrack]) -> tuple[list[_ProducerRef], dict[Path, str], dict[str, str]]:
        refs: list[_ProducerRef] = []
        seen: dict[Path, str] = {}
        by_clip: dict[str, str] = {}
        for track in tracks:
            for clip in track.clips:
                if clip.source is None:
                    if clip.nested_sequence_id:
                        by_clip[clip.id] = clip.nested_sequence_id
                        continue
                    producer_id = f"producer-{len(refs) + 1}"
                    by_clip[clip.id] = producer_id
                    refs.append(
                        _ProducerRef(
                            id=producer_id,
                            source=None,
                            service="color",
                            resource="0x00000000",
                            name=clip.name or clip.id,
                        )
                    )
                    continue
                source = clip.source.resolve()
                if source in seen:
                    by_clip[clip.id] = seen[source]
                    continue
                producer_id = f"producer-{len(refs) + 1}"
                seen[source] = producer_id
                by_clip[clip.id] = producer_id
                refs.append(
                    _ProducerRef(
                        id=producer_id,
                        source=source,
                        service=_service_for_source(source),
                        resource=str(source),
                        name=clip.name or source.stem,
                    )
                )
        return refs, seen, by_clip

    def _add_producer(self, root: ET.Element, ref: _ProducerRef) -> None:
        producer = ET.SubElement(root, "producer", {"id": ref.id})
        _property(producer, "mlt_service", ref.service)
        _property(producer, "resource", ref.resource)
        _property(producer, "kdenlive:clipname", ref.name)

    def _add_playlist(
        self,
        root: ET.Element,
        track: TimelineTrack,
        producer_by_source: dict[Path, str],
        producer_by_clip: dict[str, str],
    ) -> None:
        playlist = ET.SubElement(root, "playlist", {"id": track.id})
        cursor = 0
        for clip in sorted(track.clips, key=lambda item: item.start_frame):
            if clip.duration_frames <= 0:
                raise ValueError(f"Clip duration must be positive: {clip.id}")
            if clip.start_frame < cursor:
                raise ValueError(f"Clips overlap on track {track.id}: {clip.id}")
            if clip.start_frame > cursor:
                ET.SubElement(playlist, "blank", {"length": str(clip.start_frame - cursor)})
            entry = ET.SubElement(
                playlist,
                "entry",
                {
                    "producer": _producer_id_for_clip(clip, producer_by_source, producer_by_clip),
                    "in": str(clip.source_in_frame),
                    "out": str(clip.source_in_frame + clip.duration_frames - 1),
                },
            )
            _add_filters(entry, clip)
            cursor = clip.start_frame + clip.duration_frames

    def _add_tractor(self, root: ET.Element, tracks: Iterable[TimelineTrack], *, tractor_id: str) -> None:
        track_list = list(tracks)
        end_frame = max((clip.end_frame for track in track_list for clip in track.clips), default=0)
        tractor = ET.SubElement(root, "tractor", {"id": tractor_id, "in": "0", "out": str(end_frame)})
        _property(tractor, "kdenlive:docproperties.version", "1.1")
        _property(tractor, "kdenlive:docproperties.profile", "atsc_1080p_30")
        _property(tractor, "kdenlive:docproperties.documentid", tractor_id)
        for track in track_list:
            attributes = {"producer": track.id}
            if track.kind == "audio":
                attributes["hide"] = "video"
            ET.SubElement(tractor, "track", attributes)


def validate_project_xml(xml_bytes: bytes) -> None:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid Kdenlive XML: {exc}") from exc
    if root.tag != "mlt":
        raise ValueError("Kdenlive project root must be <mlt>")
    if root.find("profile") is None:
        raise ValueError("Kdenlive project must include a profile")
    if root.find("tractor") is None:
        raise ValueError("Kdenlive project must include a tractor")

    ids: set[str] = set()
    for element in root.iter():
        element_id = element.attrib.get("id")
        if not element_id:
            continue
        if element_id in ids:
            raise ValueError(f"Duplicate MLT id: {element_id}")
        ids.add(element_id)

    for entry in root.iter("entry"):
        producer_id = entry.attrib.get("producer")
        if not producer_id or producer_id not in ids:
            raise ValueError(f"Missing producer reference: {producer_id}")
        entry_in = int(entry.attrib.get("in", "0"))
        entry_out = int(entry.attrib.get("out", "-1"))
        if entry_out < entry_in:
            raise ValueError(f"Invalid entry frame range for producer: {producer_id}")

    for track in root.iter("track"):
        producer_id = track.attrib.get("producer")
        if not producer_id or producer_id not in ids:
            raise ValueError(f"Missing track producer reference: {producer_id}")


def _producer_id_for_clip(
    clip: TimelineClip,
    producer_by_source: dict[Path, str],
    producer_by_clip: dict[str, str],
) -> str:
    if clip.source is None:
        if clip.nested_sequence_id:
            return clip.nested_sequence_id
        producer_id = producer_by_clip.get(clip.id)
        if producer_id:
            return producer_id
        raise ValueError(f"Clip has no source producer: {clip.id}")
    producer_id = producer_by_source.get(clip.source.resolve())
    if not producer_id:
        raise ValueError(f"Clip source has no producer: {clip.source}")
    return producer_id


def _all_tracks(project: TimelineProject) -> list[TimelineTrack]:
    tracks = list(project.tracks)
    for sequence in project.sequences.values():
        tracks.extend(_all_tracks(sequence))
    return tracks


def _add_filters(entry: ET.Element, clip: TimelineClip) -> None:
    for index, effect in enumerate(clip.effects):
        filter_element = ET.SubElement(entry, "filter", {"id": f"{clip.id}-filter-{index + 1}"})
        _property(filter_element, "mlt_service", effect.service)
        _property(filter_element, "kdenlive:effect.status", effect.status)
        if effect.source_name:
            _property(filter_element, "kdenlive:effect.source_name", effect.source_name)
        for name, value in effect.properties.items():
            _property(filter_element, name, str(value))


def _service_for_source(source: Path) -> str:
    suffix = source.suffix.casefold()
    if suffix in IMAGE_EXTENSIONS:
        return "qimage"
    if suffix in VIDEO_EXTENSIONS or suffix in AUDIO_EXTENSIONS:
        return "avformat-novalidate"
    return "avformat-novalidate"


def _property(parent: ET.Element, name: str, value: str) -> None:
    element = ET.SubElement(parent, "property", {"name": name})
    element.text = value
