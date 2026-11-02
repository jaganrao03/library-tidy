# Safety model

This tool renames thousands of files, so its failure mode has to be "nothing
happened", never "some files are gone".

## The two invariants

**1. Two sources can never share one target.**

On POSIX, `Path.rename()` replaces an existing destination and reports success.
A batch renamer that computes each target independently — the obvious
implementation — will silently destroy files whenever two sources normalise to
the same name. This is not hypothetical: in the development of this tool, 20
files (16 videos, 4 photos) were overwritten this way, because a run renamed 941
videos onto 925 unique names.

The fix is in `rename._claim()` and `Plan.check()`: every planned target is added
to a `claimed` set, a collision appends ` (1)`, ` (2)` …, and `apply()` refuses to
start if the plan contains a duplicate target or a target that already exists on
disk.

**2. Every rename is reversible.**

`apply()` writes a JSONL journal before the first move, and a record after each
successful rename, with both paths relative to the root:

```json
{"type":"header","root":"/home/you/library","count":941}
{"type":"rename","old":"Videos/2025-06/clip.mp4","new":"Videos/2025-06/2025-06-01 - Video - Phone.mp4"}
```

`undo` reads the journal, reverses it newest-first (so a file renamed twice lands
back where it started), and never overwrites: if a name is already taken it
appends ` (restored)`.

## What apply() will not do

- Refuse to run on `/`, `$HOME`, or `~/.hermes` — the roots where a mistaken
  glob historically does the most damage.
- Refuse to run if any planned destination already exists.
- Refuse to start if the plan is internally inconsistent.
- Never delete, truncate, or move a file outside the root it was given.

## Deduplication never deletes

`dedupe` moves duplicate copies into `_Duplicates/<original relative path>` and
leaves the first (shortest-path) copy in place. Reclaiming the space is your
decision, made after reading the list.

## Auditing is read-only

`audit`, `plan`, `check` and `gallery` open files for reading only. `gallery`
resolves every requested path and refuses anything outside the root — a request
for `../../.env` returns 403.

## Verification discipline

A renamer's success message proves nothing. After any bulk change, count files
before and after:

```bash
find ~/library -type f | wc -l
```

and reconcile the journal against disk. If the counts differ, stop — do not run
another pass to "fix" it.
