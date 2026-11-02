"""library-tidy — turn a pile of accumulated files into a predictable library.

The convention is `DATE - PARTY - TYPE - TOPIC.ext`. Everything else — date
recovery from filenames, EXIF or media metadata, collision-safe planning,
journalled renames, undo, duplicate detection and a read-only phone gallery —
is machinery in service of that one rule.
"""
__version__ = "0.1.0"
