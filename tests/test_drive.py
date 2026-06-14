"""
Unit tests for `gservices.drive`. Each test wires up a `DriveService` against
a `MagicMock` `DriveResource` whose method chains are pre-seeded with canned
responses.
"""

import pathlib
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from gservices.drive.drive_service import DriveService
from gservices.drive.folder import Folder
from gservices.drive.path import Path

FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_MIME = "application/vnd.google-apps.document"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

USER_DRIVE = {"id": "USERDRIVE", "name": "My Drive", "mimeType": FOLDER_MIME}


def _http_error(status: int) -> HttpError:
    """Build an HttpError that mimics the googleapiclient one with the given
    HTTP status — the only field the Drive code inspects."""
    resp = MagicMock()
    resp.status = status
    return HttpError(resp, b"{}")


def _make_drive(
    files_list_pages: list[dict[str, Any]] | None = None,
    files_get_by_id: dict[str, dict[str, Any]] | None = None,
    drives_list_pages: list[dict[str, Any]] | None = None,
    files_get_404: set[str] | None = None,
) -> tuple[DriveService, MagicMock]:
    """
    Build a `DriveService` backed by a `MagicMock` resource.

    `files_list_pages` queues responses for successive `files().list(...).execute()`
    calls; pass one dict per expected page.

    `files_get_by_id` maps file ids to canned responses for `files().get(fileId=...)`.

    `drives_list_pages` queues responses for successive `drives().list(...).execute()`
    calls. If omitted, a single empty page is returned (no shared drives).
    """
    resource = MagicMock()

    # files().get(fileId=...) — the constructor calls this once for "root";
    # the side_effect routes by call args so a single mock handles both that
    # bootstrap call and subsequent `_fetch_file_by_id` lookups.
    files_get_by_id = dict(files_get_by_id or {})
    files_get_404 = set(files_get_404 or set())

    def _files_get(*, fileId: str, **_kwargs: Any) -> MagicMock:
        execute = MagicMock()
        if fileId in files_get_404:
            execute.execute.side_effect = _http_error(404)
        elif fileId == "root":
            execute.execute.return_value = USER_DRIVE
        elif fileId in files_get_by_id:
            execute.execute.return_value = files_get_by_id[fileId]
        else:
            raise AssertionError(f"Unexpected files().get(fileId={fileId!r}) call")
        return execute

    resource.files.return_value.get.side_effect = _files_get

    # drives().list() — successive calls cycle through queued pages.
    drives_list_execute = resource.drives.return_value.list.return_value.execute
    drives_list_execute.side_effect = list(drives_list_pages or [{"drives": []}])

    # files().list(...) — successive calls cycle through queued pages.
    files_list_execute = resource.files.return_value.list.return_value.execute
    files_list_execute.side_effect = list(files_list_pages or [])

    return DriveService(resource), resource


# ----------------------------------------------------------------------------
# Bug #1 — `~/<...>` path resolution drops the leading "~"
# ----------------------------------------------------------------------------


class TestTildePathResolution:
    def test_tilde_alone_resolves_to_user_drive(self):
        drive, _ = _make_drive()
        assert Path.from_string("~", drive) == drive.user_drive.path

    def test_tilde_slash_foo_does_not_duplicate_tilde(self):
        drive, _ = _make_drive()
        path = Path.from_string("~/foo", drive)
        assert str(path) == "/My Drive/foo"

    def test_tilde_slash_multi_segment(self):
        drive, _ = _make_drive()
        path = Path.from_string("~/foo/bar", drive)
        assert str(path) == "/My Drive/foo/bar"

    def test_tilde_with_trailing_slash_preserves_tail(self):
        drive, _ = _make_drive()
        path = Path.from_string("~/foo/", drive)
        assert str(path) == "/My Drive/foo"
        assert path.has_tail is True


# ----------------------------------------------------------------------------
# Path equality must agree with hash — `has_tail` is a syntactic hint, not
# part of identity. (Surfaced while wiring up the cp tests below.)
# ----------------------------------------------------------------------------


class TestPathEqualityIgnoresTail:
    def test_eq_ignores_tail(self):
        assert Path(("", "A"), has_tail=True) == Path(("", "A"), has_tail=False)

    def test_eq_matches_hash(self):
        a = Path(("", "A"), has_tail=True)
        b = Path(("", "A"), has_tail=False)
        # The original bug: equal hashes, unequal __eq__ → dict lookups missed.
        assert hash(a) == hash(b) and a == b

    def test_tailed_path_finds_untailed_cached_entry(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {
                    "files": [
                        {"id": "D", "name": "D", "mimeType": FOLDER_MIME,
                         "parents": ["USERDRIVE"]},
                    ]
                },
            ],
        )
        untailed = drive.get("~/D")
        tailed = drive.get("~/D/")
        assert tailed is untailed


# ----------------------------------------------------------------------------
# Bug #2 — `cp("A/", "B/")` puts copies at the wrong path with the wrong name
# ----------------------------------------------------------------------------


class TestCpFolderContents:
    def test_cp_tail_to_tail_preserves_names_and_targets_dest_folder(self):
        # Folder A contains files x.txt and y.txt; folder B is the destination.
        drive, resource = _make_drive(
            files_list_pages=[
                # First listing: My Drive contains A and B.
                {
                    "files": [
                        {"id": "A", "name": "A", "mimeType": FOLDER_MIME,
                         "parents": ["USERDRIVE"]},
                        {"id": "B", "name": "B", "mimeType": FOLDER_MIME,
                         "parents": ["USERDRIVE"]},
                    ]
                },
                # Second listing: contents of A.
                {
                    "files": [
                        {"id": "X", "name": "x.txt", "mimeType": DOC_MIME,
                         "parents": ["A"]},
                        {"id": "Y", "name": "y.txt", "mimeType": DOC_MIME,
                         "parents": ["A"]},
                    ]
                },
            ],
        )

        # `files().copy()` returns the new file's metadata. Each call should
        # land inside B with the original name.
        copy_execute = resource.files.return_value.copy.return_value.execute
        copy_execute.side_effect = [
            {"id": "X2", "name": "x.txt", "mimeType": DOC_MIME, "parents": ["B"]},
            {"id": "Y2", "name": "y.txt", "mimeType": DOC_MIME, "parents": ["B"]},
        ]

        drive.cp("~/A/", "~/B/")

        copy_calls = resource.files.return_value.copy.call_args_list
        assert len(copy_calls) == 2
        bodies = [c.kwargs["body"] for c in copy_calls]
        assert bodies[0] == {"name": "x.txt", "parents": ["B"]}
        assert bodies[1] == {"name": "y.txt", "parents": ["B"]}

    def test_cp_single_file_to_tail_keeps_original_name(self):
        # Sanity check: copying a single file into a tailed destination should
        # preserve the source's name (regression guard for the analogous path).
        drive, resource = _make_drive(
            files_list_pages=[
                {
                    "files": [
                        {"id": "F", "name": "f.txt", "mimeType": DOC_MIME,
                         "parents": ["USERDRIVE"]},
                        {"id": "B", "name": "B", "mimeType": FOLDER_MIME,
                         "parents": ["USERDRIVE"]},
                    ]
                },
            ],
        )
        copy_execute = resource.files.return_value.copy.return_value.execute
        copy_execute.return_value = {
            "id": "F2", "name": "f.txt", "mimeType": DOC_MIME, "parents": ["B"],
        }

        drive.cp("~/f.txt", "~/B/")

        body = resource.files.return_value.copy.call_args.kwargs["body"]
        assert body == {"name": "f.txt", "parents": ["B"]}


# ----------------------------------------------------------------------------
# Bug #3 — `rename()` leaves the stale `_path` cached
# ----------------------------------------------------------------------------


