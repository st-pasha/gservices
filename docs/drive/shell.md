# Shell-like API

`DriveService` exposes a small set of methods that mirror a POSIX shell:
`ls`, `cd`, `pwd`, `mkdir`, `mkfile`, `rm`, `cp`, `mv`, plus the lookup
helpers `get`, `exists`, and `find`. Path arguments are parsed per
[paths.md](paths.md); everything else is direct Drive API calls.

## `ls` — list a directory

```python
drive.ls()                       # current working directory
drive.ls("~")                    # My Drive
drive.ls("~/Reports/2026")
```

Returns a `FileList` (a `list[File]` with a tree-style `__repr__`). Folders
are sorted first, then non-folders, both alphabetically. Listing a
non-directory raises `NotADirectoryError`.

The listing is cached on the folder. A second `ls()` of the same folder
reuses the cached list (no HTTP). Mutations performed via this wrapper
(`mv`, `cp`, `rm`, `mkfile`, etc.) keep the cached list in sync; mutations
made outside this process do not. See [caching.md](caching.md).

## `cd` and `pwd` — current directory

```python
drive.cd("~/Reports")
drive.pwd()                      # Path("/My Drive/Reports")
drive.cd("..")
```

`cd` resolves its argument against the current directory and stores the
result. It does **not** verify the target exists — the path is taken at face
value. Subsequent operations that resolve a relative path will fail
naturally if the cwd is bogus.

`pwd()` returns the parsed `Path`. The cwd starts at the synthetic root
`/`.

## `mkdir` and `mkfile` — create

```python
drive.mkdir("~/Reports/2026/archive")
drive.mkfile("~/Reports/2026/Q1.gsheet", "spreadsheet")
drive.mkfile("~/notes.gdoc", "document")
drive.mkfile("~/slides.gslides", "slides")
drive.mkfile("~/sketch.gdraw", "drawing")
```

`mkdir(path)` is `mkfile(path, "folder")`. The parent directory must already
exist and must be a folder; creating directly under the synthetic root is
not allowed (the children of root are *drives*, not files).

The `kind` argument picks the MIME type:

| `kind` | MIME |
|---|---|
| `"folder"` | `application/vnd.google-apps.folder` |
| `"document"` | `application/vnd.google-apps.document` |
| `"spreadsheet"` | `application/vnd.google-apps.spreadsheet` |
| `"slides"` | `application/vnd.google-apps.presentation` |
| `"drawing"` | `application/vnd.google-apps.drawing` |

The new file is cached and the parent folder's list is updated. The
returned `File` (from `Folder.make_file`) is the appropriate subclass
(`Folder`, `SpreadsheetFile`, `DocumentFile`, or plain `File`); `mkdir` and
`mkfile` discard it — call `Folder.make_file` directly if you need the
reference.

## `rm` — delete

```python
drive.rm("~/Reports/2026/draft.gdoc")
```

Trashes the file (calls `files.update(trashed=True)`). To delete
permanently, call the file's own method:

```python
drive.get("~/junk.gdoc").delete(trash=False)
```

Deleting a folder trashes its contents recursively — Drive does this
server-side. The local cache is cleaned up: the file and all its cached
descendants are removed from both id and path indices, and the parent
folder's `list()` drops the entry.

Deleting the Root, a `UserDrive`, or a `SharedDrive` raises
`RuntimeError` — these are not safe to delete via this library.

## `cp` and `mv`

```python
drive.cp("~/Reports/Q1.gsheet", "~/archive/Q1-copy.gsheet")
drive.cp("~/Reports/", "~/backup/")          # copy contents into backup/
drive.mv("~/Q1.gsheet", "~/archive/")        # move into archive/
drive.mv("~/Q1.gsheet", "~/archive/Q1.gsheet")  # move + rename in one go
```

The trailing-slash convention determines whether the *file* or its
*contents* are the subject:

|  | dest `B` | dest `B/` |
|---|---|---|
| **src `A`** | rename/copy A → B | place A inside B |
| **src `A/`** | not allowed | copy/move each child of A into B |

