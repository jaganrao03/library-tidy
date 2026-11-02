# The convention

```
DATE - PARTY - TYPE - TOPIC.ext
```

Four fields, always in that order, always separated by ` - ` (space hyphen space).
Any field may be dropped from the right; the first two may not.

## DATE

Five accepted shapes, in descending precision:

| shape | example | use |
|---|---|---|
| `YYYY-MM-DD HH.MM.SS` | `2024-10-01 08.24.38` | photos and videos with a real timestamp |
| `YYYY-MM-DD` | `2024-10-01` | documents, any file with a known day |
| `YYYY-MM` | `2024-10` | a month, when the day is unknown |
| `YYYY-Term` | `2021-Fall` | academic or quarterly material |
| `YYYY` | `2024` | only the year is known |
| `Undated` | `Undated` | nothing recoverable — never guess |

Times use dots, not colons: `08.24.38`. Colons are illegal on Windows and SMB
shares, and a name that can't be copied to a share is not portable.

Dates sort lexicographically exactly when formatted this way — that is the entire
point. `2024-10-01` sorts before `2024-10-02`, and both sort before `Undated`
(which is where unknown-dated material belongs: at the end, not scattered).

## PARTY

Who or what the file belongs to: a person's name, an institution, a company, a
government body — short and consistent.

```
2024-06-12 - IRS - Tax Form - 1040 Federal Return.pdf
2022-03-09 - University of Memphis - I-20 - Signed Copy.pdf
2024-10-01 08.24.38 - OnePlus 11 - Photo.jpg
```

For your own documents, pick one token (`Me`, your name initials) and never vary
it. Consistency is what makes `- Party -` filterable.

## TYPE

The noun for what the file *is*, not what it's about:

`Photo` · `Screenshot` · `Screen Recording` · `Video` · `Wallpaper` ·
`Document` · `Notes` · `Spreadsheet` · `Data` · `Presentation` · `Diagram` ·
`Scan` · `Tax Form` · `Receipt` · `Statement` · `Contract` · `Archive` ·
`Backup` · `Design Source` · `Email` · `Signature` · `Lecture`

Derive it from the folder when the folder already knows (`Screenshots/` →
`Screenshot`), otherwise from the extension. A small controlled vocabulary is
worth more than precision.

## TOPIC

Free text, human-readable, and the only field allowed to be creative. Strip the
machine tokens first: camera counters (`IMG_2043`), download-bot suffixes
(`someaccount-20240917-0001`), `(1)` copy markers, `Final`/`Copy` noise.

```
Undated - Photo - IMG_6476.jpg              ✗ machine name wearing a date
2024-08-17 14.03.56 - Photo - OnePlus 11.jpg ✓
```

## Folders

The convention is about names; folders are about scale. When a folder already
carries a date, it must agree with the files inside it — a `2024-10/` directory
holding September files is a lie that costs you the file when you go looking.
`resort` fixes exactly that, and nothing else.

```
Photos and Images/
  Phone-OnePlus-11/2024-09/2024-09-29 05.05.31 - Video - OnePlus 11.mov
  Screenshots/2026/07/2026-07-24 - Quickshell crash report submission.png
```

## Exemptions, and why they're not laziness

| area | convention | reason |
|---|---|---|
| Music | `Artist - Album (Year)/NN - Track` + `cover.jpg`, `.cue`, `.log` | cue sheets and playlist files reference track paths; decoration breaks them |
| Code | as written | imports, routing and build config resolve by name |
| App config | as written | `.env`, `qbittorrent.conf`, `.gitignore` are read by name |
| Lightroom catalogs | as written | `.lrcat-data` internals are addressed by the catalog database |

A convention that fights the software reading those files is not a convention,
it's a breakage.

## Naming rules that prevent real problems

- **No `:` `"` `<` `>` `|` `?` `*` `/` `\`** — Windows, SMB, and URL-encoding all
  disagree about them.
- **No `&` or `#` when files are served over HTTP.** They are legal on disk and
  break naive URL construction; sanitise at the boundary or avoid them.
- **Keep names under ~180 characters.** Deep academic paths plus long topics hit
  filesystem and client limits fast.
- **ASCII for subjects of emails and anything crossing a shell** — an em dash
  becomes mojibake in a mail header.