class TestRenameInvalidatesPath:
    def test_get_by_new_path_succeeds_after_rename(self):
        drive, resource = _make_drive(
            files_list_pages=[
                {
                    "files": [
                        {"id": "F", "name": "old.txt", "mimeType": DOC_MIME,
                         "parents": ["USERDRIVE"]},
                    ]
                },
            ],
        )

        file = drive.get("~/old.txt")
        assert str(file.path) == "/My Drive/old.txt"

        # The rename response carries the new metadata.
        update_execute = resource.files.return_value.update.return_value.execute
        update_execute.return_value = {
            "id": "F", "name": "new.txt", "mimeType": DOC_MIME,
            "parents": ["USERDRIVE"],
        }

        file.rename("new.txt")

        # After rename, the file's own path reflects the new name…
        assert str(file.path) == "/My Drive/new.txt"
        # …and the drive's path cache routes the new path to the same object.
        assert drive.get("~/new.txt") is file

    def test_old_path_no_longer_resolves(self):
        drive, resource = _make_drive(
            files_list_pages=[
                {
                    "files": [
                        {"id": "F", "name": "old.txt", "mimeType": DOC_MIME,
                         "parents": ["USERDRIVE"]},
                    ]
                },
                # If `get()` falls back to re-listing the parent (because the
                # cache missed), the second page returns just the renamed file
                # — and the lookup should still fail for the old name.
                {
                    "files": [
                        {"id": "F", "name": "new.txt", "mimeType": DOC_MIME,
                         "parents": ["USERDRIVE"]},
                    ]
                },
            ],
        )
        file = drive.get("~/old.txt")
        update_execute = resource.files.return_value.update.return_value.execute
        update_execute.return_value = {
            "id": "F", "name": "new.txt", "mimeType": DOC_MIME,
            "parents": ["USERDRIVE"],
        }
        file.rename("new.txt")

        with pytest.raises(FileNotFoundError):
            drive.get("~/old.txt")


# ----------------------------------------------------------------------------
# Bug #4 — folder listing requests `fields=files(...)`, dropping nextPageToken
# ----------------------------------------------------------------------------


class TestFolderListPagination:
    def test_list_request_asks_for_next_page_token(self):
        drive, resource = _make_drive(
            files_list_pages=[
                {
                    "files": [
                        {"id": "A", "name": "a", "mimeType": DOC_MIME,
                         "parents": ["USERDRIVE"]},
                    ]
                },
            ],
        )

        user_drive = cast(Folder, drive.get("~"))
        user_drive.list()

        list_kwargs = resource.files.return_value.list.call_args.kwargs
        assert "nextPageToken" in list_kwargs["fields"]
        assert "files(" in list_kwargs["fields"]

    def test_list_follows_next_page_token_across_pages(self):
        drive, resource = _make_drive(
            files_list_pages=[
                {
                    "files": [
                        {"id": "A", "name": "a", "mimeType": DOC_MIME,
                         "parents": ["USERDRIVE"]},
                    ],
                    "nextPageToken": "PAGE2",
                },
                {
                    "files": [
                        {"id": "B", "name": "b", "mimeType": DOC_MIME,
                         "parents": ["USERDRIVE"]},
                    ],
                },
            ],
        )

        user_drive = cast(Folder, drive.get("~"))
        names = [f.name for f in user_drive.list()]
        assert names == ["a", "b"]

        # And the second call must have forwarded the page token.
        list_calls = resource.files.return_value.list.call_args_list
        assert len(list_calls) == 2
        assert list_calls[0].kwargs["pageToken"] == ""
        assert list_calls[1].kwargs["pageToken"] == "PAGE2"


# ----------------------------------------------------------------------------
# Bug #5 — Root._fetch_files didn't paginate `drives().list()`
# ----------------------------------------------------------------------------


class TestSharedDrivesPagination:
    def test_drives_list_follows_next_page_token(self):
        # Two pages of shared drives.
        drive, resource = _make_drive(
            drives_list_pages=[
                {
                    "drives": [
                        {"id": "SD1", "name": "Alpha",
                         "mimeType": FOLDER_MIME},
                    ],
                    "nextPageToken": "PAGE2",
                },
                {
                    "drives": [
                        {"id": "SD2", "name": "Beta",
                         "mimeType": FOLDER_MIME},
                    ],
                },
            ],
        )
        # Root's listing now includes both drives.
        names = [f.name for f in drive.ls("/")]
        assert "Alpha" in names and "Beta" in names

        drives_calls = resource.drives.return_value.list.call_args_list
        assert len(drives_calls) == 2
        assert drives_calls[0].kwargs["pageToken"] == ""
        assert drives_calls[1].kwargs["pageToken"] == "PAGE2"


# ----------------------------------------------------------------------------
# Bug #6 + #9 — find() now exact-matches before regex, anchors regex, and
# returns [] for unparseable patterns instead of crashing.
# ----------------------------------------------------------------------------


class TestFindMatching:
    def _drive_with_files(self, *names: str) -> DriveService:
        drive, _ = _make_drive(
            files_list_pages=[
                {
                    "files": [
                        {"id": f"F{i}", "name": n, "mimeType": DOC_MIME,
                         "parents": ["USERDRIVE"]}
                        for i, n in enumerate(names)
                    ]
                }
            ],
        )
        return drive

    def test_regex_is_anchored_at_both_ends(self):
        drive = self._drive_with_files("foo", "foobar")
        # Pre-fix, `re.match("foo", ...)` matched both.
        names = [f.name for f in drive.find("~/foo")]
        assert names == ["foo"]

    def test_exact_match_beats_regex_interpretation(self):
        # The literal name contains regex metacharacters. The exact-match
        # branch must fire first so the user gets the file they asked for.
        drive = self._drive_with_files("Report (Q1).csv", "Report Q1.csv")
        names = [f.name for f in drive.find("~/Report (Q1).csv")]
        assert names == ["Report (Q1).csv"]

    def test_unparseable_regex_returns_empty(self):
        drive = self._drive_with_files("a.csv", "b.csv")
        # Pre-fix, `*.csv` raised re.error: nothing to repeat.
        assert drive.find("~/*.csv") == []

    def test_star_lists_everything(self):
        drive = self._drive_with_files("a", "b", "c")
        names = sorted(f.name for f in drive.find("~/*"))
        assert names == ["a", "b", "c"]


# ----------------------------------------------------------------------------
# Bug #7 — delete must be refused on Root, UserDrive, and SharedDrive
# ----------------------------------------------------------------------------


class TestDeleteGuards:
    def test_root_delete_raises(self):
        drive, _ = _make_drive()
        root = drive.get("/")
        with pytest.raises(RuntimeError):
            root.delete()

    def test_user_drive_delete_raises(self):
        drive, _ = _make_drive()
        ud = drive.user_drive
        with pytest.raises(RuntimeError):
            ud.delete()

    def test_shared_drive_delete_raises(self):
        drive, _ = _make_drive(
            drives_list_pages=[
                {
                    "drives": [
                        {"id": "SD", "name": "MyTeam",
                         "mimeType": FOLDER_MIME},
                    ]
                },
            ],
        )
        sd = drive.get("/MyTeam")
        with pytest.raises(RuntimeError):
            sd.delete()


# ----------------------------------------------------------------------------
# Bug #8 — duplicate names within a folder
# ----------------------------------------------------------------------------


