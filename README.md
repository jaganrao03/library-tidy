# library-tidy

Turn a drive full of accumulated files into a library you can actually navigate.

One rule does the work: **`DATE - PARTY - TYPE - TOPIC.ext`**.

```
2024-10-01 08.24.38 - OnePlus 11 - Photo.jpg
2024-06-12 - IRS - Tax Form - 1040 Federal Return.pdf
2021-Fall - University of Memphis - Notes - Week 03 Lecture.txt
Undated - Me - Archive - Old Resume Bundle.zip
```

Every file sorts chronologically in every file manager, on every device, forever.
The date is the first thing you see, so a folder becomes a timeline instead of a
pile. That's the whole idea — the tooling below just makes adopting it survivable
on a library of thousands of files.

## Why not just `rename` it all

Batch renamers destroy data in two ways:

1. **Silent overwrite.** On POSIX, `Path.rename` replaces an existing file and
   returns success. Any two sources that map to one destination lose a file with
   no error. (This tool claims every target before moving anything, and refuses
   to run if a plan has a duplicate target.)
2. **No way back.** A rename without a journal is irreversible. Every change here
   is written to a JSONL journal with both paths, and `undo` walks it backwards.

Both rules exist because ignoring them cost real files during the development of
this tool. See [SAFETY.md](SAFETY.md).

## Install

```bash
pip install pillow          # optional: EXIF dates + thumbnail gallery
sudo pacman -S ffmpeg       # optional: video creation_time dates
git clone https://github.com/jaganrao03/library-tidy && cd library-tidy
python -m library_tidy --help
```

No packaging required — it runs straight from the checkout with the standard
library, plus Pillow/ffmpeg when you want embedded dates or previews.

## Use it in the order that's safe

```bash
# 1. Look before you touch. Read-only: naming, integrity, duplicates, hygiene.
python -m library_tidy audit ~/library --json audit.json

# 2. See what would change. Nothing moves without --apply.
python -m library_tidy plan  ~/library --kind photo
python -m library_tidy plan  ~/library --kind video
python -m library_tidy plan  ~/library --kind document --party "Me"

# 3. Apply, journalled.
python -m library_tidy apply ~/library --kind photo --apply

# 4. Regret it? Journals reverse in one command.
python -m library_tidy undo "~/library/_Library Metadata/rename-20261101T195604.jsonl" --apply
```

Then repair the two things an audit always finds:

```bash
# Files whose extension lies: a ".jpg" that is really WebP or HEIC is why phones
# show blank tiles. Renaming to the true extension is lossless (no re-encode).
python -m library_tidy fixext ~/library --apply

# Files whose date-shaped parent folder disagrees with their own date.
python -m library_tidy resort ~/library --apply

# Duplicates: quarantined into _Duplicates/, never deleted.
python -m library_tidy dedupe ~/library
python -m library_tidy dedupe ~/library --apply
```

## Verify from a phone

WebDAV and SMB clients decide for themselves whether to fetch thumbnails, and
many simply don't for network shares — so a perfectly organised library looks
like a wall of blank tiles. Serve the JPEGs yourself:

```bash
python -m library_tidy gallery ~/library --port 8451 --user me --password secret --title "My Library"
```

`/` is a mobile grid with folder browsing, a "Photos only" contact sheet of the
whole subtree, and tap-to-full-size. `/data.json` is the inventory, `/thumb` is a
400 px JPEG cache, `/full` streams originals with Range support. Binds to
127.0.0.1 by default; put it behind `tailscale serve` or an SSH tunnel rather than
exposing it.

## What the audit reports

- **naming** — files that don't match the convention (with the paths)
- **integrity** — zero-byte files, extensions contradicted by the file's magic bytes
- **hygiene** — illegal characters, over-long names, empty directories
- **duplicates** — grouped by content hash (sizes first, so it only hashes files that could actually collide)

Exit codes make it a gate: `audit --strict` and `check` return non-zero when
something is off-convention, so it drops into cron or CI unchanged.

## Commands

| command | does |
|---|---|
| `audit` | read-only report: naming, integrity, duplicates, hygiene |
| `plan` / `apply` | rename photos, videos or documents (dry run by default) |
| `undo` | reverse a journal, newest first |
| `fixext` | rename files to the extension their bytes prove |
| `resort` | move files into the date folder their name claims |
| `dedupe` | quarantine duplicate copies into `_Duplicates/` |
| `gallery` | read-only web view with thumbnails |
| `check` | compliance gate for cron/CI |

## What it deliberately does not do

- **Music libraries.** `Artist - Album (Year)/NN - Track` is a convention too —
  renaming tracks breaks cue sheets, `.m3u` playlists and artwork lookup. Excluded
  by design; `--skip-dir "Music"` and leave it alone.
- **Code.** `.py`, `.ipynb`, `.js`, `package.json`, `index.html` are imported by
  name. Files inside `Programs/`, `Code/`, `src/`, `node_modules/` are skipped.
- **Delete anything.** Deduplication moves copies to `_Duplicates/`. You delete.
- **Invent dates.** No date recoverable from the name, EXIF or container metadata
  means `Undated`, not "the day it was copied".

## Licence

MIT.
