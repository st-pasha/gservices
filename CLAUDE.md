# Repo conventions for code agents

## Imports

Prefer **top-of-file imports** whenever possible. Function-local or
bottom-of-file imports are an escape hatch — use them only when needed
to break a real circular import cycle (e.g. the
`gservices.drive.file ↔ gservices.drive.folder ↔ gservices.drive.spreadsheet_file`
triangle, where `SpreadsheetFile` is imported function-locally in
`File.resolve_from_mime` / `File.is_spreadsheet` / `Folder.make_file`).

When you do use a deferred import to break a cycle, leave a short comment
explaining which cycle it breaks, so the next reader doesn't "fix" it by
hoisting it back to the top.
