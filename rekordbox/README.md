# Playlist Preparation Tool

`playlist_prep.py` is a Python utility for scanning a directory tree of audio files in two phases:

1. Convert `.flac` files to `.aiff` in the same directory.
2. Review certain files for deletion.

## What it does

### Phase 1: Conversion

- Recursively finds `.flac` files.
- Prompts before each conversion by default.
- Converts each FLAC to an AIFF file in the same folder.
- Uses a temporary AIFF file first, then moves it into place.
- Deletes the original FLAC only after a successful conversion.
- Skips a FLAC if the destination AIFF already exists.
- Copies metadata on a best-effort basis with `ffmpeg` and writes ID3v2 tags into the AIFF output.
- Always verifies FLAC vs. AIFF tags with `ffprobe` before deleting the original FLAC.

### Phase 2: Deletion review

- Prompts before deleting flagged files by default.
- Flags `.mp3` files with bitrate below `320000` bps.
- Flags `.wav`, `.aif`, and `.aiff` files with sample rate above `48000` Hz.

## Requirements

- Python 3
- `ffmpeg`
- `ffprobe`

Both `ffmpeg` and `ffprobe` must be available in your `PATH`, unless you pass custom paths with command-line options.

## Installation

1. Make sure Python 3 is installed.
2. Install `ffmpeg`, which also provides `ffprobe` in most distributions.
3. Keep `playlist_prep.py` and the `playlist_prep` launcher in the same directory.
4. Make the launcher executable:

```bash
chmod +x playlist_prep
```

5. Optional: symlink the launcher into `/usr/local/bin` so you can run it from anywhere:

```bash
ln -s "/full/path/to/playlist_prep" /usr/local/bin/playlist_prep
```

You can also run the Python script directly with an absolute path if you prefer.

You can verify the external tools are available with:

```bash
ffmpeg -version
ffprobe -version
```

## Usage

Run both phases in order:

```bash
playlist_prep "/path/to/audio"
```

Run only the conversion phase:

```bash
playlist_prep "/path/to/audio" --phase convert
```

Run only the deletion-review phase:

```bash
playlist_prep "/path/to/audio" --phase delete
```

Skip conversion prompts:

```bash
playlist_prep "/path/to/audio" --yes-convert
```

Skip deletion prompts:

```bash
playlist_prep "/path/to/audio" --yes
```

Skip both conversion and deletion prompts:

```bash
playlist_prep "/path/to/audio" --yes-convert --yes
```

## Command-line options

- `root` - root directory to scan recursively. Defaults to the current directory.
- `--phase {all,convert,delete}` - choose which phase to run. Default is `all`.
- `--ffmpeg` - path to the `ffmpeg` executable.
- `--ffprobe` - path to the `ffprobe` executable.
- `--yes-convert` - convert FLAC files without prompting.
- `--yes` - delete flagged files without prompting.

## Notes

- AIFF output is written next to the original FLAC.
- The script only deletes original FLAC files after a successful conversion.
- If `ffprobe` cannot read a file, the script reports the error and moves on.
- Bitrate checks are only applied to MP3 files.
- Sample-rate checks are only applied to WAV and AIFF files.
- Metadata retention during FLAC to AIFF conversion is best-effort. Native AIFF tags are limited, so some fields may only survive in the AIFF ID3 chunk.
- The script always compares source and converted tags. If differences are found, it keeps both the original FLAC and the new AIFF and reports the mismatches.
- The metadata comparison ignores a small set of tool-generated fields such as `encoder`, `encoded_by`, `software`, and `creation_time`.

## Troubleshooting

### `ffmpeg` or `ffprobe` not found

If the script exits immediately saying a required tool was not found, install `ffmpeg` and make sure both `ffmpeg` and `ffprobe` are in your `PATH`.

You can also point to custom binaries:

```bash
playlist_prep "/path/to/audio" --ffmpeg "/custom/bin/ffmpeg" --ffprobe "/custom/bin/ffprobe"
```

### A FLAC file is skipped

The script skips conversion when:

- the destination `.aiff` file already exists, or
- a temporary AIFF file from an earlier interrupted run is still present.

In the second case, inspect the temporary file and remove it if you no longer need it.

### Metadata verification reports differences

The script compares source and converted tags using `ffprobe` on every FLAC conversion.

When differences are found, the script:

- keeps the converted AIFF,
- keeps the original FLAC,
- prints the missing or changed tags.

This is intentional, because AIFF metadata support is more limited than FLAC metadata support.

The comparison intentionally ignores a few tags that often change during transcoding even when the useful metadata is still intact, such as encoder or software-identification fields.

### A file cannot be inspected

If `ffprobe` cannot read a file, the script prints an error and continues with the next file. This usually means the file is damaged, unsupported, or not actually an audio file despite its extension.

### Nothing is deleted

That can mean either:

- no files matched the deletion rules, or
- you answered no to every deletion prompt.
