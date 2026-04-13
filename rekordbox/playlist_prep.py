#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict


SUPPORTED_RETAINED_EXTENSIONS = {".flac", ".mp3", ".wav", ".aiff"}
MAX_MP3_BITRATE = 320_000
MAX_SAMPLE_RATE = 48_000
IGNORED_METADATA_KEYS = {
    "encoder",
    "encoded_by",
    "software",
    "compatible_brands",
    "minor_version",
    "major_brand",
    "creation_time",
}


@dataclass(frozen=True)
class AudioMetadata:
    path: Path
    codec_name: str | None
    sample_rate: int | None
    bit_rate: int | None
    bits_per_sample: int | None


@dataclass(frozen=True)
class Config:
    root: str
    phase: str
    ffmpeg: str
    ffprobe: str
    yes_convert: bool
    yes: bool


class ProbeStream(TypedDict, total=False):
    codec_name: str
    sample_rate: str
    bit_rate: str
    bits_per_sample: str


class ProbePayload(TypedDict, total=False):
    streams: list[ProbeStream]


class TagPayload(TypedDict, total=False):
    format: dict[str, object]


def parse_args() -> Config:
    parser = argparse.ArgumentParser(
        description=(
            "Convert FLAC files to AIFF in place, then review unsupported files, low-bitrate MP3 files, and high-sample-rate WAV/AIFF files."
        )
    )
    _ = parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Root directory to scan recursively. Defaults to the current directory.",
    )
    _ = parser.add_argument(
        "--phase",
        choices=("all", "convert", "delete"),
        default="all",
        help="Run only the conversion phase, only the deletion-review phase, or both in order.",
    )
    _ = parser.add_argument(
        "--ffmpeg",
        default="ffmpeg",
        help="Path to the ffmpeg executable. Defaults to 'ffmpeg'.",
    )
    _ = parser.add_argument(
        "--ffprobe",
        default="ffprobe",
        help="Path to the ffprobe executable. Defaults to 'ffprobe'.",
    )
    _ = parser.add_argument(
        "--yes-convert",
        action="store_true",
        help="Convert FLAC files without prompting during the conversion phase.",
    )
    _ = parser.add_argument(
        "--yes",
        action="store_true",
        help="Delete flagged files without prompting during the deletion-review phase.",
    )
    namespace = parser.parse_args()
    namespace_values = vars(namespace)
    return Config(
        root=string_arg(namespace_values, "root", "."),
        phase=string_arg(namespace_values, "phase", "all"),
        ffmpeg=string_arg(namespace_values, "ffmpeg", "ffmpeg"),
        ffprobe=string_arg(namespace_values, "ffprobe", "ffprobe"),
        yes_convert=bool_arg(namespace_values, "yes_convert", False),
        yes=bool_arg(namespace_values, "yes", False),
    )


def ensure_tool(name: str, configured_path: str) -> None:
    if shutil.which(configured_path) is None:
        raise SystemExit(f"Required tool '{configured_path}' for {name} was not found in PATH.")


def iter_audio_files(root: Path, suffixes: Iterable[str]) -> Iterable[Path]:
    wanted = {suffix.lower() for suffix in suffixes}
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in wanted:
            yield path


def iter_unsupported_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() not in SUPPORTED_RETAINED_EXTENSIONS:
            yield path


def remove_empty_directories(root: Path) -> int:
    removed = 0
    directories = sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True)
    for path in directories:
        try:
            path.rmdir()
            removed += 1
            print(f"Removed empty directory {path}.")
        except OSError:
            continue
    return removed


