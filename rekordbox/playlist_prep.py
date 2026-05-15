#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, cast


SUPPORTED_RETAINED_EXTENSIONS = {".aif", ".flac", ".mp3", ".wav", ".aiff"}
MAX_SAMPLE_RATE = 48_000
TARGET_BITS_PER_SAMPLE = 16
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
    aiff: bool
    down: bool
    clean: bool
    ffmpeg: str
    ffprobe: str
    yes: bool


class ProbeStream(TypedDict, total=False):
    codec_name: str
    sample_rate: str
    bit_rate: str
    bits_per_sample: str


class ProbePayload(TypedDict, total=False):
    streams: list[ProbeStream]


def parse_args() -> Config:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare audio files by running explicitly selected AIFF conversion, "
            "AIFF downconversion, and cleanup operations."
        )
    )
    _ = parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Root directory to scan recursively. Defaults to the current directory.",
    )
    _ = parser.add_argument(
        "--aiff",
        action="store_true",
        help="Convert .flac and .wav files to .aiff in the same directory.",
    )
    _ = parser.add_argument(
        "--down",
        action="store_true",
        help="Normalize .aiff files above 16-bit or 48000 Hz to 16-bit / 48000 Hz in place.",
    )
    _ = parser.add_argument(
        "--clean",
        action="store_true",
        help="Review unwanted extensions, .aif renames, and empty directories.",
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
        "--yes",
        action="store_true",
        help="Run selected operations without prompting.",
    )
    namespace = parser.parse_args()
    namespace_values = vars(namespace)

    aiff = bool_arg(namespace_values, "aiff", False)
    down = bool_arg(namespace_values, "down", False)
    clean = bool_arg(namespace_values, "clean", False)
    if not aiff and not down and not clean:
        parser.error("select at least one operation: --aiff, --down, or --clean")

    return Config(
        root=string_arg(namespace_values, "root", "."),
        aiff=aiff,
        down=down,
        clean=clean,
        ffmpeg=string_arg(namespace_values, "ffmpeg", "ffmpeg"),
        ffprobe=string_arg(namespace_values, "ffprobe", "ffprobe"),
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


def remove_empty_directories(root: Path, assume_yes: bool) -> int:
    removed = 0
    directories = sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True)
    for path in directories:
        if not assume_yes and not confirm_empty_directory_removal(path):
            print(f"Kept empty directory {path}.")
            continue

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


def load_json_object(raw_output: str) -> dict[str, object]:
    loaded = cast(object, json.loads(raw_output or "{}"))
    return string_keyed_dict(loaded) or {}


def string_keyed_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None

    mapping = cast(Mapping[object, object], value)
    normalized: dict[str, object] = {}
    for key, item in mapping.items():
        if isinstance(key, str):
            normalized[key] = item
    return normalized


def load_probe_payload(raw_output: str) -> ProbePayload:
    loaded = load_json_object(raw_output)
    raw_streams = loaded.get("streams")
    if not isinstance(raw_streams, list):
        return {}

    streams: list[ProbeStream] = []
    for item in cast(list[object], raw_streams):
        item_values = string_keyed_dict(item)
        if item_values is None:
            continue

        stream: ProbeStream = {}
        codec_name = item_values.get("codec_name")
        sample_rate = item_values.get("sample_rate")
        bit_rate = item_values.get("bit_rate")
        bits_per_sample = item_values.get("bits_per_sample")

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
    loaded = load_json_object(result.stdout)
    payload = string_keyed_dict(loaded.get("format"))
    if payload is None:
        return {}

    tags = string_keyed_dict(payload.get("tags"))
    if tags is None:
        return {}

    normalized: dict[str, str] = {}
    for key, value in tags.items():
        if isinstance(value, str):
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


def create_temporary_aiff_path(destination: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f"{destination.stem}.",
        suffix=".__tmp__.aiff",
        dir=destination.parent,
    )
    os.close(descriptor)
    return Path(name)


def ensure_regular_output(path: Path, description: str) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"{description} produced no usable AIFF output")