Mismatches raise `ValueError` (source has tail, dest doesn't) or
`NotADirectoryError` (a tailed path that doesn't resolve to a folder).

### `cp` semantics

- Files are copied via `files.copy`.
- Folders are copied **recursively** by the wrapper — Drive's `files.copy`
  doesn't accept folders, so this wrapper creates the destination folder
  and copies each child into it. Large trees can take many round-trips.
- The copy keeps the source's MIME type. Each new file is cached.

### `mv` semantics

- Moving a file changes its parents via `files.update(addParents=, removeParents=)`.
- Moving a file to its current parent is a no-op (no HTTP call).
- For `mv("A", "B")` (no tails), the file is first moved to `B`'s parent
  folder, then renamed to `B`'s basename — two round-trips.
- Moving a folder invalidates the cached `path` of every cached descendant,
  but the descendant `File` instances themselves stay alive and remain
  reachable by id.

## `get` — resolve a path or id

```python
drive.get("~/Reports/Q1.gsheet")     # by path
drive.get(id="1abc...XYZ")           # by id
```

Path lookup walks the cache. If the path isn't cached, the parent folder is
listed (one round-trip) and the lookup retried. A path that resolves to
zero files raises `FileNotFoundError`; a path that resolves to *more than
one* file (Drive allows duplicate names) raises `ValueError` — use
`get(id=)` to pick one.

Id lookup uses an in-memory map; on a cache miss it fetches the file
directly (`files.get`) and caches it. A 404 is translated to
`FileNotFoundError`; other HTTP errors propagate.

Exactly one of `path` and `id` must be provided.

## `exists`

```python
if drive.exists("~/Reports/Q1.gsheet"):
    ...
```

A `FileNotFoundError`-suppressing wrapper around `get`. **An ambiguous path
still raises** `ValueError` — `exists` only swallows "not found", not "too
many."

## `find` — pattern-matching multi-result lookup

```python
drive.find("~/Reports/*")                 # every child
drive.find("~/Reports/**")                # every descendant
drive.find("~/Reports/Q[1-4]\\.gsheet")   # regex segment
drive.find("~/Reports/**", mime_type=SpreadsheetFile.MIME)
```

`find` walks the path one segment at a time:

1. **Exact match wins.** A child whose name equals the segment is taken,
   even if the segment looks like a regex. So `"Report (Q1).csv"` is found
   literally — the parens aren't interpreted.
2. **Otherwise, regex.** The segment is compiled with `re.compile` and
   matched against each child's name via `re.fullmatch` (anchored at both
   ends). Bad regex → that branch yields no results.
3. **Wildcards.** `*` matches any single child; `**` matches any
   descendant (recursive).

Listing a folder is required to enumerate its children, so `find` may
trigger several round-trips on cache-cold trees.

`mime_type` is an optional post-filter — pass `Folder.MIME`,
`SpreadsheetFile.MIME`, etc. to keep only matching types.

## Errors at a glance

| Method | Raises |
|---|---|
| `ls(path)` | `NotADirectoryError` if path isn't a folder; `FileNotFoundError` if missing |
| `cd(path)` | Nothing — the path isn't validated |
| `mkdir` / `mkfile` | `ValueError` for unknown kind or root-as-parent; `NotADirectoryError` for non-folder parent |
| `rm(path)` | Whatever `get` raises; `RuntimeError` for Root / UserDrive / SharedDrive |
| `cp` / `mv` | `NotADirectoryError` for tail-on-non-dir; `ValueError` for source-tail-to-non-tail |
| `get(path)` | `FileNotFoundError`, `ValueError` (ambiguous), `NotADirectoryError` (non-folder ancestor) |
| `get(id)` | `FileNotFoundError` on 404; HTTP errors otherwise |
| `get()` (no args) | `TypeError` |
| `find` | Returns `[]` on no match (no exception); regex errors yield empty per-segment |