def probe_audio(path: Path, ffprobe_bin: str) -> AudioMetadata:
    command = [
        ffprobe_bin,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_name,sample_rate,bit_rate,bits_per_sample",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    payload = load_probe_payload(result.stdout)
    streams = payload.get("streams", [])
    stream: ProbeStream = streams[0] if streams else {}

    return AudioMetadata(
        path=path,
        codec_name=stream.get("codec_name"),
        sample_rate=parse_int(stream.get("sample_rate")),
        bit_rate=parse_int(stream.get("bit_rate")),
        bits_per_sample=parse_int(stream.get("bits_per_sample")),
    )


def parse_int(value: object) -> int | None:
    if value in (None, "N/A", ""):
        return None

    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def string_arg(values: dict[str, object], key: str, default: str) -> str:
    value = values.get(key)
    return value if isinstance(value, str) else default


def bool_arg(values: dict[str, object], key: str, default: bool) -> bool:
    value = values.get(key)
    return value if isinstance(value, bool) else default


def load_probe_payload(raw_output: str) -> ProbePayload:
    loaded: object = json.loads(raw_output or "{}")
    if not isinstance(loaded, dict):
        return {}

    raw_streams = loaded.get("streams")
    if not isinstance(raw_streams, list):
        return {}

    streams: list[ProbeStream] = []
    for item in raw_streams:
        if not isinstance(item, dict):
            continue

        stream: ProbeStream = {}
        codec_name = item.get("codec_name")
        sample_rate = item.get("sample_rate")
        bit_rate = item.get("bit_rate")
        bits_per_sample = item.get("bits_per_sample")

        if isinstance(codec_name, str):
            stream["codec_name"] = codec_name
        if isinstance(sample_rate, str):
            stream["sample_rate"] = sample_rate
        if isinstance(bit_rate, str):
            stream["bit_rate"] = bit_rate
        if isinstance(bits_per_sample, str):
            stream["bits_per_sample"] = bits_per_sample

        streams.append(stream)

    return {"streams": streams}


def read_tags(path: Path, ffprobe_bin: str) -> dict[str, str]:
    command = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format_tags",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    loaded: object = json.loads(result.stdout or "{}")
    if not isinstance(loaded, dict):
        return {}

    payload = loaded.get("format")
    if not isinstance(payload, dict):
        return {}

    tags = payload.get("tags")
    if not isinstance(tags, dict):
        return {}

    normalized: dict[str, str] = {}
    for key, value in tags.items():
        if isinstance(key, str) and isinstance(value, str):
            normalized[key.lower()] = value
    return normalized


def compare_tags(source: dict[str, str], destination: dict[str, str]) -> list[str]:
    differences: list[str] = []
    for key in sorted(source):
        if key in IGNORED_METADATA_KEYS:
            continue
        source_value = source[key]
        destination_value = destination.get(key)
        if destination_value is None:
            differences.append(f"missing tag '{key}' (source={source_value!r})")
        elif destination_value != source_value:
            differences.append(
                f"changed tag '{key}' (source={source_value!r}, destination={destination_value!r})"
            )
    return differences


def choose_pcm_codec(metadata: AudioMetadata) -> str:
    if metadata.bits_per_sample and metadata.bits_per_sample > 16:
        return "pcm_s24be"
    return "pcm_s16be"


def convert_flac_files(
    root: Path,
    ffmpeg_bin: str,
    ffprobe_bin: str,
    assume_yes: bool,
) -> int:
    converted = 0
    flac_files = sorted(iter_audio_files(root, {".flac"}))

    if not flac_files:
        print("No FLAC files found for conversion.")
        return converted

    for flac_path in flac_files:
        destination = flac_path.with_suffix(".aiff")
        temporary_output = destination.with_name(f"{destination.stem}.__tmp__.aiff")

        if destination.exists():
            print(f"Skipping {flac_path}: destination already exists at {destination}.")
            continue

        if temporary_output.exists():
            print(f"Skipping {flac_path}: temporary file already exists at {temporary_output}.")
            continue

        if not assume_yes and not confirm_conversion(flac_path, destination):
            print(f"Skipped conversion for {flac_path}.")
            continue

        try:
            metadata = probe_audio(flac_path, ffprobe_bin)
            source_tags = read_tags(flac_path, ffprobe_bin)
            codec = choose_pcm_codec(metadata)
            command = [
                ffmpeg_bin,
                "-nostdin",
                "-y",
                "-i",
                str(flac_path),
                "-map",
                "0:a:0",
                "-vn",
                "-sn",
                "-dn",
                "-c:a",
                codec,
                "-map_metadata",
                "0",
                "-write_id3v2",
                "1",
                "-id3v2_version",
                "4",
                str(temporary_output),
            ]
            _ = subprocess.run(command, check=True)

            if not temporary_output.exists() or temporary_output.stat().st_size == 0:
                raise RuntimeError("conversion produced no usable AIFF output")

            destination_tags = read_tags(temporary_output, ffprobe_bin)
            differences = compare_tags(source_tags, destination_tags)
            if differences:
                _ = temporary_output.replace(destination)
                print(
                    f"Converted {flac_path} -> {destination}, but kept original FLAC because metadata verification found differences:",
                    file=sys.stderr,
                )
                for difference in differences:
                    print(f"  - {difference}", file=sys.stderr)
                continue

            _ = temporary_output.replace(destination)
            flac_path.unlink()
            converted += 1
            print(f"Converted {flac_path} -> {destination} and deleted original FLAC.")
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, RuntimeError) as error:
            if temporary_output.exists():
                temporary_output.unlink()
            print(f"Failed to convert {flac_path}: {error}", file=sys.stderr)

    return converted