class TestDuplicateNameCache:
    def test_get_on_ambiguous_path_raises(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {
                    "files": [
                        {"id": "F1", "name": "twin.txt", "mimeType": DOC_MIME,
                         "parents": ["USERDRIVE"]},
                        {"id": "F2", "name": "twin.txt", "mimeType": DOC_MIME,
                         "parents": ["USERDRIVE"]},
                    ]
                }
            ],
        )
        with pytest.raises(ValueError, match="ambiguous"):
            drive.get("~/twin.txt")

    def test_get_by_id_works_for_both_twins(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {
                    "files": [
                        {"id": "F1", "name": "twin.txt", "mimeType": DOC_MIME,
                         "parents": ["USERDRIVE"]},
                        {"id": "F2", "name": "twin.txt", "mimeType": DOC_MIME,
                         "parents": ["USERDRIVE"]},
                    ]
                }
            ],
        )
        # Force the parent listing so both twins land in the path cache.
        list(drive.ls("~"))
        f1 = drive.get(id="F1")
        f2 = drive.get(id="F2")
        assert f1.id == "F1" and f2.id == "F2"

    def test_find_returns_all_duplicates(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {
                    "files": [
                        {"id": "F1", "name": "twin.txt", "mimeType": DOC_MIME,
                         "parents": ["USERDRIVE"]},
                        {"id": "F2", "name": "twin.txt", "mimeType": DOC_MIME,
                         "parents": ["USERDRIVE"]},
                    ]
                }
            ],
        )
        ids = sorted(f.id for f in drive.find("~/twin.txt"))
        assert ids == ["F1", "F2"]

    def test_uncache_one_leaves_the_other_unambiguous(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {
                    "files": [
                        {"id": "F1", "name": "twin.txt", "mimeType": DOC_MIME,
                         "parents": ["USERDRIVE"]},
                        {"id": "F2", "name": "twin.txt", "mimeType": DOC_MIME,
                         "parents": ["USERDRIVE"]},
                    ]
                }
            ],
        )
        list(drive.ls("~"))
        f1 = drive.get(id="F1")
        drive.uncache(f1)
        # Now `twin.txt` resolves unambiguously to the survivor.
        assert drive.get("~/twin.txt").id == "F2"


# ----------------------------------------------------------------------------
# Bug #12 — `_loaded` flag avoids re-fetching for files with no `size`
# ----------------------------------------------------------------------------


class TestPropertiesLoadedFlag:
    def test_repeated_access_does_not_refetch(self):
        # A folder has no `size` even after `fields=*`. The old sentinel
        # (`"size" not in self._data`) would re-fetch on every access.
        drive, resource = _make_drive(
            files_list_pages=[
                {
                    "files": [
                        {"id": "D", "name": "D", "mimeType": FOLDER_MIME,
                         "parents": ["USERDRIVE"]},
                    ]
                },
            ],
            files_get_by_id={
                "D": {
                    "id": "D", "name": "D", "mimeType": FOLDER_MIME,
                    "parents": ["USERDRIVE"], "createdTime": "2025-01-01T00:00:00Z",
                    "modifiedTime": "2025-01-02T00:00:00Z",
                },
            },
        )
        folder = drive.get("~/D")
        # First access triggers a `fields=*` fetch.
        _ = folder.created_time
        _ = folder.modified_time
        _ = folder.created_time
        # Bootstrap call ("root") + this single property-load fetch.
        get_calls = resource.files.return_value.get.call_args_list
        property_loads = [c for c in get_calls if c.kwargs.get("fields") == "*"]
        assert len(property_loads) == 1


# ----------------------------------------------------------------------------
# Bug #13 — Path.parent on the root must raise cleanly, not assert
# ----------------------------------------------------------------------------


class TestRootParent:
    def test_root_parent_raises_value_error(self):
        root = Path(("",))
        with pytest.raises(ValueError, match="no parent"):
            _ = root.parent

    def test_root_truediv_dotdot_raises(self):
        root = Path(("",))
        with pytest.raises(ValueError, match="no parent"):
            _ = root / ".."


# ----------------------------------------------------------------------------
# Bug #14 — driveId in FIELDS, used by shared_drive_id
# ----------------------------------------------------------------------------


class TestDriveIdField:
    def test_shared_drive_id_reads_from_drive_id_field(self):
        # Build a drive with one shared drive, then fetch a file directly by
        # id whose response carries `driveId`. The shared_drive_id should be
        # read from that field directly — without walking the parent chain.
        # (We parent it on the shared drive itself so the lazy `.path`
        # resolution during caching doesn't trigger extra fetches.)
        drive, _ = _make_drive(
            drives_list_pages=[
                {
                    "drives": [
                        {"id": "SD", "name": "Team", "mimeType": FOLDER_MIME},
                    ]
                },
            ],
            files_get_by_id={
                "F": {
                    "id": "F", "name": "f.txt", "mimeType": DOC_MIME,
                    "parents": ["SD"], "driveId": "SD",
                },
            },
        )
        # Make sure the shared drive loads (Root.list) so SD is cacheable.
        list(drive.ls("/"))
        f = drive.get(id="F")
        assert f.shared_drive_id == "SD"

    def test_fields_constant_contains_drive_id(self):
        from gservices.drive.file import File

        assert "driveId" in File.FIELDS


# ----------------------------------------------------------------------------
# Bug #11 — subtree cache invalidation after move / delete
# ----------------------------------------------------------------------------


class TestSubtreeCacheInvalidation:
    def test_move_folder_makes_descendants_findable_at_new_path(self):
        drive, resource = _make_drive(
            files_list_pages=[
                # UserDrive listing: A and B.
                {
                    "files": [
                        {"id": "A", "name": "A", "mimeType": FOLDER_MIME,
                         "parents": ["USERDRIVE"]},
                        {"id": "B", "name": "B", "mimeType": FOLDER_MIME,
                         "parents": ["USERDRIVE"]},
                    ]
                },
                # A's listing: one child.
                {
                    "files": [
                        {"id": "C", "name": "c.txt", "mimeType": DOC_MIME,
                         "parents": ["A"]},
                    ]
                },
            ],
        )
        list(drive.ls("~"))     # caches A and B
        list(drive.ls("~/A"))   # caches C under /My Drive/A/c.txt

        # Now move A inside B. The update response reflects the new parent.
        update_execute = resource.files.return_value.update.return_value.execute
        update_execute.return_value = {
            "id": "A", "name": "A", "mimeType": FOLDER_MIME, "parents": ["B"],
        }
        drive.get("~/A").move_to(Path.from_string("~/B", drive))

        # C is now reachable via the new path …
        c = drive.get("~/B/A/c.txt")
        assert c.id == "C"
        # … and the old path no longer resolves (the parent listing for
        # /My Drive/A is gone too — `get` would fall back to listing, but
        # there are no further queued pages, so it should raise).
        with pytest.raises((FileNotFoundError, StopIteration)):
            drive.get("~/A/c.txt")

    def test_delete_folder_uncaches_descendants(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {
                    "files": [
                        {"id": "A", "name": "A", "mimeType": FOLDER_MIME,
                         "parents": ["USERDRIVE"]},
                    ]
                },
                {
                    "files": [
                        {"id": "C", "name": "c.txt", "mimeType": DOC_MIME,
                         "parents": ["A"]},
                    ]
                },
            ],
        )
        list(drive.ls("~"))
        list(drive.ls("~/A"))
        c = drive.get(id="C")  # should be cached after the listing above

        # Delete A.
        drive.get("~/A").delete()

        # C is no longer in the id cache (the descendant uncache fired).
        assert "C" not in drive._ids  # type: ignore[attr-defined]
        # And the file we already had a reference to wasn't mutated:
        assert c.id == "C"


# ----------------------------------------------------------------------------
# Bug #16 — mkfile accepts more kinds
# ----------------------------------------------------------------------------


class TestMkfileKinds:
    def _set_create_response(self, resource: MagicMock, response: dict[str, Any]):
        resource.files.return_value.create.return_value.execute.return_value = response

    def test_mkfile_slides(self):
        drive, resource = _make_drive()
        self._set_create_response(resource, {
            "id": "S", "name": "slides", "mimeType":
                "application/vnd.google-apps.presentation",
            "parents": ["USERDRIVE"],
        })
        drive.mkfile("~/slides", "slides")
        body = resource.files.return_value.create.call_args.kwargs["body"]
        assert body["mimeType"] == "application/vnd.google-apps.presentation"

    def test_mkfile_drawing(self):
        drive, resource = _make_drive()
        self._set_create_response(resource, {
            "id": "D", "name": "drawing", "mimeType":
                "application/vnd.google-apps.drawing",
            "parents": ["USERDRIVE"],
        })
        drive.mkfile("~/drawing", "drawing")
        body = resource.files.return_value.create.call_args.kwargs["body"]
        assert body["mimeType"] == "application/vnd.google-apps.drawing"

    def test_mkfile_unknown_kind_raises(self):
        drive, _ = _make_drive()
        with pytest.raises(ValueError, match="Unknown file kind"):
            # Pyright will flag the literal as invalid, but the runtime guard
            # is what we're testing.
            drive.mkfile("~/x", "bogus")  # type: ignore[arg-type]


