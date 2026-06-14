# Google Drive

A typed Python wrapper around the Google Drive API. Designed for scripts and
backend code: navigate Drive like a shell, look files up by path or id,
move / copy / rename / delete, create new documents.

## Quick start

```python
from gservices import GoogleServices

google = GoogleServices(credentials)
drive = google.Drive

# Browse
print(drive.ls("~"))             # list the user's My Drive
drive.cd("~/Reports/2026")
for f in drive.ls():
    print(f.name, f.mime_type)

# Resolve
report = drive.get("~/Reports/2026/Q1.gsheet")
print(report.id, report.modified_time)

# Mutate
drive.mkdir("~/Reports/2026/archive")
drive.mv("~/Reports/2026/Q1.gsheet", "~/Reports/2026/archive/")
drive.rm("~/Reports/2026/draft.gdoc")
```

Each call hits the Drive API directly — there is no batching layer here. Where
results are cacheable (folder listings, id lookups, file metadata) the
`DriveService` keeps them in memory for the lifetime of the instance; see
[caching.md](caching.md).

## Object model

```
GoogleServices.Drive             →  DriveService           entry point
    .ls / .cd / .pwd / .mkdir / .mkfile / .rm / .cp / .mv
    .get(path | id) / .exists / .find
    .user_drive                  →  UserDrive              "My Drive"
    .resource                    →  googleapiclient resource (escape hatch)

    file = .get(...)             →  File                   any Drive file
        .id, .name, .mime_type, .path, .parent
        .size, .created_time, .modified_time, .version
        .starred, .trashed, .explicitly_trashed
        .is_dir, .is_shortcut, .is_spreadsheet, .is_document, .is_shared_drive
        .rename(new_name)
        .move_to(dest_dir_path)
        .copy_to(dest_path)
        .delete(trash=True)
        .download()              →  bytes  (blob files only)
        .update_content(data)

      ├── Folder                 a directory
      │     .list()              →  FileList
      │     .make_file(name, kind)
      │     .upload(name, data)  →  File   (blob files only)
      │   ├── Root               the synthetic "/" above My Drive + shared drives
      │   ├── UserDrive          "My Drive"
      │   └── SharedDrive        one shared drive
      │
      ├── Shortcut               .target → File (or MissingFile if broken)
      │                          .is_broken
      ├── SpreadsheetFile        green in `ls`
      └── DocumentFile           cyan in `ls`
```

The Drive API uses opaque file ids. This wrapper layers a *path namespace* over
those ids: every cached file has a path like `/My Drive/Reports/Q1.gsheet`,
and paths are how you address files in shell-like calls. Paths are constructed
from cached parent/child relationships, so they reflect the wrapper's view of
the tree — not a server-side concept. See [paths.md](paths.md).

## Documentation map

- [**paths.md**](paths.md) — path syntax, `~`, relative paths, trailing-slash
  semantics, what counts as a path
- [**shell.md**](shell.md) — `ls`, `cd`, `pwd`, `mkdir`, `mkfile`, `rm`,
  `cp`, `mv`, `get`, `exists`, `find`
- [**files.md**](files.md) — the `File` class, subtypes (Folder / Root /
  UserDrive / SharedDrive / Shortcut / SpreadsheetFile / DocumentFile),
  per-file properties and operations
- [**caching.md**](caching.md) — what the `DriveService` caches, when entries
  become stale, duplicate names, escape hatches

## Feature coverage

### Supported

- **Navigation** — `ls`, `cd`, `pwd` over a unified namespace that spans
  My Drive, shared drives, and (read-only) Workspace files
- **Path syntax** — absolute paths, `~` for My Drive, `.` / `..`, relative
  paths against the current working directory, trailing-slash semantics
  for `cp` / `mv`
- **Lookup** — `get(path=)` or `get(id=)`, `exists`, `find` with
  regex / `*` / `**` segments and optional MIME filtering
- **File metadata** — id, name, MIME type, parents, size, created /
  modified time, version, starred, trashed
- **File lifecycle** — `rename`, `move_to`, `copy_to`, `delete`
  (trash or permanent); folders copy recursively
- **Content I/O** — `Folder.upload` (bytes / text / local file),
  `File.download`, `File.update_content` for ordinary blob files; works in
  shared drives, integrates with the path/cache layer
- **Folder operations** — `list` (with automatic pagination), `make_file`
  for creating Docs / Sheets / Slides / Drawings / sub-folders
- **Shortcuts** — target resolution, broken-shortcut detection
- **Shared drives** — listed alongside My Drive under the synthetic root;
  `supportsAllDrives` is set automatically
- **Caching** — id and path caches, automatic invalidation across moves /
  renames / deletes, descendant-path fix-up after a folder rename

### Not supported (use `DriveService.resource` for the raw API)

- **Permissions** — sharing, role changes, link sharing
- **Workspace-doc export** — exporting Sheets / Docs / Slides as
  XLSX / PDF / Markdown (raw blob upload / download *is* supported; see above)
- **Revisions** — listing or restoring revisions, exporting at a revision
- **Comments / replies**
- **Drive activity** / changes feed / push notifications
- **Trash management** — listing the trash, restoring from trash, emptying
- **Drive labels and label values**
- **App-specific properties** — `appProperties`, custom indexable properties
- **Quotas and storage info**
- **Team membership / capabilities** on shared drives
- **Search beyond name-based `find`** — full-text search, `q=` query strings,
  search across all drives in one call

### Architectural limits

- **No batching.** Each shell-like call hits the API directly. There is no
  equivalent of Sheets' `Spreadsheet.save()`.
- **Path namespace is wrapper-local.** Drive itself has no path concept —
  paths are computed from cached parent ids. A file whose parent is unknown
  to this `DriveService` instance can still be reached by id, but its
  `path` will route through whatever parent the cache has loaded.
- **Duplicate names are allowed.** Drive permits two children of the same
  folder with the same name. `get(path=)` on an ambiguous path raises;
  callers must disambiguate with `get(id=)`. See [caching.md](caching.md).
