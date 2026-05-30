# Caching

`DriveService` keeps an in-memory cache of every file it has touched, so
that repeated lookups, navigation, and pagination don't re-hit the API.
The cache is per-`DriveService` instance — there is no cross-process
coordination, no TTL, and no invalidation for changes made outside this
library.

## What's cached

Three structures, all owned by the `DriveService`:

| Structure | Type | Populated by |
|---|---|---|
| `_ids` | `dict[id, File]` | Every `get`, `find`, `ls`, `mkfile`, `copy_to`, ... |
| `_paths` | `dict[Path, list[File]]` | Same — keyed by the file's computed path |
| `Folder._file_list` | `FileList` per folder | `Folder.list()` on first access |

Plus per-file caches:

- `File._loaded` — set after a `fields=*` re-fetch for extended metadata
- `File._path`, `File._parent`, `File._shared_drive_id` — lazy properties
- `Shortcut._target`, `Shortcut._broken` — memoized target resolution

## Cache lifecycle

### Population

- The constructor seeds the cache with the `Root`.
- `drive.get(id=X)` on a miss calls `files.get(fileId=X)` and caches the
  result.
- `drive.get(path=...)` walks the cache and falls back to listing the
  parent folder (one round-trip), which populates every sibling at once.
- `Folder.list()` paginates through `files.list` and caches every child.
- Every `mkfile` / `make_file` / `copy_to` adds the new file to both
  parent's `_file_list` and the service-wide indices.

### Invalidation

Mutations through this wrapper keep the cache consistent:

- `rename` — uncaches by old path, recomputes `_path`, re-caches by new
  path. Folder: descendant paths are invalidated and re-cached.
- `move_to` — both source and destination parents' `_file_list` are
  updated; the moved file's `_path`, `_parent`, and `_shared_drive_id` are
  reset. Folder: descendant paths are invalidated and re-cached.
- `delete` — file removed from `_ids` and `_paths`; parent's `_file_list`
  drops the entry; folder descendants are uncached recursively.
- `copy_to` — the new file is cached; the source is untouched.

### What's *not* invalidated

- **External changes** are invisible until you refresh by id or restart.
  If someone renames a file in the Drive UI, the cached path stays stale.
- **`cd`** does not touch the cache or validate the destination.
- **Raw `drive.resource` calls** bypass the wrapper entirely; affected
  files must be re-fetched by id.
- **Shortcut targets** are memoized on the `Shortcut` instance —
  re-creating the target later doesn't refresh the cached `MissingFile`.
- **Folder listings** are cached on the `Folder` instance; the only way
  to force a refresh is to uncache the folder and re-fetch it.

## Duplicate names

Drive permits two children of the same folder to share a name. The cache
copes by mapping each `Path` to a *list* of `File` objects:

```python
drive._paths[path]   # list[File] — usually 1 entry, sometimes more
```

Behavior on an ambiguous path:

| Call | Result |
|---|---|
| `get(path=...)` | raises `ValueError` listing the matching ids |
| `get(id=...)` | works for both — id is unique |
| `find(path=...)` | returns *all* matches |
| `exists(path=...)` | still raises `ValueError` — only `FileNotFoundError` is swallowed |
| `ls(parent_path)` | both entries appear in the `FileList` |

To recover an unambiguous path, drop one of the duplicates from the cache:

```python
duplicates = drive.find("~/twin.txt")
drive.uncache(duplicates[0])   # path now resolves to duplicates[1]
```

(Caveat: `uncache` only removes the local mapping. The server still has
both files. The next `Folder.list()` re-reads both and the ambiguity
returns.)

## When paths get rebuilt

`_invalidate_descendant_paths` runs after a folder is renamed or moved.
It walks the folder's cached children: drops each from `_paths`, clears
`_path` and `_parent`, then re-caches under the new (lazily-recomputed)
path. The descendant `File` instances themselves stay alive in `_ids`, so
any outside reference keeps working — `f.path` simply returns the new path
on next access.

`_uncache_descendants_for_delete` is the destructive counterpart: after a
folder is deleted, every cached descendant is removed from both `_ids` and
`_paths`, and the folder's `_file_list` is cleared. Anything still
referenced from outside becomes orphaned (still usable by id, but not
re-cached).

Both walks only traverse what's already cached — uncached descendants
were never going to appear in `_ids`/`_paths` anyway, and any path
that would have routed through them is recomputed on demand.

## Manual cache control

```python
drive.cache(file)     # public — used internally and by tests; idempotent
drive.uncache(file)   # remove from both `_ids` and `_paths`
```

There is no "refresh from server" primitive — uncaching by id and
re-fetching is the workaround:

```python
file_id = file.id
drive.uncache(file)
fresh = drive.get(id=file_id)   # one fresh round-trip
```

`uncache` does *not* touch parent folders' `_file_list`. If the file is
still listed under its parent, the next `Folder.list()` won't re-fetch
(the list is itself cached). To force a folder to re-read its contents,
construct a new `DriveService` or clear `folder._file_list = None`
directly.

## Performance notes

- A path lookup costs *at most* one `files.list` (the parent folder) plus
  any ancestors that aren't yet cached. On a cold cache, walking from the
  root down to a deeply-nested file is one round-trip per directory.
- An id lookup is one `files.get` on miss, zero on hit.
- `find` with `**` walks the entire subtree, listing every folder it
  reaches. Use with care on large trees.
- Extended properties (`size`, `created_time`, ...) trigger a `fields=*`
  re-fetch on first access per file. Bulk-listing a folder fetches only
  the small `FIELDS` set; the larger fetch is per-file and lazy.
