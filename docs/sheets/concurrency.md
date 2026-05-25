# Concurrency

Google Sheets has no transactional update primitive. The `batchUpdate` API
fires by absolute coordinates and accepts whatever lands first. Without
extra care, a "write `done` into F8" request can overwrite a different cell
if another user inserts a row above F8 between your load and save.

This document explains the two opt-in safety nets and where each helps.

## The fundamental problem

```python
ss = google.Sheets.open(file_id)
cell = ss.sheets[0].cell(7, 5)          # F8
cell.value = "done"                     # queued
# ... meanwhile, another user inserts a row at index 3 ...
ss.save()                                # writes "done" into row 7 col 5
                                         # — but that's now the cell that was F7
```

The `updateCells` request goes through with absolute `(rowIndex=7,
columnIndex=5)`. The server doesn't know your local view is one row stale.
"done" lands in the wrong cell, silently.

Both safety mechanisms below help against this, in different ways.

## Option 1: `save(check_version=True)` — detect

A best-effort post-hoc check. Compares the file's Drive version against a
baseline captured at open time; mismatches raise instead of writing.

```python
ss = google.Sheets.open(file_id, track_version=True)
# ... edits ...
try:
    ss.save(check_version=True)
except SpreadsheetVersionMismatchError as e:
    # Someone else edited the file (e.baseline → e.current).
    # ss._pending_updates still holds our queued changes.
    ...
```

### What it catches

Any third-party edit to the spreadsheet between open and save, on any sheet.
Edits include cell changes, structural changes (row/column inserts and
deletes), sheet additions, formatting changes — anything that bumps the
file's `version` field.

### What it doesn't catch

A remote edit that lands between the version check and the batchUpdate (a
~100ms window). Best-effort, not transactional.

### How it interacts with successive saves

After a successful save (checked or unchecked), the baseline auto-refreshes
to the post-save version. So you can use the same `Spreadsheet` for many
sequential checked saves without spurious mismatches from your own writes
bumping the version.

```python
ss = google.Sheets.open(file_id, track_version=True)
ss.sheets[0].cell(0, 0).value = "a"
ss.save(check_version=True)        # baseline → post-save version

ss.sheets[0].cell(1, 0).value = "b"
ss.save(check_version=True)        # checks against the new baseline; passes
```

### What it costs

- One extra Drive API call on `open(track_version=True)` (~100ms).
- One extra Drive API call per `save()` if tracking is on (refreshing
  baseline). With `check_version=True`, two extra calls (pre-check +
  post-save refresh).

## Option 2: `exclusive_edit()` — prevent

A context manager that installs a `protectedRange` covering target sheets,
making the authenticated user the only allowed editor. Other users see the
affected sheets as read-only until the block exits.

```python
with ss.exclusive_edit():
    # Other editors are blocked from modifying any sheet for the duration.
    ss.reload()                  # see "Why reload?" below
    fresh_cell = ss.sheets[0].cell(7, 5)
    fresh_cell.value = "done"
    # pending updates flushed on normal exit; protection released afterwards
```

### Why `reload()` inside the block

Protection only stops *future* edits. Anything you loaded *before* the
lock might be stale relative to edits that landed in the small window
between your initial fetch and the lock taking effect — or in the much
larger window between your initial fetch and entering the `with` block
(if you held the `Spreadsheet` for a while). `ss.reload()` re-fetches
the cell data for every loaded sheet from the server, so what you read
next reflects post-lock authoritative state.

```python
ss = google.Sheets.open(id, load=True)
# minutes pass; another user might have edited
with ss.exclusive_edit():
    ss.reload()                  # discard stale local view
    # ... now reads are authoritative
```

