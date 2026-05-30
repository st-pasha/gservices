# Files

Every Drive object — documents, folders, shortcuts, drives — is a `File` or
a subclass. The subclass is chosen from the MIME type at fetch time, so
`drive.get(...)` returns the most specific type the wrapper knows about.

## The type hierarchy

```
File                        any Drive file
├── Folder                  application/vnd.google-apps.folder
│   ├── Root                synthetic top-level container
│   ├── UserDrive           "My Drive"
│   └── SharedDrive         one shared drive
├── Shortcut                application/vnd.google-apps.shortcut
├── SpreadsheetFile         application/vnd.google-apps.spreadsheet
└── DocumentFile            application/vnd.google-apps.document

MissingFile                 sentinel for a broken shortcut's target
```

`Slides` (`presentation`) and `Drawing` files are returned as the base
`File` — they can be created with `mkfile("...", "slides" | "drawing")` but
have no dedicated class.

`File.resolve_from_mime(data, drive)` is the factory: given a raw Drive
response, it picks the right subclass and instantiates it. The shell-like
API uses this internally.

## Identifying a file

```python
file.id              # "1abc...XYZ"
file.name            # "Q1.gsheet"
file.mime_type       # "application/vnd.google-apps.spreadsheet"
file.path            # Path("/My Drive/Reports/Q1.gsheet")
file.parent          # Folder
file.shared_drive_id # "" if in My Drive, else the SharedDrive's id
```

`is_*` predicates avoid the need for `isinstance`:

```python
file.is_dir          # Folder (including Root / UserDrive / SharedDrive)
file.is_shared_drive # SharedDrive specifically
file.is_shortcut     # Shortcut
file.is_spreadsheet  # SpreadsheetFile
file.is_document     # DocumentFile
```

`__str__` returns the path; `__repr__` is `"ClassName(path)"`.

## Extended metadata

The first time you access any of these, the wrapper does a `fields=*`
re-fetch (one round-trip) to populate them; subsequent reads are cached.

```python
file.size              # int — bytes; 0 for folders, shortcuts, Workspace docs
file.created_time      # datetime.datetime
file.modified_time     # datetime.datetime
file.version           # int — server-side monotonic counter
file.starred           # bool
file.trashed           # bool — including via a trashed parent
file.explicitly_trashed  # bool — only if directly trashed
```

The `_loaded` flag tracks the fetch so folders (which never have a `size`)
don't re-fetch on every access.

## Per-file operations

These methods live on `File` and apply to any file (including folders,
unless noted).

### `rename(new_name)`

```python
file.rename("Q1-final.gsheet")
```

Calls `files.update(name=...)`. The file's `name` and `path` update; the
cache entry is moved to the new path; if the file is a `Folder`, every
cached descendant's path is invalidated and recomputed.

### `move_to(dest_dir_path)`

```python
file.move_to(Path("/My Drive/archive"))
```

`dest_dir_path` must point to a folder. Calls `files.update(addParents=,
removeParents=)`. Moving a file to its current parent is a no-op (no HTTP
call). The previous and new parents' cached file lists are updated.

For folders, descendant path entries are invalidated; the descendants
themselves stay alive in the id cache.

### `copy_to(dest_path)`

```python
file.copy_to(Path("/My Drive/archive/Q1-copy.gsheet"))
```

`dest_path` is the *full destination path*, including the new name. The
parent of `dest_path` must be a folder.

- **Files** are copied with `files.copy`, which keeps the MIME type.
- **Folders** are copied recursively by the wrapper: a new destination
  folder is created, then each child is copied into it. Drive's
  `files.copy` does not accept folders.

### `delete(trash=True)`

```python
file.delete()              # trash
file.delete(trash=False)   # permanent
```

Trashing calls `files.update(trashed=True)`. Permanent deletion calls
`files.delete`. Either way the file (and, for folders, every cached
descendant) is removed from the cache, and the parent folder's `list()`
drops the entry.

Deleting the Root, a `UserDrive`, or a `SharedDrive` raises `RuntimeError`.

