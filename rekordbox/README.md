# Playlist Preparation Tool

`playlist_prep.py` is a Python utility for scanning a directory tree of audio files and running the operations you explicitly select.

## What it does

### AIFF conversion

- Recursively finds `.flac` and `.wav` files when `--aiff` is selected.
- Prompts before each conversion by default.
- Converts each source file to an `.aiff` file in the same folder.
- Uses a temporary AIFF file first, then moves it into place.
- Skips a source file if the destination AIFF already exists.
- Deletes the original source only after a successful conversion.
- Copies metadata on a best-effort basis with `ffmpeg` and writes ID3v2 tags into the AIFF output.
- Always verifies FLAC vs. AIFF tags with `ffprobe` before deleting the original FLAC.
- Attempts metadata verification for WAV conversions when tags can be read.

### AIFF downconversion

- Recursively finds `.aiff` files when `--down` is selected.
- Prompts before each in-place normalization by default.
- Normalizes AIFF files to 16-bit / 48000 Hz when their bit depth is above 16-bit, their sample rate is above 48000 Hz, or either value cannot be inspected.
- Writes a temporary AIFF output first and replaces the original only after a successful regular output is produced and inspected.

### Clean review

- Reviews deletion and rename candidates when `--clean` is selected.
- Prompts before deleting flagged files, renaming `.aif` files, or removing empty directories by default.
- Renames `.aif` files to `.aiff` unless the target `.aiff` file already exists.
- Deletes files whose extensions are not `.wav`, `.aiff`, `.mp3`, `.flac`, or `.aif`.
- Removes directories that are empty after the clean operation finishes when confirmed or `--yes` is used.

### ReplayGain metadata stripping

- Recursively finds `.aif`, `.aiff`, `.flac`, `.mp3`, and `.wav` files when `--strip-replaygain` is selected.
- Prompts before rewriting each file that has ReplayGain metadata by default.
- Removes format-level ReplayGain tags without re-encoding the audio stream.
- Writes a temporary output first and replaces the original only after `ffprobe` confirms no ReplayGain tags remain.

## Requirements

- Python 3
- `ffmpeg`
- `ffprobe`

Both `ffmpeg` and `ffprobe` must be available in your `PATH` for `--aiff`, `--down`, and `--strip-replaygain`, unless you pass custom paths with command-line options. `--clean` does not require either tool.

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

Select at least one operation. Running with only `root` prints an argparse error instead of modifying files.

Convert FLAC and WAV files to AIFF:

```bash
playlist_prep "/path/to/audio" --aiff
```

Normalize high-resolution AIFF files to 16-bit / 48000 Hz in place:

```bash
playlist_prep "/path/to/audio" --down
```

Review unwanted file extensions, `.aif` renames, and empty directories:

```bash
playlist_prep "/path/to/audio" --clean
```

Strip ReplayGain metadata tags:

```bash
playlist_prep "/path/to/audio" --strip-replaygain
```

Run multiple operations in order:

```bash
playlist_prep "/path/to/audio" --aiff --down --strip-replaygain --clean
```

Skip prompts for all selected operations:

```bash
playlist_prep "/path/to/audio" --aiff --down --strip-replaygain --clean --yes
```

Use custom `ffmpeg` or `ffprobe` binaries:

```bash
playlist_prep "/path/to/audio" --aiff --ffmpeg "/custom/bin/ffmpeg" --ffprobe "/custom/bin/ffprobe"
```

## Command-line options

- `root` - root directory to scan recursively. Defaults to the current directory.
- `--aiff` - convert `.flac` and `.wav` files to `.aiff` in the same directory.
- `--down` - normalize `.aiff` files above 16-bit or 48000 Hz, or with unknown bit depth/sample rate, to 16-bit / 48000 Hz in place.
- `--clean` - review unwanted extensions, `.aif` renames, and empty directories.
- `--strip-replaygain` - remove ReplayGain metadata tags from supported audio files without re-encoding audio.
- `--ffmpeg` - path to the `ffmpeg` executable.
- `--ffprobe` - path to the `ffprobe` executable.
- `--yes` - run selected operations without prompting.

## Notes

- AIFF conversion output is written next to the source FLAC or WAV.
- The script only deletes original FLAC and WAV files after successful conversion output is produced.
- The script always compares source and converted tags for FLAC conversions. If differences are found, it keeps both the original FLAC and the new AIFF and reports the mismatches.
- WAV metadata verification is best-effort. If useful tags can be read, they are compared before the original WAV is removed.
- The metadata comparison ignores a small set of tool-generated fields such as `encoder`, `encoded_by`, `software`, and `creation_time`.
- Downconversion uses a temporary output file and replaces the original AIFF only after successful output is produced and `ffprobe` confirms its bit depth and sample rate.
- AIFF files with unknown bit depth or sample rate are treated as downconversion candidates, but the replacement output must still be verifiable.
- ReplayGain stripping removes tags whose names are `REPLAYGAIN` or begin with `REPLAYGAIN_` / `REPLAYGAIN-`, case-insensitively.
- ReplayGain stripping copies audio streams instead of re-encoding them.
- If `ffprobe` cannot read a file, the script reports the error and moves on.
- `.aif` files are renamed to `.aiff` during `--clean` unless the target `.aiff` file already exists.
- Files with extensions other than `.wav`, `.aiff`, `.mp3`, `.flac`, and `.aif` are reviewed for deletion without using `ffprobe`.
- Empty directories left behind after `--clean` are reviewed for removal.

## Troubleshooting

### `ffmpeg` or `ffprobe` not found

If the script exits immediately saying a required tool was not found, install `ffmpeg` and make sure both `ffmpeg` and `ffprobe` are in your `PATH`.

You can also point to custom binaries:

```bash
playlist_prep "/path/to/audio" --aiff --ffmpeg "/custom/bin/ffmpeg" --ffprobe "/custom/bin/ffprobe"
```

### A source file is skipped during AIFF conversion

The script skips conversion when the destination `.aiff` file already exists.

### Metadata verification reports differences

The script compares source and converted tags using `ffprobe` on every FLAC conversion and on WAV conversions when metadata can be read.

When differences are found, the script:

- keeps the converted AIFF,
- keeps the original source file,
- prints the missing or changed tags.

This is intentional, because AIFF metadata support is more limited than FLAC or WAV metadata support.

The comparison intentionally ignores a few tags that often change during transcoding even when the useful metadata is still intact, such as encoder or software-identification fields.

### A file cannot be inspected

If `ffprobe` cannot read a file, the script prints an error and continues with the next file. This usually means the file is damaged, unsupported, or not actually an audio file despite its extension.

### Nothing is deleted

That can mean either:

- no files matched the clean deletion rules, or
- you answered no to every clean deletion prompt.

Files with the `.aif` extension are handled separately and renamed to `.aiff` instead of being deleted.

Directories are only removed when they are empty after all selected operations are complete.
