"""Unit tests for `OrjsonModel.deserialize` — the orjson-backed replacement
for googleapiclient's stdlib JSON deserializer."""

import json

import pytest

from gservices.json_model import OrjsonModel


@pytest.fixture
def model() -> OrjsonModel:
    return OrjsonModel()


def test_parses_bytes(model: OrjsonModel):
    assert model.deserialize(b'{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_parses_str(model: OrjsonModel):
    assert model.deserialize('{"a": 1}') == {"a": 1}


def test_empty_bytes_returns_input(model: OrjsonModel):
    # googleapiclient relies on this behavior for 204 No Content responses.
    assert model.deserialize(b"") == b""


def test_empty_str_returns_input(model: OrjsonModel):
    assert model.deserialize("") == ""


def test_matches_stdlib_for_sheet_response_shape(model: OrjsonModel):
    # A trimmed representative of what spreadsheets.get returns — make sure
    # orjson parses it identically to the stdlib it replaces.
    body = {
        "sheets": [
            {
                "properties": {"sheetId": 0, "title": "Summary"},
                "data": [{
                    "rowData": [
                        {"values": [
                            {"userEnteredValue": {"stringValue": "Name"}},
                            {"userEnteredValue": {"numberValue": 42.5}},
                            {"userEnteredValue": {"boolValue": True}},
                            {"userEnteredValue": {"formulaValue": "=A1+1"}},
                        ]},
                    ],
                }],
            }
        ],
    }
    encoded = json.dumps(body).encode("utf-8")
    assert model.deserialize(encoded) == body
