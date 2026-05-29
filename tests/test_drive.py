"""
Unit tests for `gservices.drive`. Each test wires up a `DriveService` against
a `MagicMock` `DriveResource` whose method chains are pre-seeded with canned
responses.
"""

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from gservices.drive.drive_service import DriveService
from gservices.drive.folder import Folder
from gservices.drive.path import Path

FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_MIME = "application/vnd.google-apps.document"

USER_DRIVE = {"id": "USERDRIVE", "name": "My Drive", "mimeType": FOLDER_MIME}


def _make_drive(
    files_list_pages: list[dict[str, Any]] | None = None,
    files_get_by_id: dict[str, dict[str, Any]] | None = None,
    drives_list_pages: list[dict[str, Any]] | None = None,
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

    def _files_get(*, fileId: str, **_kwargs: Any) -> MagicMock:
        execute = MagicMock()
        if fileId == "root":
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
        with pytest.raises(NotImplementedError):
            root.delete()

    def test_user_drive_delete_raises(self):
        drive, _ = _make_drive()
        ud = drive.user_drive
        with pytest.raises(NotImplementedError):
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
        with pytest.raises(NotImplementedError):
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