## Subtype specifics

### `Folder`

```python
folder = drive.get("~/Reports")
folder.list()                              # FileList — paginated, cached
folder.make_file("Q2.gsheet", "spreadsheet")
```

`Folder.list()` returns a `FileList` (a `list[File]` with a tree-style
`__repr__`). It paginates internally via `nextPageToken`, sets
`includeItemsFromAllDrives` and `supportsAllDrives` when in a shared drive,
and filters out trashed children. The result is cached; mutations through
this wrapper keep it in sync.

`Folder.make_file(name, kind)` creates a new file under this folder and
returns the typed instance. See [shell.md](shell.md#mkdir-and-mkfile) for
the available kinds.

### `Root`

The synthetic top-level container — it has no Drive id, its `parent`
raises `ValueError`, and its `_fetch_files` enumerates the user's drives
(My Drive + every shared drive). You usually interact with it implicitly
(`drive.ls("/")`).

### `UserDrive`

The personal "My Drive". The wrapper looks it up via the special
`files.get(fileId="root")` call during construction, then caches it as a
child of Root. Its name is whatever the user has configured (typically
`"My Drive"`), so `~` resolves to `/My Drive` for most users but not all.

`UserDrive.delete()` raises `RuntimeError`.

### `SharedDrive`

One shared drive. Listed under Root alongside My Drive; constructed from
the response of `drives.list`. Files inside a shared drive carry the
drive's id on their `shared_drive_id`; the wrapper uses this to set
`supportsAllDrives` and `driveId` on requests where needed.

`SharedDrive.delete()` raises `RuntimeError` — shared drives can only be
deleted through the Drive UI.

### `Shortcut`

```python
sc = drive.get("~/ShortcutToReport")
sc.target            # File — the resolved target (or MissingFile if broken)
sc.is_broken         # bool
```

A shortcut's `target` is resolved lazily on first access. If the target id
no longer exists, `target` is set to a `MissingFile` (a `File` whose path
is `Path(("?",))` — rendered as `"?"`) and `is_broken` flips to `True`.
The result is memoized on the `Shortcut` instance — if the target is later
re-created, the cached `MissingFile` stays stale; re-fetch by id to refresh.

In a `FileList` repr a shortcut renders with a `↪` icon (or `✘` if broken).

### `SpreadsheetFile` and `DocumentFile`

Marker subclasses used to type-check `is_spreadsheet` / `is_document` and
to colour the `FileList` repr (green for spreadsheets, cyan for docs).
Neither adds methods of its own — to actually edit a spreadsheet, hand the
id to `google.Sheets.open(...)` (see the
[Sheets docs](../sheets/README.md)).

The `SpreadsheetFile` import is kept function-local in a few places
(`File.resolve_from_mime`, `Folder.make_file`, `File.is_spreadsheet`) to
break a `spreadsheet_file → file → folder → spreadsheet_file` cycle. See
the project's `CLAUDE.md` for the convention.

## `FileList`

```python
files = drive.ls("~/Reports")
files[0].name
print(files)        # tree-style repr with ANSI colours by default
```

`FileList` is a thin `list[File]` subclass that carries the parent path
and a custom `__repr__`. Folders are listed before non-folders; each entry
is rendered through `File.file_list_repr`, which subclasses override for
colour:

| Subclass | Colour |
|---|---|
| `Folder` | bold default |
| `UserDrive` | bold green + trailing `/` |
| `SharedDrive` | bold cyan + trailing `/` |
| `SpreadsheetFile` | green |
| `DocumentFile` | cyan |
| `Shortcut` | rendered as the target name + `↪` (dim + `✘` if broken) |

Set `FileList.USE_COLORS = False` (class attribute) to suppress ANSI
escapes globally.

## Escape hatch: the raw resource

```python
drive.resource     # googleapiclient.discovery.Resource for "drive v3"
```

Whenever the wrapper doesn't cover what you need (permissions, comments,
exports, revisions, ...), drop down to the raw resource. The wrapper's
cache will not see those changes; refresh affected entries by id if you
need them after.
