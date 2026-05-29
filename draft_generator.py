import argparse
import json
import re
import struct
import zlib
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple


DEFAULT_FALLBACK_TRANSITIONS = ["叠化", "右移", "下移", "向上", "模糊"]
DEFAULT_CONFIG = {
    "draft_name": "web_transition_video",
    "resolution": [1080, 1920],
    "segment_duration_sec": 4.5,
    "max_chars_per_card": 72,
    "fallback_transitions": DEFAULT_FALLBACK_TRANSITIONS,
    "default_background_color": "#000000",
}

SUPPORTED_ASSET_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".gif",
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".m4v",
}


@dataclass(frozen=True)
class TextCard:
    text: str


@dataclass(frozen=True)
class GeneratorConfig:
    draft_folder: Path
    draft_name: str
    resolution: Tuple[int, int]
    segment_duration_sec: float
    max_chars_per_card: int
    fallback_transitions: List[str]
    default_background_color: str


class _FallbackHTMLTextParser(HTMLParser):
    """Small fallback used when BeautifulSoup is not installed."""

    SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "form"}
    BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "div", "article", "section", "br"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._parts: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        if tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        cleaned = " ".join(data.split())
        if cleaned:
            self._parts.append(cleaned)

    def get_text(self) -> str:
        return "\n".join(_clean_lines(" ".join(self._parts).splitlines()))


def load_config(path: Path) -> GeneratorConfig:
    raw = DEFAULT_CONFIG.copy()
    with path.open("r", encoding="utf-8") as handle:
        raw.update(json.load(handle))

    draft_folder = raw.get("draft_folder")
    if not draft_folder:
        raise ValueError("config.json must set draft_folder to your JianyingPro Drafts directory")

    resolution = raw["resolution"]
    if len(resolution) != 2:
        raise ValueError("resolution must contain [width, height]")

    return GeneratorConfig(
        draft_folder=Path(draft_folder),
        draft_name=str(raw["draft_name"]),
        resolution=(int(resolution[0]), int(resolution[1])),
        segment_duration_sec=float(raw["segment_duration_sec"]),
        max_chars_per_card=int(raw["max_chars_per_card"]),
        fallback_transitions=[str(item) for item in raw["fallback_transitions"]],
        default_background_color=str(raw["default_background_color"]),
    )


def read_input_text(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".html", ".htm"}:
        return extract_text_from_html(content)
    return content