`reload()` doesn't eagerly load sheets that weren't already loaded — it
only refreshes ones with data. Any `Cell`, `Row`, or `Column` references
you held before `reload()` are stale after; re-fetch via `sheet.cell(...)`
inside the block. See [spreadsheet.md](spreadsheet.md#reload) for the
full semantics.

### What other users see

While the lock is held, users *other than the lock holder* see:

- A lock icon on each protected sheet's tab.
- A blocking dialog if they try to edit a protected cell: *"You can't edit
  this cell. [Sheet] is protected. Contact [lock holder email] for access."*
- An entry in the Data → Protected sheets and ranges panel with our
  description string (raw JSON; this is ugly — see [limitations](#limitations)).

The lock holder (you) can edit normally throughout.

### Recommended workflow

The right order is **protect → fetch → modify → save → unprotect**, not
fetch-then-protect. Protection only stops *future* edits, so any race-eligible
read must happen *after* the protection is in place. The context manager
handles 1 (protect) and 5 (unprotect); you typically re-fetch or re-read
inside the block to get authoritative post-lock state.

### Scoping

```python
# Default: locks every sheet in the spreadsheet.
with ss.exclusive_edit():
    ...

# Scoped: locks only specific sheets. Other sheets remain editable.
with ss.exclusive_edit(sheets=["Summary", "Q4"]):
    ...
```

Scoping reduces UX impact but means edits to unscoped sheets still bump
`File.version`. If you care about file-level consistency, combine with
`check_version=True`.

### Stale-lock recovery

Each acquired lock encodes `{holder, lock_id, expires_at}` as JSON in the
protection's `description`. If a process crashes mid-block, the lock stays
in place. The next `exclusive_edit()` to run scans existing protections,
finds ones marked with our prefix (`gservices-lock:`), and if their
`expires_at` is past, forcibly deletes them before installing its own.

So a stuck lock self-heals after `ttl_seconds` (default 300s = 5 min)
**when the next `exclusive_edit()` runs**. If nobody else uses
`exclusive_edit()` for a while, the lock sits — but other users can still
manually remove it via the Data menu, or the file owner can.

```python
with ss.exclusive_edit(ttl_seconds=60):   # short TTL, fast recovery
    ...

with ss.exclusive_edit(ttl_seconds=3600): # long TTL, big work
    ...
```

Set `ttl_seconds` longer than the longest your block might take. The TTL
doesn't trigger automatic release — it's only the bar for the recovery
sweep.

### Limitations

- **Best-effort, not transactional.** Two simultaneous `exclusive_edit()`
  calls can each install a protection (each thinks it holds the lock).
  Edits inside each protection are blocked for non-listed users — server-
  side enforcement still holds — but the bookkeeping is advisory.
- **Visible to other users.** The lock icon and "you can't edit" dialog
  are intrusive in collaborative spreadsheets. Fine for backend workflows
  where humans don't normally touch the file.
- **File owner can bypass.** The owner can always edit and can manually
  remove the protection. Same for Google Workspace admins.
- **Description field shows raw JSON.** Anyone opening the Data → Protected
  sheets panel sees `gservices-lock:{"holder":"...","lock_id":"...","expires_at":"..."}`.
  Not user-friendly. Could be improved with a human-readable description +
  side-channel metadata.
- **Latency.** Two extra batchUpdate round-trips per block (~200-500ms each).

## Who can remove a protection?

| Role | Can edit cells in protected range | Can remove the protection |
|---|---|---|
| File owner | yes (always) | yes (always) |
| Lock holder (in `editors` list) | yes | yes |
| Other editor of file | no | no |
| Viewer / commenter | no | no |

So if the lock holder crashes and no one runs `exclusive_edit()` again, the
file owner is the only escape hatch besides the recovery sweep.

## When to use which

| Situation | Recommendation |
|---|---|
| Single-writer backend, no other editors | Neither needed. |
| Backend worker + occasional human edits | `check_version=True`. Cheap, detects after the fact. |
| Backend with strict consistency, blocking human edits is acceptable | `exclusive_edit()`. |
| Both | Open with `track_version=True`, use `exclusive_edit()` for critical sections, `check_version=True` elsewhere. |
| Truly concurrent multi-writer system | You're outside what Sheets can give you. Consider a real database. |

## See also

- [spreadsheet.md](spreadsheet.md) — `SheetsService.open(track_version=True)`,
  `Spreadsheet.save(check_version=True)`