# ----------------------------------------------------------------------------
# Bug #17 — recursive folder copy
# ----------------------------------------------------------------------------


class TestRecursiveFolderCopy:
    def test_copy_folder_creates_destination_and_copies_children(self):
        drive, resource = _make_drive(
            files_list_pages=[
                # UserDrive: just folder A.
                {
                    "files": [
                        {"id": "A", "name": "A", "mimeType": FOLDER_MIME,
                         "parents": ["USERDRIVE"]},
                    ]
                },
                # A: contains two files.
                {
                    "files": [
                        {"id": "X", "name": "x.txt", "mimeType": DOC_MIME,
                         "parents": ["A"]},
                        {"id": "Y", "name": "y.txt", "mimeType": DOC_MIME,
                         "parents": ["A"]},
                    ]
                },
            ],
        )

        # `files.create` (used to make the new folder) and `files.copy` are
        # different methods on the resource — set up both.
        create_execute = resource.files.return_value.create.return_value.execute
        create_execute.return_value = {
            "id": "B", "name": "B", "mimeType": FOLDER_MIME,
            "parents": ["USERDRIVE"],
        }
        copy_execute = resource.files.return_value.copy.return_value.execute
        copy_execute.side_effect = [
            {"id": "X2", "name": "x.txt", "mimeType": DOC_MIME, "parents": ["B"]},
            {"id": "Y2", "name": "y.txt", "mimeType": DOC_MIME, "parents": ["B"]},
        ]

        drive.cp("~/A", "~/B")

        # New folder B was created in the user drive.
        create_body = resource.files.return_value.create.call_args.kwargs["body"]
        assert create_body == {"name": "B", "mimeType": FOLDER_MIME,
                                "parents": ["USERDRIVE"]}

        # Each child was copied into B with its original name.
        copy_bodies = [c.kwargs["body"] for c in
                       resource.files.return_value.copy.call_args_list]
        assert {"name": "x.txt", "parents": ["B"]} in copy_bodies
        assert {"name": "y.txt", "parents": ["B"]} in copy_bodies


# ----------------------------------------------------------------------------
# Coverage: `mv` — the shell-like move API had no tests at all
# ----------------------------------------------------------------------------


