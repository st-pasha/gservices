# Paths

Drive itself has no path concept — every file is referenced by an opaque id.
This wrapper layers a path namespace on top by walking each file's `parents`
field back to a synthetic root. Paths are the primary way you address files
in the shell-like API ([shell.md](shell.md)); ids are the escape hatch when
paths are ambiguous or unknown.

## The synthetic tree

```
/                       Root             (synthetic — has no Drive id)
├── My Drive            UserDrive        (the current user's personal drive)
│   ├── Reports
│   │   └── Q1.gsheet
│   └── notes.gdoc
├── Engineering         SharedDrive      (one shared drive)
│   └── ...
└── Marketing           SharedDrive      (another shared drive)
    └── ...
```

The `Root` is fabricated by the wrapper — there is no Drive id `"/"`. Its
children are *My Drive* (always present, named whatever the user has
configured — typically `"My Drive"`) followed by every shared drive the user
can see. Listing the root issues `drives.list` with pagination; see
[caching.md](caching.md).

## Path strings

Path strings look like POSIX paths:

```python
"/My Drive/Reports/Q1.gsheet"   # absolute
"Q1.gsheet"                      # relative to pwd
"../archive/Q1.gsheet"           # relative with parent navigation
"~/Reports"                      # ~ → user_drive.path (e.g. "/My Drive")
"."                              # current working directory
""                               # same as "."
```

`~` expands to the user's My Drive path. `~/foo` becomes `/My Drive/foo`,
not `~/foo` — the `~` is dropped, not preserved as a literal segment.

`.` and `..` are resolved during parsing. `..` that would climb past the
Root raises `ValueError`.

A *relative* path (one that doesn't start with `/` or `~`) is anchored at
the current working directory. The cwd starts at the synthetic root; change
it with [`drive.cd(...)`](shell.md).

## Trailing slashes

A trailing `/` is preserved on the parsed `Path` as `has_tail = True`. It
does **not** affect equality or hashing — `/A/B` and `/A/B/` are the same
path identity — but the `cp` and `mv` operations read it to decide between
"copy the directory itself" and "copy the directory's contents":

| Source | Dest | Meaning |
|---|---|---|
| `cp("A", "B")` | rename: A is copied as B |
| `cp("A", "B/")` | A is copied *into* B (becomes `B/A`) |
| `cp("A/", "B/")` | every child of A is copied into B |
| `cp("A/", "B")` | not allowed — raises `ValueError` |

`mv` follows the same convention. See [shell.md](shell.md#cp-and-mv).

## `Path` as a value

`Path` is a small immutable value class. You rarely construct one directly —
the shell-like API parses strings for you — but it appears whenever a file's
location matters:

```python
file.path                 # Path("/My Drive/Reports/Q1.gsheet")
file.path.parent          # Path("/My Drive/Reports")
file.path.basename        # "Q1.gsheet"
file.path.is_root         # False
file.path / "child.gdoc"  # Path("/My Drive/Reports/Q1.gsheet/child.gdoc")
str(file.path)            # "/My Drive/Reports/Q1.gsheet"
```

`Path.__truediv__` builds child paths; `Path / ".."` is the same as
`Path.parent`. Paths are hashable and used as cache keys (see
[caching.md](caching.md)).

## Caveats and edge cases

- **Path is wrapper-local.** The `path` property is computed from each
  file's cached `parent`. If a folder along the chain is unknown to this
  `DriveService` instance, `parent` will fetch it (one round-trip per
  uncached ancestor). The path you see reflects whatever the cache holds —
  not a server-side canonical location.
- **Same path, different files.** Drive allows two children of the same
  folder to share a name. `get(path=)` on such a path raises a `ValueError`
  with the matching ids — use `get(id=)` to disambiguate. See
  [caching.md](caching.md#duplicate-names).
- **Path after rename.** Renaming or moving a folder invalidates the cached
  `path` of the folder *and* of every cached descendant. Outside
  references to the descendant `File` instances stay valid; their `path`
  property recomputes on next access.
- **Missing-file paths.** A broken `Shortcut` returns a `MissingFile` for
  its `target`. The `MissingFile`'s path is `Path(("?",))`, rendered as
  `"?"` — a sentinel, not a real location.
- **Special characters in names.** Path parsing splits on `/`. A Drive file
  whose name contains `/` cannot be addressed by path string; reach it by
  id instead.