def extract_text_from_html(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        parser = _FallbackHTMLTextParser()
        parser.feed(html)
        return parser.get_text()

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    block_texts = [
        tag.get_text(" ", strip=True)
        for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"])
    ]
    if block_texts:
        return "\n".join(_clean_lines(block_texts))
    return "\n".join(_clean_lines(soup.get_text("\n").splitlines()))


def split_into_cards(text: str, max_chars: int) -> List[TextCard]:
    paragraphs = _clean_lines(text.splitlines())
    cards: List[TextCard] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            cards.append(TextCard(paragraph))
            continue
        cards.extend(TextCard(chunk) for chunk in _split_long_paragraph(paragraph, max_chars))
    if not cards:
        raise ValueError("Input text did not contain any usable content")
    return cards


def pick_transition(text: str, fallback_transitions: Sequence[str], index: int) -> str:
    for name in available_transition_names():
        if name and name in text:
            return name
    if not fallback_transitions:
        raise ValueError("fallback_transitions cannot be empty")
    return fallback_transitions[index % len(fallback_transitions)]


def available_transition_names() -> List[str]:
    try:
        from pyJianYingDraft import TransitionType
    except ImportError:
        return sorted(set(DEFAULT_FALLBACK_TRANSITIONS + ["信号故障"]), key=len, reverse=True)

    names = []
    for member in TransitionType:
        names.append(member.value.name)
        names.append(member.name.replace("_", " "))
    return sorted(set(names), key=len, reverse=True)


def ensure_assets(
    assets_dir: Path,
    generated_dir: Path,
    resolution: Tuple[int, int],
    default_background_color: str,
) -> List[Path]:
    assets = _list_assets(assets_dir)
    if assets:
        return assets

    generated_dir.mkdir(parents=True, exist_ok=True)
    default_path = generated_dir / "default_black.png"
    write_solid_png(default_path, resolution[0], resolution[1], default_background_color)
    return [default_path]


def write_solid_png(path: Path, width: int, height: int, color: str) -> None:
    red, green, blue = _parse_hex_color(color)
    raw = b"".join(b"\x00" + bytes((red, green, blue)) * width for _ in range(height))
    png = b"\x89PNG\r\n\x1a\n"
    png += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += _png_chunk(b"IDAT", zlib.compress(raw))
    png += _png_chunk(b"IEND", b"")
    path.write_bytes(png)


def build_draft(config: GeneratorConfig, input_path: Path, assets_dir: Path) -> None:
    try:
        import pyJianYingDraft as draft
    except ImportError as exc:
        raise RuntimeError("pyJianYingDraft is required. Install dependencies with: pip install -r requirements.txt") from exc

    cards = split_into_cards(read_input_text(input_path), config.max_chars_per_card)
    assets = ensure_assets(assets_dir, Path("generated"), config.resolution, config.default_background_color)

    draft_folder = draft.DraftFolder(str(config.draft_folder))
    script = draft_folder.create_draft(
        config.draft_name,
        config.resolution[0],
        config.resolution[1],
        allow_replace=True,
    )
    script.add_track(draft.TrackType.video).add_track(draft.TrackType.text)

    duration_us = int(round(config.segment_duration_sec * 1_000_000))
    video_segments = []
    for index, card in enumerate(cards):
        start_us = index * duration_us
        timerange = draft.trange(start_us, duration_us)
        asset_path = str(assets[index % len(assets)])
        video_segment = draft.VideoSegment(asset_path, timerange)
        video_segments.append(video_segment)

        text_segment = draft.TextSegment(
            card.text,
            timerange,
            style=draft.TextStyle(
                size=7.0,
                color=(1.0, 1.0, 1.0),
                align=1,
                auto_wrapping=True,
                max_line_width=0.82,
            ),
            border=draft.TextBorder(width=28.0, color=(0.0, 0.0, 0.0)),
            background=draft.TextBackground(color="#000000", alpha=0.55, round_radius=0.08, width=0.82, height=0.2),
            clip_settings=draft.ClipSettings(transform_y=0.55),
        )
        script.add_segment(video_segment).add_segment(text_segment)

    for index, segment in enumerate(video_segments[:-1]):
        transition_name = pick_transition(cards[index].text, config.fallback_transitions, index)
        transition_type = _resolve_transition_type(draft.TransitionType, transition_name)
        segment.add_transition(transition_type)

    script.save()
    _print_asset_summary(assets, len(cards))
    print(f"Draft saved: {config.draft_name}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a Jianying draft from pasted web text or HTML.")
    parser.add_argument("--config", default="config.json", type=Path, help="Path to config.json")
    parser.add_argument("--input", required=True, type=Path, help="Path to pasted .txt/.html content")
    parser.add_argument("--assets", default=Path("assets"), type=Path, help="Directory containing optional 01.*, 02.* assets")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    config = load_config(args.config)
    build_draft(config, args.input, args.assets)


def _resolve_transition_type(transition_type, transition_name: str):
    for member in transition_type:
        if member.value.name == transition_name or member.name == transition_name or member.name.replace("_", " ") == transition_name:
            return member
    raise ValueError(f"Unknown transition: {transition_name}")


def _print_asset_summary(assets: Sequence[Path], card_count: int) -> None:
    if len(assets) == 1 and assets[0].name == "default_black.png":
        print("No assets found. Using generated/default_black.png for every card.")
    elif len(assets) < card_count:
        print(f"Only {len(assets)} assets for {card_count} cards. Assets will loop.")
    elif len(assets) > card_count:
        print(f"{len(assets) - card_count} extra assets ignored.")


def _list_assets(assets_dir: Path) -> List[Path]:
    if not assets_dir.exists():
        return []
    return sorted(
        (path for path in assets_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_ASSET_EXTENSIONS),
        key=_natural_key,
    )


def _natural_key(path: Path) -> List[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def _clean_lines(lines: Iterable[str]) -> List[str]:
    cleaned = []
    for line in lines:
        compact = _normalize_inline_text(" ".join(line.split()))
        if compact:
            cleaned.append(compact)
    return cleaned


def _normalize_inline_text(text: str) -> str:
    return re.sub(r"\s+([。！？；，、,.!?;:：])", r"\1", text)


def _split_long_paragraph(paragraph: str, max_chars: int) -> List[str]:
    sentences = re.findall(r".+?[。！？!?；;]|.+$", paragraph)
    chunks: List[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(sentence[i : i + max_chars] for i in range(0, len(sentence), max_chars))
        elif not current:
            current = sentence
        elif len(current) + len(sentence) <= max_chars:
            current += sentence
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


def _parse_hex_color(color: str) -> Tuple[int, int, int]:
    normalized = color.strip().lstrip("#")
    if len(normalized) != 6:
        raise ValueError("default_background_color must be #RRGGBB")
    return int(normalized[0:2], 16), int(normalized[2:4], 16), int(normalized[4:6], 16)


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


if __name__ == "__main__":
    main()