class TestMv:
    def _two_folders(self) -> tuple[DriveService, MagicMock]:
        # User drive has folders A and B; A holds a single file x.txt.
        return _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "A", "name": "A", "mimeType": FOLDER_MIME,
                     "parents": ["USERDRIVE"]},
                    {"id": "B", "name": "B", "mimeType": FOLDER_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
                {"files": [
                    {"id": "X", "name": "x.txt", "mimeType": DOC_MIME,
                     "parents": ["A"]},
                ]},
            ],
        )

    def test_mv_tail_to_tail_moves_each_child_into_dest(self):
        drive, resource = self._two_folders()
        update_execute = resource.files.return_value.update.return_value.execute
        update_execute.return_value = {
            "id": "X", "name": "x.txt", "mimeType": DOC_MIME, "parents": ["B"],
        }
        drive.mv("~/A/", "~/B/")
        kwargs = resource.files.return_value.update.call_args.kwargs
        assert kwargs["addParents"] == "B"
        assert kwargs["removeParents"] == "A"

    def test_mv_file_to_tail_moves_into_folder(self):
        drive, resource = self._two_folders()
        # Force A to be listed too, then move x.txt into B.
        list(drive.ls("~/A"))
        update_execute = resource.files.return_value.update.return_value.execute
        update_execute.return_value = {
            "id": "X", "name": "x.txt", "mimeType": DOC_MIME, "parents": ["B"],
        }
        drive.mv("~/A/x.txt", "~/B/")
        kwargs = resource.files.return_value.update.call_args.kwargs
        assert kwargs["addParents"] == "B" and kwargs["removeParents"] == "A"

    def test_mv_file_to_non_tail_moves_then_renames(self):
        drive, resource = self._two_folders()
        list(drive.ls("~/A"))
        # `mv("~/A/x.txt", "~/B/y.txt")` should both move into B and rename.
        update_execute = resource.files.return_value.update.return_value.execute
        update_execute.side_effect = [
            {"id": "X", "name": "x.txt", "mimeType": DOC_MIME, "parents": ["B"]},
            {"id": "X", "name": "y.txt", "mimeType": DOC_MIME, "parents": ["B"]},
        ]
        drive.mv("~/A/x.txt", "~/B/y.txt")
        all_kwargs = [c.kwargs for c in
                      resource.files.return_value.update.call_args_list]
        # First call moved between folders, second call set the new name.
        assert all_kwargs[0]["addParents"] == "B"
        assert all_kwargs[1]["body"] == {"name": "y.txt"}

    def test_mv_source_tail_to_non_tail_raises(self):
        drive, _ = self._two_folders()
        # Forcing existence checks first.
        list(drive.ls("~"))
        with pytest.raises(ValueError, match="must have a trailing /"):
            drive.mv("~/A/", "~/B")

    def test_mv_non_directory_source_with_tail_raises(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "F", "name": "f.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        with pytest.raises(NotADirectoryError):
            drive.mv("~/f.txt/", "~/whatever/")

    def test_mv_non_directory_dest_with_tail_raises(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "S", "name": "src", "mimeType": FOLDER_MIME,
                     "parents": ["USERDRIVE"]},
                    {"id": "F", "name": "f.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        with pytest.raises(NotADirectoryError):
            drive.mv("~/src/", "~/f.txt/")


# ----------------------------------------------------------------------------
# Coverage: `cp` error paths and `mkfile` error paths
# ----------------------------------------------------------------------------


class TestCpErrorPaths:
    def test_cp_source_tail_to_non_tail_raises(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "A", "name": "A", "mimeType": FOLDER_MIME,
                     "parents": ["USERDRIVE"]},
                    {"id": "B", "name": "B", "mimeType": FOLDER_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        with pytest.raises(ValueError, match="must have a trailing /"):
            drive.cp("~/A/", "~/B")

    def test_cp_non_directory_source_tail_raises(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "F", "name": "f.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        with pytest.raises(NotADirectoryError):
            drive.cp("~/f.txt/", "~/dest/")

    def test_cp_non_directory_dest_tail_raises(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "S", "name": "src", "mimeType": FOLDER_MIME,
                     "parents": ["USERDRIVE"]},
                    {"id": "F", "name": "f.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        with pytest.raises(NotADirectoryError):
            drive.cp("~/src/", "~/f.txt/")


class TestMkfileErrors:
    def test_mkfile_at_root_raises(self):
        drive, _ = _make_drive()
        with pytest.raises(ValueError, match="within the Root"):
            drive.mkdir("/NewDrive")

    def test_mkfile_non_directory_parent_raises(self):
        # Parent path is a file, not a folder.
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "F", "name": "f.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        with pytest.raises(NotADirectoryError):
            drive.mkfile("~/f.txt/child", "document")

    def test_mkdir_creates_a_folder(self):
        drive, resource = _make_drive()
        create_execute = resource.files.return_value.create.return_value.execute
        create_execute.return_value = {
            "id": "NEW", "name": "newdir", "mimeType": FOLDER_MIME,
            "parents": ["USERDRIVE"],
        }
        drive.mkdir("~/newdir")
        body = resource.files.return_value.create.call_args.kwargs["body"]
        assert body["mimeType"] == FOLDER_MIME
        assert body["name"] == "newdir"


# ----------------------------------------------------------------------------
# Coverage: shell-like helpers (cd/pwd, ls errors, rm, exists, get(no args))
# ----------------------------------------------------------------------------


class TestShellApi:
    def test_cd_then_pwd(self):
        drive, _ = _make_drive()
        drive.cd("~")
        assert str(drive.pwd()) == "/My Drive"

    def test_pwd_starts_at_root(self):
        drive, _ = _make_drive()
        assert str(drive.pwd()) == "/"

    def test_ls_on_a_file_raises(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "F", "name": "f.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        with pytest.raises(NotADirectoryError):
            drive.ls("~/f.txt")

    def test_rm_trashes_file(self):
        drive, resource = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "F", "name": "f.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        update_execute = resource.files.return_value.update.return_value.execute
        update_execute.return_value = {}
        drive.rm("~/f.txt")
        body = resource.files.return_value.update.call_args.kwargs["body"]
        assert body == {"trashed": True}

    def test_exists_true_for_present_file(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "F", "name": "f.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        assert drive.exists("~/f.txt")

    def test_exists_false_for_missing_file(self):
        drive, _ = _make_drive(files_list_pages=[{"files": []}])
        assert not drive.exists("~/nope.txt")

    def test_get_no_args_raises_type_error(self):
        drive, _ = _make_drive()
        with pytest.raises(TypeError, match="Missing either"):
            drive.get()


# ----------------------------------------------------------------------------
# Coverage: Shortcut — resolution, brokenness, display
# ----------------------------------------------------------------------------


class TestShortcut:
    def _drive_with_shortcut(
        self, *, broken: bool = False, target_name: str = "target.txt",
        shortcut_name: str = "alias",
    ) -> tuple[DriveService, MagicMock]:
        files = [
            {"id": "S", "name": shortcut_name, "mimeType": SHORTCUT_MIME,
             "parents": ["USERDRIVE"],
             "shortcutDetails": {
                 "targetId": "MISSING" if broken else "T",
                 "targetMimeType": DOC_MIME,
             }},
        ]
        if not broken:
            files.insert(0, {
                "id": "T", "name": target_name, "mimeType": DOC_MIME,
                "parents": ["USERDRIVE"],
            })
        return _make_drive(
            files_list_pages=[{"files": files}],
            files_get_404={"MISSING"} if broken else None,
        )

    def test_resolves_target_to_real_file(self):
        from gservices.drive.shortcut import Shortcut

        drive, _ = self._drive_with_shortcut()
        list(drive.ls("~"))
        shortcut = drive.get(id="S")
        assert isinstance(shortcut, Shortcut)
        assert shortcut.target.id == "T"
        assert shortcut.is_broken is False

    def test_broken_shortcut_returns_missing_file(self):
        from gservices.drive.shortcut import MissingFile, Shortcut

        drive, _ = self._drive_with_shortcut(broken=True)
        list(drive.ls("~"))
        shortcut = drive.get(id="S")
        assert isinstance(shortcut, Shortcut)
        target = shortcut.target
        assert isinstance(target, MissingFile)
        assert target.id == "MISSING"
        # MissingFile uses a `?`-rooted sentinel path so it can't collide
        # with any real file (parts[0] == "?" instead of "").
        assert target.path._parts == ("?",)  # type: ignore[attr-defined]
        assert shortcut.is_broken is True

    def test_target_is_memoized(self):
        from gservices.drive.shortcut import Shortcut

        drive, _ = self._drive_with_shortcut()
        list(drive.ls("~"))
        shortcut = drive.get(id="S")
        assert isinstance(shortcut, Shortcut)
        first = shortcut.target
        second = shortcut.target
        # No new files().get call beyond the listing already issued.
        assert first is second

    def test_file_list_repr_without_colors(self):
        drive, _ = self._drive_with_shortcut()
        list(drive.ls("~"))
        shortcut = drive.get(id="S")
        rep = shortcut.file_list_repr(use_colors=False)
        assert "alias" in rep
        assert "↪" in rep  # the curved-arrow icon for live shortcut

    def test_broken_file_list_repr_uses_x_icon(self):
        drive, _ = self._drive_with_shortcut(broken=True)
        list(drive.ls("~"))
        shortcut = drive.get(id="S")
        rep = shortcut.file_list_repr(use_colors=False)
        assert "✘" in rep  # ✘ for broken

    def test_file_list_repr_with_colors_substitutes_shortcut_name(self):
        # Shortcut name differs from target name — the colored repr should
        # show the shortcut's name, not the target's.
        drive, _ = self._drive_with_shortcut(
            shortcut_name="my-alias", target_name="real.txt",
        )
        list(drive.ls("~"))
        shortcut = drive.get(id="S")
        rep = shortcut.file_list_repr(use_colors=True)
        assert "my-alias" in rep
        assert "real.txt" not in rep


# ----------------------------------------------------------------------------
# Coverage: File extended properties — single fetch populates everything
# ----------------------------------------------------------------------------


class TestExtendedProperties:
    def _drive_with_fully_loaded_file(self) -> tuple[DriveService, MagicMock, Any]:
        # The `*` fetch returns every extended field at once.
        full = {
            "id": "F", "name": "f.txt", "mimeType": DOC_MIME,
            "parents": ["USERDRIVE"],
            "size": "4096",
            "createdTime": "2024-01-02T03:04:05Z",
            "modifiedTime": "2024-03-04T05:06:07Z",
            "starred": True,
            "trashed": False,
            "explicitlyTrashed": False,
            "version": "42",
        }
        drive, resource = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "F", "name": "f.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
            files_get_by_id={"F": full},
        )
        f = drive.get("~/f.txt")
        return drive, resource, f

    def test_size(self):
        import datetime as dt

        _, _, f = self._drive_with_fully_loaded_file()
        assert f.size == 4096
        assert f.created_time == dt.datetime.fromisoformat("2024-01-02T03:04:05+00:00")
        assert f.modified_time == dt.datetime.fromisoformat("2024-03-04T05:06:07+00:00")
        assert f.starred is True
        assert f.trashed is False
        assert f.explicitly_trashed is False
        assert f.version == 42


# ----------------------------------------------------------------------------
# Coverage: File.parent when "parents" is missing → returns Root
# ----------------------------------------------------------------------------


class TestFileLazyParent:
    def test_file_without_parents_field_has_root_parent(self):
        from gservices.drive.root import Root

        drive, _ = _make_drive(
            files_get_by_id={
                "F": {"id": "F", "name": "orphan", "mimeType": DOC_MIME},
            },
        )
        f = drive.get(id="F")
        assert isinstance(f.parent, Root)


# ----------------------------------------------------------------------------
# Coverage: _fetch_file_by_id surfaces 404 as FileNotFoundError
# ----------------------------------------------------------------------------


class TestFetchByIdNotFound:
    def test_404_becomes_file_not_found(self):
        drive, _ = _make_drive(files_get_404={"NOPE"})
        with pytest.raises(FileNotFoundError, match="NOPE"):
            drive.get(id="NOPE")


# ----------------------------------------------------------------------------
# Coverage: find() — `**` and mime_type filter
# ----------------------------------------------------------------------------


class TestFindRecursive:
    def test_double_star_returns_all_descendants(self):
        drive, _ = _make_drive(
            files_list_pages=[
                # User drive: contains folder A and file q.txt.
                {"files": [
                    {"id": "A", "name": "A", "mimeType": FOLDER_MIME,
                     "parents": ["USERDRIVE"]},
                    {"id": "Q", "name": "q.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
                # Inside A: B (folder) and x.txt.
                {"files": [
                    {"id": "B", "name": "B", "mimeType": FOLDER_MIME,
                     "parents": ["A"]},
                    {"id": "X", "name": "x.txt", "mimeType": DOC_MIME,
                     "parents": ["A"]},
                ]},
                # Inside B: y.txt.
                {"files": [
                    {"id": "Y", "name": "y.txt", "mimeType": DOC_MIME,
                     "parents": ["B"]},
                ]},
            ],
        )
        ids = sorted(f.id for f in drive.find("~/**"))
        assert ids == ["A", "B", "Q", "X", "Y"]

    def test_find_filters_by_mime_type(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "S", "name": "a", "mimeType": SHEET_MIME,
                     "parents": ["USERDRIVE"]},
                    {"id": "D", "name": "a", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        result = drive.find("~/a", mime_type=SHEET_MIME)
        assert [f.id for f in result] == ["S"]


# ----------------------------------------------------------------------------
# Coverage: Path.from_string — `.`, `..`, normalization, repr
# ----------------------------------------------------------------------------


class TestPathParsing:
    def test_dot_resolves_to_pwd(self):
        drive, _ = _make_drive()
        drive.cd("~")
        assert Path.from_string(".", drive) == drive.pwd()

    def test_empty_resolves_to_pwd(self):
        drive, _ = _make_drive()
        assert Path.from_string("", drive) == drive.pwd()

    def test_relative_path_is_prepended_with_pwd(self):
        drive, _ = _make_drive()
        drive.cd("~")
        p = Path.from_string("sub", drive)
        assert str(p) == "/My Drive/sub"

    def test_dotdot_climbs_one_level(self):
        drive, _ = _make_drive()
        p = Path.from_string("/A/B/..", drive)
        assert str(p) == "/A"

    def test_single_dot_in_path_is_dropped(self):
        drive, _ = _make_drive()
        p = Path.from_string("/A/./B", drive)
        assert str(p) == "/A/B"

    def test_dotdot_past_root_raises(self):
        drive, _ = _make_drive()
        with pytest.raises(ValueError, match="beyond the Root"):
            Path.from_string("/..", drive)

    def test_repr_includes_path(self):
        assert repr(Path(("", "A", "B"))) == "Path(/A/B)"

    def test_eq_against_non_path_is_false(self):
        assert (Path(("",)) == "/") is False


# ----------------------------------------------------------------------------
# Coverage: file_list_repr on each concrete type + FileList.__repr__
# ----------------------------------------------------------------------------


class TestDisplayRepr:
    def test_file_base_repr_returns_name(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "F", "name": "f.txt", "mimeType":
                        "application/octet-stream",
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        f = drive.get("~/f.txt")
        assert f.file_list_repr() == "f.txt"
        assert str(f) == "/My Drive/f.txt"
        assert "/My Drive/f.txt" in repr(f)

    def test_folder_with_colors_wraps_with_ansi(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "D", "name": "D", "mimeType": FOLDER_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        folder = drive.get("~/D")
        with_colors = folder.file_list_repr(use_colors=True)
        plain = folder.file_list_repr(use_colors=False)
        assert "\033[" in with_colors and "D" in with_colors
        assert plain == "D"

    def test_shared_drive_and_user_drive_repr(self):
        drive, _ = _make_drive(
            drives_list_pages=[
                {"drives": [
                    {"id": "SD", "name": "Team", "mimeType": FOLDER_MIME},
                ]},
            ],
        )
        list(drive.ls("/"))
        sd = drive.get("/Team")
        ud = drive.user_drive
        # Smoke: each has its own color sequence; without colors strips it.
        assert sd.file_list_repr(use_colors=True) != sd.name
        assert sd.file_list_repr(use_colors=False) == "Team"
        assert ud.file_list_repr(use_colors=True) != ud.name
        assert ud.file_list_repr(use_colors=False) == "My Drive"

    def test_document_and_spreadsheet_repr(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "DO", "name": "doc", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                    {"id": "SH", "name": "sheet", "mimeType": SHEET_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        doc = drive.get("~/doc")
        sheet = drive.get("~/sheet")
        assert doc.file_list_repr(use_colors=False) == "doc"
        assert sheet.file_list_repr(use_colors=False) == "sheet"
        # With colors the names are wrapped in ANSI escapes.
        assert "doc" in doc.file_list_repr(use_colors=True)
        assert "\033[" in sheet.file_list_repr(use_colors=True)

    def test_file_list_repr_lists_dirs_before_files(self):
        from gservices.drive.file_list import FileList

        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "A", "name": "A", "mimeType": FOLDER_MIME,
                     "parents": ["USERDRIVE"]},
                    {"id": "F", "name": "f.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        ud = cast(Folder, drive.get("~"))
        listing = ud.list()
        assert isinstance(listing, FileList)
        # Disable colors so the assertion isn't fooled by escapes.
        FileList.USE_COLORS = False
        try:
            rep = repr(listing)
            # The folder should appear before the file in the rendered output.
            assert rep.index("A") < rep.index("f.txt")
        finally:
            FileList.USE_COLORS = True


# ----------------------------------------------------------------------------
# Coverage: Folder.handle_file_added/removed when file_list is not loaded
# ----------------------------------------------------------------------------


class TestUncachedHandlers:
    def test_handle_added_noop_when_list_uncached(self):
        # A handler call on a folder whose `_file_list` is None should be a
        # no-op (no crash). Construct a Folder without ever listing it.
        drive, _ = _make_drive()
        ud = cast(Folder, drive.get("~"))
        # Folder constructed but never listed.
        assert ud._file_list is None  # type: ignore[attr-defined]
        # Build a throwaway file object to hand to the handler.
        from gservices.drive.file import File as FileCls

        ghost = FileCls(
            {"id": "G", "name": "ghost", "mimeType": DOC_MIME,
             "parents": ["USERDRIVE"]},
            drive,
        )
        ud.handle_file_added(ghost)  # should not crash
        ud.handle_file_removed(ghost)  # should not crash
        assert ud._file_list is None  # type: ignore[attr-defined]


# ----------------------------------------------------------------------------
# Coverage: remaining branches — type predicates, error paths, edge cases
# ----------------------------------------------------------------------------


class TestIsPredicates:
    def test_is_predicates_match_concrete_types(self):
        drive, _ = _make_drive(
            drives_list_pages=[
                {"drives": [
                    {"id": "SD", "name": "Team", "mimeType": FOLDER_MIME},
                ]},
            ],
            files_list_pages=[
                {"files": [
                    {"id": "DO", "name": "doc", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                    {"id": "SH", "name": "sheet", "mimeType": SHEET_MIME,
                     "parents": ["USERDRIVE"]},
                    {"id": "S", "name": "alias", "mimeType": SHORTCUT_MIME,
                     "parents": ["USERDRIVE"],
                     "shortcutDetails": {"targetId": "DO",
                                         "targetMimeType": DOC_MIME}},
                ]},
            ],
        )
        list(drive.ls("/"))
        list(drive.ls("~"))
        doc = drive.get(id="DO")
        sheet = drive.get(id="SH")
        shortcut = drive.get(id="S")
        sd = drive.get(id="SD")

        assert doc.is_document and not doc.is_spreadsheet
        assert sheet.is_spreadsheet and not sheet.is_document
        assert shortcut.is_shortcut
        assert sd.is_shared_drive


class TestMoveToEdgeCases:
    def test_move_to_non_directory_dest_raises(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "F", "name": "f.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                    {"id": "G", "name": "g.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        f = drive.get("~/f.txt")
        with pytest.raises(NotADirectoryError):
            # `g.txt` is a file, not a folder — must be rejected as a target.
            f.move_to(Path.from_string("~/g.txt", drive))

    def test_move_to_same_parent_is_a_noop(self):
        drive, resource = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "F", "name": "f.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        f = drive.get("~/f.txt")
        f.move_to(Path.from_string("~", drive))
        # No `files.update` was issued — the short-circuit fired.
        assert not resource.files.return_value.update.return_value.execute.called


class TestCopyToErrorPath:
    def test_copy_to_non_directory_parent_raises(self):
        # Destination's parent is a file, not a folder.
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "S", "name": "src.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                    {"id": "F", "name": "f.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        src = drive.get("~/src.txt")
        with pytest.raises(NotADirectoryError):
            src.copy_to(Path.from_string("~/f.txt/child.txt", drive))


class TestDeletePermanent:
    def test_delete_with_trash_false_calls_files_delete(self):
        drive, resource = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "F", "name": "f.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        delete_execute = resource.files.return_value.delete.return_value.execute
        delete_execute.return_value = None
        drive.get("~/f.txt").delete(trash=False)
        kwargs = resource.files.return_value.delete.call_args.kwargs
        assert kwargs["fileId"] == "F"
        # And `update` was not called (no `trashed: True` body).
        assert not resource.files.return_value.update.return_value.execute.called


class TestFindRegexFallback:
    def test_regex_fallback_matches_when_no_exact_name(self):
        # No file is literally named "f.*" — fall back to regex.
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "F1", "name": "foo", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                    {"id": "B", "name": "bar", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        ids = sorted(f.id for f in drive.find("~/f.*"))
        assert ids == ["F1"]


class TestMkfileSpreadsheet:
    def test_mkfile_spreadsheet_uses_spreadsheet_mime(self):
        drive, resource = _make_drive()
        create_execute = resource.files.return_value.create.return_value.execute
        create_execute.return_value = {
            "id": "S", "name": "sheet", "mimeType": SHEET_MIME,
            "parents": ["USERDRIVE"],
        }
        drive.mkfile("~/sheet", "spreadsheet")
        body = resource.files.return_value.create.call_args.kwargs["body"]
        assert body["mimeType"] == SHEET_MIME


class TestMimeTypeOverrides:
    def test_folder_mime_type_property_is_folder(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "D", "name": "D", "mimeType": FOLDER_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        folder = drive.get("~/D")
        assert folder.mime_type == FOLDER_MIME

    def test_shortcut_mime_type_property_is_shortcut(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "T", "name": "t.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                    {"id": "S", "name": "s", "mimeType": SHORTCUT_MIME,
                     "parents": ["USERDRIVE"],
                     "shortcutDetails": {"targetId": "T",
                                         "targetMimeType": DOC_MIME}},
                ]},
            ],
        )
        list(drive.ls("~"))
        assert drive.get(id="S").mime_type == SHORTCUT_MIME


class TestFileListColoredRepr:
    def test_repr_with_default_colors_includes_ansi(self):
        from gservices.drive.file_list import FileList

        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "F", "name": "f.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        ud = cast(Folder, drive.get("~"))
        FileList.USE_COLORS = True  # explicit (this is the module default)
        rep = repr(ud.list())
        assert "\033[" in rep


class TestRecursiveFolderInvalidation:
    def test_move_folder_with_subfolders_recurses(self):
        # Folder structure: ~/A contains subfolder S, which contains file C.
        # After moving A → ~/B/A, all three should be reachable at the new
        # paths and unreachable at the old ones.
        drive, resource = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "A", "name": "A", "mimeType": FOLDER_MIME,
                     "parents": ["USERDRIVE"]},
                    {"id": "B", "name": "B", "mimeType": FOLDER_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
                {"files": [
                    {"id": "S", "name": "S", "mimeType": FOLDER_MIME,
                     "parents": ["A"]},
                ]},
                {"files": [
                    {"id": "C", "name": "c.txt", "mimeType": DOC_MIME,
                     "parents": ["S"]},
                ]},
            ],
        )
        list(drive.ls("~"))
        list(drive.ls("~/A"))
        list(drive.ls("~/A/S"))

        update_execute = resource.files.return_value.update.return_value.execute
        update_execute.return_value = {
            "id": "A", "name": "A", "mimeType": FOLDER_MIME, "parents": ["B"],
        }
        drive.get("~/A").move_to(Path.from_string("~/B", drive))

        # The deeply nested descendant is reachable at the new path.
        assert drive.get("~/B/A/S/c.txt").id == "C"

    def test_delete_folder_with_subfolders_uncaches_recursively(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "A", "name": "A", "mimeType": FOLDER_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
                {"files": [
                    {"id": "S", "name": "S", "mimeType": FOLDER_MIME,
                     "parents": ["A"]},
                ]},
                {"files": [
                    {"id": "C", "name": "c.txt", "mimeType": DOC_MIME,
                     "parents": ["S"]},
                ]},
            ],
        )
        list(drive.ls("~"))
        list(drive.ls("~/A"))
        list(drive.ls("~/A/S"))

        drive.get("~/A").delete()

        # Every descendant id is purged from the id cache.
        for purged in ("A", "S", "C"):
            assert purged not in drive._ids  # type: ignore[attr-defined]


class TestFetchByIdNon404Error:
    def test_non_404_http_error_is_reraised(self):
        # A 500 should bubble up, not become FileNotFoundError.
        drive, resource = _make_drive()
        execute = MagicMock()
        execute.execute.side_effect = _http_error(500)

        def _files_get(**_kwargs: Any) -> MagicMock:
            return execute

        resource.files.return_value.get.side_effect = _files_get
        with pytest.raises(HttpError):
            drive.get(id="WHATEVER")


class TestLastFewBranches:
    def test_root_parent_raises(self):
        # Concrete Root.parent override.
        drive, _ = _make_drive()
        with pytest.raises(ValueError, match="doesn't have a parent"):
            _ = drive.get("/").parent

    def test_get_with_non_folder_parent_raises(self):
        # `get("/A/B/x")` where `/A/B` is a file → the recursive parent
        # lookup hits NotADirectoryError inside `get`.
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "F", "name": "f.txt", "mimeType": DOC_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        with pytest.raises(NotADirectoryError, match="not a directory"):
            drive.get("~/f.txt/child")

    def test_rename_folder_invalidates_descendant_paths(self):
        drive, resource = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "A", "name": "A", "mimeType": FOLDER_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
                {"files": [
                    {"id": "C", "name": "c.txt", "mimeType": DOC_MIME,
                     "parents": ["A"]},
                ]},
            ],
        )
        list(drive.ls("~"))
        list(drive.ls("~/A"))

        update_execute = resource.files.return_value.update.return_value.execute
        update_execute.return_value = {
            "id": "A", "name": "A2", "mimeType": FOLDER_MIME,
            "parents": ["USERDRIVE"],
        }
        drive.get("~/A").rename("A2")
        # The descendant is reachable under the new parent name…
        assert drive.get("~/A2/c.txt").id == "C"

    def test_move_unlisted_folder_is_safe(self):
        # Moving a folder that hasn't been listed must not crash on the
        # descendant-invalidation early-return.
        drive, resource = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "A", "name": "A", "mimeType": FOLDER_MIME,
                     "parents": ["USERDRIVE"]},
                    {"id": "B", "name": "B", "mimeType": FOLDER_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        # Listing the user drive caches A and B but NOT A's contents.
        list(drive.ls("~"))
        update_execute = resource.files.return_value.update.return_value.execute
        update_execute.return_value = {
            "id": "A", "name": "A", "mimeType": FOLDER_MIME, "parents": ["B"],
        }
        drive.get("~/A").move_to(Path.from_string("~/B", drive))
        # No crash; the moved folder is still reachable by id.
        assert drive.get(id="A").name == "A"

    def test_delete_unlisted_folder_is_safe(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "A", "name": "A", "mimeType": FOLDER_MIME,
                     "parents": ["USERDRIVE"]},
                ]},
            ],
        )
        list(drive.ls("~"))  # A is cached, but its contents are not.
        drive.get("~/A").delete()
        assert "A" not in drive._ids  # type: ignore[attr-defined]


# ----------------------------------------------------------------------------
# Issue #23 — first-class content upload / download
# ----------------------------------------------------------------------------


class TestUploadDownload:
    def _set_create(self, resource: MagicMock, response: dict[str, Any]):
        resource.files.return_value.create.return_value.execute.return_value = response

    def _media(self, resource: MagicMock) -> Any:
        """The `media_body` passed to the last files().create call."""
        return resource.files.return_value.create.call_args.kwargs["media_body"]

    def _media_bytes(self, media: Any) -> bytes:
        return media.getbytes(0, media.size())

    def test_upload_bytes(self):
        drive, resource = _make_drive()
        self._set_create(resource, {
            "id": "F1", "name": "data.bin", "mimeType": "application/octet-stream",
            "parents": ["USERDRIVE"],
        })
        f = drive.user_drive.upload("data.bin", b"\x00\x01\x02")

        media = self._media(resource)
        assert self._media_bytes(media) == b"\x00\x01\x02"
        # Unknown extension -> octet-stream fallback.
        assert media.mimetype() == "application/octet-stream"
        kwargs = resource.files.return_value.create.call_args.kwargs
        assert kwargs["body"] == {"name": "data.bin", "parents": ["USERDRIVE"]}
        assert kwargs["supportsAllDrives"] is True
        # The returned file is cached and addressable by path afterwards.
        assert f.id == "F1"
        assert drive.get("~/data.bin") is f

    def test_upload_str_defaults_to_text_plain(self):
        drive, resource = _make_drive()
        self._set_create(resource, {
            "id": "N", "name": "notes", "mimeType": "text/plain",
            "parents": ["USERDRIVE"],
        })
        drive.user_drive.upload("notes", "hello world")

        media = self._media(resource)
        assert self._media_bytes(media) == b"hello world"
        assert media.mimetype() == "text/plain"

    def test_upload_infers_mime_from_name(self):
        drive, resource = _make_drive()
        self._set_create(resource, {
            "id": "J", "name": "snap.json", "mimeType": "application/json",
            "parents": ["USERDRIVE"],
        })
        drive.user_drive.upload("snap.json", b"{}")
        assert self._media(resource).mimetype() == "application/json"

    def test_upload_mime_type_override_wins(self):
        drive, resource = _make_drive()
        self._set_create(resource, {
            "id": "P", "name": "snap.json", "mimeType": "image/png",
            "parents": ["USERDRIVE"],
        })
        drive.user_drive.upload("snap.json", b"\x89PNG", mime_type="image/png")
        # Explicit mime_type beats the .json inference.
        assert self._media(resource).mimetype() == "image/png"

    def test_upload_from_pathlib_path(self, tmp_path: pathlib.Path):
        src = tmp_path / "report.csv"
        src.write_bytes(b"a,b,c\n1,2,3")
        drive, resource = _make_drive()
        self._set_create(resource, {
            "id": "C", "name": "report.csv", "mimeType": "text/csv",
            "parents": ["USERDRIVE"],
        })
        drive.user_drive.upload("report.csv", src)

        media = self._media(resource)
        assert self._media_bytes(media) == b"a,b,c\n1,2,3"
        assert media.mimetype() == "text/csv"

    def test_upload_appends_to_loaded_listing(self):
        drive, resource = _make_drive(files_list_pages=[{"files": []}])
        drive.user_drive.list()  # load (empty) listing
        self._set_create(resource, {
            "id": "A", "name": "a.bin", "mimeType": "application/octet-stream",
            "parents": ["USERDRIVE"],
        })
        f = drive.user_drive.upload("a.bin", b"x")
        assert f in drive.user_drive.list()

    def test_duplicate_uploads_create_distinct_files(self):
        drive, resource = _make_drive()
        # Drive permits duplicate child names — each upload is a separate file,
        # which is what enables version retention.
        resource.files.return_value.create.return_value.execute.side_effect = [
            {"id": "V1", "name": "snap.json", "mimeType": "application/json",
             "parents": ["USERDRIVE"]},
            {"id": "V2", "name": "snap.json", "mimeType": "application/json",
             "parents": ["USERDRIVE"]},
        ]
        f1 = drive.user_drive.upload("snap.json", b"{}")
        f2 = drive.user_drive.upload("snap.json", b"{}")
        assert (f1.id, f2.id) == ("V1", "V2")
        assert drive.get(id="V1") is f1
        assert drive.get(id="V2") is f2
        # The shared path is now ambiguous and must be disambiguated by id.
        with pytest.raises(ValueError, match="ambiguous"):
            drive.get("~/snap.json")

    def test_download_returns_bytes(self):
        drive, resource = _make_drive(files_get_by_id={
            "F": {"id": "F", "name": "a.bin",
                  "mimeType": "application/octet-stream", "parents": ["USERDRIVE"]},
        })
        get_media = resource.files.return_value.get_media
        get_media.return_value.execute.return_value = b"payload"
        f = drive.get(id="F")
        assert f.download() == b"payload"
        assert get_media.call_args.kwargs["fileId"] == "F"
        assert get_media.call_args.kwargs["supportsAllDrives"] is True

    def test_download_workspace_item_raises(self):
        drive, _ = _make_drive(files_get_by_id={
            "S": {"id": "S", "name": "sheet", "mimeType": SHEET_MIME,
                  "parents": ["USERDRIVE"]},
        })
        f = drive.get(id="S")
        with pytest.raises(ValueError, match="Workspace item"):
            f.download()

    def test_update_content_overwrites_in_place(self):
        drive, resource = _make_drive(files_get_by_id={
            "F": {"id": "F", "name": "a.json", "mimeType": "application/json",
                  "parents": ["USERDRIVE"]},
        })
        resource.files.return_value.update.return_value.execute.return_value = {
            "id": "F", "name": "a.json", "mimeType": "application/json",
            "parents": ["USERDRIVE"],
        }
        f = drive.get(id="F")
        f.update_content(b'{"k":1}')

        kwargs = resource.files.return_value.update.call_args.kwargs
        media = kwargs["media_body"]
        assert media.getbytes(0, media.size()) == b'{"k":1}'
        assert media.mimetype() == "application/json"  # inferred from name
        assert kwargs["fileId"] == "F"
        # id / name / path are unchanged.
        assert f.id == "F"
        assert drive.get(id="F") is f

    def test_update_content_resets_loaded_flag(self):
        drive, resource = _make_drive(files_get_by_id={
            "F": {"id": "F", "name": "a.bin",
                  "mimeType": "application/octet-stream", "parents": ["USERDRIVE"]},
        })
        f = drive.get(id="F")
        # Load extended properties once (size etc.) via a fields="*" fetch.
        full = {"id": "F", "name": "a.bin", "mimeType": "application/octet-stream",
                "parents": ["USERDRIVE"], "size": "3"}
        resource.files.return_value.get.side_effect = None
        resource.files.return_value.get.return_value.execute.return_value = full
        assert f.size == 3

        resource.files.return_value.update.return_value.execute.return_value = {
            "id": "F", "name": "a.bin", "mimeType": "application/octet-stream",
            "parents": ["USERDRIVE"],
        }
        f.update_content(b"new content")
        # Re-fetch is forced after a content update — new size is observed.
        full["size"] = "11"
        assert f.size == 11

    def test_update_content_workspace_item_raises(self):
        drive, _ = _make_drive(files_get_by_id={
            "S": {"id": "S", "name": "sheet", "mimeType": SHEET_MIME,
                  "parents": ["USERDRIVE"]},
        })
        f = drive.get(id="S")
        with pytest.raises(ValueError, match="Workspace item"):
            f.update_content(b"data")


class TestBrokenShortcutColoredRepr:
    def test_broken_shortcut_with_colors_is_dimmed(self):
        drive, _ = _make_drive(
            files_list_pages=[
                {"files": [
                    {"id": "S", "name": "alias", "mimeType": SHORTCUT_MIME,
                     "parents": ["USERDRIVE"],
                     "shortcutDetails": {"targetId": "MISSING",
                                         "targetMimeType": DOC_MIME}},
                ]},
            ],
            files_get_404={"MISSING"},
        )
        list(drive.ls("~"))
        shortcut = drive.get(id="S")
        rep = shortcut.file_list_repr(use_colors=True)
        # The colored broken repr wraps with the dim escape \033[2m.
        assert "\033[2m" in rep
        assert "✘" in rep
