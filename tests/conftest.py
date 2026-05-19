"""Test-suite shared fixtures and import-order workaround."""

# Importing `Spreadsheet` directly trips a circular import because the
# canonical entry point (`gservices.GoogleServices`) uses PEP 690 lazy imports
# that don't eagerly load `gservices.drive`. Forcing the drive module to load
# first breaks the cycle for the test environment.
from gservices.drive.drive_service import (
    DriveService,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