def review_deletions(root: Path, ffprobe_bin: str, assume_yes: bool) -> tuple[int, int, int]:
    deleted = 0
    renamed = 0
    aif_files = sorted(iter_audio_files(root, {".aif"}))
    unsupported_files = sorted(iter_unsupported_files(root))
    candidates = sorted(iter_audio_files(root, {".mp3", ".wav", ".aiff"}))

    if not aif_files and not unsupported_files and not candidates:
        print("No files found for deletion review.")

    for path in aif_files:
        destination = path.with_suffix(".aiff")
        reason = f"rename {path.suffix.lower()} to .aiff"

        if destination.exists():
            print(f"Skipping rename for {path}: destination already exists at {destination}.")
            continue

        if assume_yes or confirm_rename(path, destination):
            try:
                _ = path.rename(destination)
                renamed += 1
                print(f"Renamed {path} -> {destination}.")
            except OSError as error:
                print(f"Failed to rename {path} to {destination}: {error}", file=sys.stderr)
        else:
            print(f"Kept {path} ({reason}).")

    for path in unsupported_files:
        suffix = path.suffix.lower() or "[no extension]"
        reason = (
            f"extension {suffix} is not one of .wav, .aiff, .mp3, or .flac"
        )
        if assume_yes or confirm_deletion(path, reason):
            try:
                path.unlink()
                deleted += 1
                print(f"Deleted {path} ({reason}).")
            except OSError as error:
                print(f"Failed to delete {path}: {error}", file=sys.stderr)
        else:
            print(f"Kept {path} ({reason}).")

    for path in candidates:
        try:
            metadata = probe_audio(path, ffprobe_bin)
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
            print(f"Failed to inspect {path}: {error}", file=sys.stderr)
            continue

        reason = deletion_reason(path, metadata)
        if reason is None:
            continue

        if assume_yes or confirm_deletion(path, reason):
            try:
                path.unlink()
                deleted += 1
                print(f"Deleted {path} ({reason}).")
            except OSError as error:
                print(f"Failed to delete {path}: {error}", file=sys.stderr)
        else:
            print(f"Kept {path} ({reason}).")

    if deleted == 0:
        print("Deletion review completed with no files deleted.")

    removed_directories = remove_empty_directories(root)
    if removed_directories == 0:
        print("No empty directories were removed.")

    return deleted, renamed, removed_directories


def deletion_reason(path: Path, metadata: AudioMetadata) -> str | None:
    suffix = path.suffix.lower()

    if suffix == ".mp3" and metadata.bit_rate is not None and metadata.bit_rate < MAX_MP3_BITRATE:
        return f"MP3 bitrate is {metadata.bit_rate} bps, below 320000 bps"

    if suffix in {".wav", ".aiff"} and metadata.sample_rate is not None and metadata.sample_rate > MAX_SAMPLE_RATE:
        return f"sample rate is {metadata.sample_rate} Hz, above 48000 Hz"

    return None


def confirm_deletion(path: Path, reason: str) -> bool:
    prompt = f"Delete {path} ({reason})? [y/N]: "
    return input(prompt).strip().lower() in {"y", "yes"}


def confirm_rename(source: Path, destination: Path) -> bool:
    prompt = f"Rename {source} to {destination}? [y/N]: "
    return input(prompt).strip().lower() in {"y", "yes"}


def confirm_conversion(source: Path, destination: Path) -> bool:
    prompt = f"Convert {source} to {destination} and delete the original FLAC after success? [y/N]: "
    return input(prompt).strip().lower() in {"y", "yes"}


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()

    if not root.exists() or not root.is_dir():
        print(f"Root path must be an existing directory: {root}", file=sys.stderr)
        return 1

    ensure_tool("ffmpeg", args.ffmpeg)
    ensure_tool("ffprobe", args.ffprobe)

    if args.phase in {"all", "convert"}:
        print(f"Starting conversion phase in {root}...")
        converted = convert_flac_files(
            root,
            args.ffmpeg,
            args.ffprobe,
            args.yes_convert,
        )
        print(f"Conversion phase complete. Converted {converted} FLAC file(s).")

    if args.phase == "all":
        print("Starting deletion-review phase...")

    if args.phase in {"all", "delete"}:
        deleted, renamed, removed_directories = review_deletions(root, args.ffprobe, args.yes)
        print(f"Deletion-review phase complete. Deleted {deleted} file(s), renamed {renamed} .aif file(s), and removed {removed_directories} empty directorie(s).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