def convert_to_aiff_files(
    root: Path,
    ffmpeg_bin: str,
    ffprobe_bin: str,
    assume_yes: bool,
) -> tuple[int, int]:
    converted = 0
    kept_originals = 0
    source_files = sorted(iter_audio_files(root, {".flac", ".wav"}))

    if not source_files:
        print("No FLAC or WAV files found for AIFF conversion.")
        return converted, kept_originals

    for source_path in source_files:
        destination = source_path.with_suffix(".aiff")
        temporary_output: Path | None = None
        source_kind = source_path.suffix.lower().lstrip(".").upper()

        if destination.exists():
            print(f"Skipping {source_path}: destination already exists at {destination}.")
            continue

        if not assume_yes and not confirm_aiff_conversion(source_path, destination):
            print(f"Skipped AIFF conversion for {source_path}.")
            continue

        try:
            temporary_output = create_temporary_aiff_path(destination)
            metadata = probe_audio(source_path, ffprobe_bin)
            source_tags = tags_for_conversion(source_path, ffprobe_bin)
            codec = choose_pcm_codec(metadata)
            command = [
                ffmpeg_bin,
                "-nostdin",
                "-y",
                "-i",
                str(source_path),
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

            ensure_regular_output(temporary_output, "conversion")

            if source_tags is not None:
                destination_tags = read_tags(temporary_output, ffprobe_bin)
                differences = compare_tags(source_tags, destination_tags)
                if differences:
                    _ = temporary_output.replace(destination)
                    kept_originals += 1
                    print(
                        f"Converted {source_path} -> {destination}, but kept original {source_kind} because metadata verification found differences:",
                        file=sys.stderr,
                    )
                    for difference in differences:
                        print(f"  - {difference}", file=sys.stderr)
                    continue

            _ = temporary_output.replace(destination)
            source_path.unlink()
            converted += 1
            print(f"Converted {source_path} -> {destination} and deleted original {source_kind}.")
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, RuntimeError) as error:
            if temporary_output is not None and temporary_output.exists():
                temporary_output.unlink()
            print(f"Failed to convert {source_path} to AIFF: {error}", file=sys.stderr)

    return converted, kept_originals


def tags_for_conversion(path: Path, ffprobe_bin: str) -> dict[str, str] | None:
    if path.suffix.lower() == ".flac":
        return read_tags(path, ffprobe_bin)

    try:
        return read_tags(path, ffprobe_bin)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(
            f"Could not read WAV metadata for {path}; metadata verification will be skipped: {error}",
            file=sys.stderr,
        )
        return None


def clean_files(root: Path, assume_yes: bool) -> tuple[int, int, int]:
    deleted = 0
    renamed = 0
    aif_files = sorted(iter_audio_files(root, {".aif"}))
    unsupported_files = sorted(iter_unsupported_files(root))
    empty_directories = sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True)
    if not aif_files and not unsupported_files and not empty_directories:
        print("No files found for clean review.")

    for path in aif_files:
        destination = path.with_suffix(".aiff")
        reason = f"rename {path.suffix.lower()} to .aiff"

        if destination.exists():
            print(f"Skipping rename for {path}: destination already exists at {destination}.")
            continue

        if assume_yes or confirm_clean_rename(path, destination):
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
            f"extension {suffix} is not one of .wav, .aiff, .mp3, .flac, or .aif"
        )
        if assume_yes or confirm_clean_deletion(path, reason):
            try:
                path.unlink()
                deleted += 1
                print(f"Deleted {path} ({reason}).")
            except OSError as error:
                print(f"Failed to delete {path}: {error}", file=sys.stderr)
        else:
            print(f"Kept {path} ({reason}).")

    if deleted == 0:
        print("Clean review completed with no files deleted.")

    removed_directories = remove_empty_directories(root, assume_yes)
    if removed_directories == 0:
        print("No empty directories were removed.")

    return deleted, renamed, removed_directories


def downconvert_reason(metadata: AudioMetadata) -> str | None:
    reasons: list[str] = []
    if metadata.bits_per_sample is None:
        reasons.append("bit depth is unknown")
    elif metadata.bits_per_sample > TARGET_BITS_PER_SAMPLE:
        reasons.append(f"bit depth is {metadata.bits_per_sample}-bit")
    if metadata.sample_rate is None:
        reasons.append("sample rate is unknown")
    elif metadata.sample_rate > MAX_SAMPLE_RATE:
        reasons.append(f"sample rate is {metadata.sample_rate} Hz")

    if not reasons:
        return None

    return ", ".join(reasons) + " above 16-bit / 48000 Hz target"


def verify_downconverted_output(path: Path, ffprobe_bin: str) -> None:
    metadata = probe_audio(path, ffprobe_bin)
    if metadata.bits_per_sample is None:
        raise RuntimeError("downconverted output bit depth could not be verified")
    if metadata.bits_per_sample > TARGET_BITS_PER_SAMPLE:
        raise RuntimeError(f"downconverted output is still {metadata.bits_per_sample}-bit")
    if metadata.sample_rate is None:
        raise RuntimeError("downconverted output sample rate could not be verified")
    if metadata.sample_rate > MAX_SAMPLE_RATE:
        raise RuntimeError(f"downconverted output is still {metadata.sample_rate} Hz")


