# Developer metadata

Google Sheets has a concept of "developer metadata" — opaque key/value pairs
your code can attach to a spreadsheet, sheet, row, or column. The library
exposes all four flavors.

## Why use it

Two use cases stand out:

1. **Stable references across structural changes.** A row's developer
   metadata follows the row if it's moved, sorted, or has rows inserted
   above. So `metadata.add("invoice_id", "INV-42")` lets you find that row
   later even after the user reorders things.

2. **Out-of-band annotations.** Information you want to associate with the
   spreadsheet that doesn't belong in a cell — pipeline run ID, source
   file path, schema version, etc.

For the first use case, also consider `Sheet.cell()` with cached IDs — but
metadata is the only mechanism that survives row inserts/sorts.

## Four flavors

```python
ss.metadata              # SpreadsheetDeveloperMetadata
ss.sheets[i].metadata    # SheetDeveloperMetadata
sheet.rows[i].metadata   # RowDeveloperMetadata
sheet.columns[i].metadata # ColumnDeveloperMetadata
```

The API surface is identical across all four — only the attachment point
(the location) differs.

## Adding

```python
ss.metadata.add(key="schema_version", value="3", public=False)
sheet.rows[5].metadata.add(key="origin", value="external")
```

`add()` queues a `createDeveloperMetadata` request. A response callback then
patches the server-assigned `metadataId` into the local entry on `save()`.

The `public` flag controls visibility:
- `public=False` (default): `PROJECT` visibility — only the same OAuth
  client (project) that wrote it can read it. Other clients see the
  metadata as if it doesn't exist.
- `public=True`: `DOCUMENT` visibility — readable by any client with read
  access to the file.

Pick `PROJECT` unless you want third parties to see your metadata. The
`DOCUMENT` flavor is rare and intended for cases where the metadata is
part of the document's contract with the world.

## Reading

```python
len(ss.metadata)                 # how many entries
for item in ss.metadata:         # iterate
    print(item.id, item.key, item.value)
ss.metadata[0]                   # MetadataItem(id=42, key="...", value="...")
```

`MetadataItem` is a dataclass with `id: int`, `key: str`, `value: str`.

## Deleting

```python
del ss.metadata[0]               # delete by index
```

Queues a `deleteDeveloperMetadata` request and shrinks the local list
immediately, so the next `len()` / iteration reflects the delete.

There's no `delete_by_key()` shortcut — find the index first:

```python
for i, item in enumerate(ss.metadata):
    if item.key == "old_version":
        del ss.metadata[i]
        break
```

(Deleting while iterating is safe with the index pattern — but be careful
in a loop, since deleting shifts subsequent indices. The single-shot
"find-and-delete" above is the safest pattern.)

## Persistence semantics

- **Spreadsheet metadata**: persists with the spreadsheet. Survives all
  edits including renaming the spreadsheet.
- **Sheet metadata**: persists with the sheet. Survives renaming and moving
  the sheet between positions.
- **Row metadata**: anchored to a row. Persists across:
  - inserts above (the row's `index` shifts, metadata follows)
  - inserts below (no change)
  - sorts (metadata moves with the row)
  - the row being moved manually
  - rows being deleted above
  Does NOT persist if the row itself is deleted.
- **Column metadata**: same semantics on the column axis.

This persistence is the killer feature — it's how you safely attach an ID to
a row that users will reorder.

## Locations under the hood

The library generates the right `metadataLocation` for each flavor:

- `SpreadsheetDeveloperMetadata` → `{"spreadsheet": True}`
- `SheetDeveloperMetadata` → `{"sheetId": <sheet.id>}`
- `RowDeveloperMetadata` → `{"dimensionRange": {"sheetId": ..., "dimension": "ROWS", "startIndex": N, "endIndex": N+1}}`
- `ColumnDeveloperMetadata` → `{"dimensionRange": {"sheetId": ..., "dimension": "COLUMNS", "startIndex": C, "endIndex": C+1}}`

You don't normally need to think about this. It matters if you're querying
metadata via raw API calls (`spreadsheets.developerMetadata.search`) — the
location structure is what you'd filter on.

## Known gaps

- **No `update()`**: to change a metadata value, delete and re-add. The
  underlying API does support direct update, but the wrapper doesn't expose
  it yet.
- **No range metadata**: the API supports `metadataLocation: A1Range` for
  metadata attached to specific cell ranges. Not currently exposed.
- **Adding two metadata with the same key under one location**: the
  server's response callback matches incoming `metadataId` assignments by
  `metadataKey`. If you add two entries with the same key in one batch,
  the callback may patch the IDs onto the wrong rows. Don't rely on
  duplicate-key adds in a single `save()`.

## See also

- [spreadsheet.md](spreadsheet.md) — `ss.metadata`
- [sheets.md](sheets.md) — `sheet.metadata`, `row.metadata`, `col.metadata`