def downconvert_aiff_files(
    root: Path,
    ffmpeg_bin: str,
    ffprobe_bin: str,
    assume_yes: bool,
) -> int:
    downconverted = 0
    aiff_files = sorted(iter_audio_files(root, {".aiff"}))

    if not aiff_files:
        print("No AIFF files found for downconversion.")
        return downconverted

    for path in aiff_files:
        temporary_output: Path | None = None

        try:
            metadata = probe_audio(path, ffprobe_bin)
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
            print(f"Failed to inspect {path} for downconversion: {error}", file=sys.stderr)
            continue

        reason = downconvert_reason(metadata)
        if reason is None:
            continue

        if not assume_yes and not confirm_downconvert(path, reason):
            print(f"Skipped downconversion for {path} ({reason}).")
            continue

        try:
            temporary_output = create_temporary_aiff_path(path)
            command = [
                ffmpeg_bin,
                "-nostdin",
                "-y",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-vn",
                "-sn",
                "-dn",
                "-c:a",
                "pcm_s16be",
                "-ar",
                str(MAX_SAMPLE_RATE),
                "-map_metadata",
                "0",
                "-write_id3v2",
                "1",
                "-id3v2_version",
                "4",
                str(temporary_output),
            ]
            _ = subprocess.run(command, check=True)

            ensure_regular_output(temporary_output, "downconversion")

            verify_downconverted_output(temporary_output, ffprobe_bin)
            _ = temporary_output.replace(path)
            downconverted += 1
            print(f"Downconverted {path} to 16-bit / 48000 Hz AIFF.")
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, RuntimeError) as error:
            if temporary_output is not None and temporary_output.exists():
                temporary_output.unlink()
            print(f"Failed to downconvert {path}: {error}", file=sys.stderr)

    return downconverted


def confirm_clean_deletion(path: Path, reason: str) -> bool:
    prompt = f"Clean by deleting {path} ({reason})? [y/N]: "
    return input(prompt).strip().lower() in {"y", "yes"}


def confirm_clean_rename(source: Path, destination: Path) -> bool:
    prompt = f"Clean by renaming {source} to {destination}? [y/N]: "
    return input(prompt).strip().lower() in {"y", "yes"}


def confirm_empty_directory_removal(path: Path) -> bool:
    prompt = f"Clean by removing empty directory {path}? [y/N]: "
    return input(prompt).strip().lower() in {"y", "yes"}


def confirm_aiff_conversion(source: Path, destination: Path) -> bool:
    source_kind = source.suffix.lower().lstrip(".").upper()
    prompt = f"Convert {source} to AIFF at {destination} and delete the original {source_kind} after success? [y/N]: "
    return input(prompt).strip().lower() in {"y", "yes"}


def confirm_downconvert(path: Path, reason: str) -> bool:
    prompt = f"Downconvert {path} in place to 16-bit / 48000 Hz AIFF ({reason})? [y/N]: "
    return input(prompt).strip().lower() in {"y", "yes"}


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()

    if not root.exists() or not root.is_dir():
        print(f"Root path must be an existing directory: {root}", file=sys.stderr)
        return 1

    if args.aiff or args.down:
        ensure_tool("ffmpeg", args.ffmpeg)
        ensure_tool("ffprobe", args.ffprobe)

    if args.aiff:
        print(f"Starting AIFF conversion in {root}...")
        converted, kept_originals = convert_to_aiff_files(
            root,
            args.ffmpeg,
            args.ffprobe,
            args.yes,
        )
        conversion_message = (
            f"AIFF conversion complete. Converted {converted} source file(s) "
            + f"and kept {kept_originals} original file(s) after metadata verification."
        )
        print(conversion_message)

    if args.down:
        print(f"Starting AIFF downconversion in {root}...")
        downconverted = downconvert_aiff_files(
            root,
            args.ffmpeg,
            args.ffprobe,
            args.yes,
        )
        downconvert_message = (
            f"AIFF downconversion complete. Normalized {downconverted} AIFF file(s) "
            + "to 16-bit / 48000 Hz."
        )
        print(downconvert_message)

    if args.clean:
        print(f"Starting clean operation in {root}...")
        deleted, renamed, removed_directories = clean_files(root, args.yes)
        clean_message = (
            f"Clean operation complete. Deleted {deleted} file(s), renamed {renamed} .aif file(s), "
            + f"and removed {removed_directories} empty directorie(s)."
        )
        print(clean_message)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
