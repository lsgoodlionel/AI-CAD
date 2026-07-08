import pytest
from unittest.mock import AsyncMock

from services.model_annotations import (
    build_manual_annotation_payload,
    list_building_unit_options,
    save_drawing_annotation,
)


@pytest.fixture
def fake_db():
    class _FakeDB:
        def __init__(self):
            self.fetch_one = AsyncMock(return_value=None)

    return _FakeDB()


def test_build_manual_annotation_payload_accepts_arbitrary_legal_unit_keys():
    payload = build_manual_annotation_payload(
        project_id="project-1",
        drawing_id="drawing-1",
        building_unit_key="opera-west",
        building_unit_display_name="西区看台",
        story_key="L1",
        story_display_name="首层",
        candidate_sources=[{"source": "manual", "value": "西区看台"}],
        confidence=1.0,
        annotated_by="user-1",
    )

    assert payload["project_id"] == "project-1"
    assert payload["building_unit_key"] == "opera-west"
    assert payload["building_unit_display_name"] == "西区看台"
    assert payload["story_key"] == "L1"
    assert payload["story_display_name"] == "首层"
    assert payload["candidate_sources"][0]["source"] == "manual"


def test_list_building_unit_options_merges_detected_and_manual_candidates():
    options = list_building_unit_options(
        detected_units=[
            {
                "unit_key": "south",
                "display_name": "南区",
                "confidence": 0.74,
                "candidate_sources": [{"source": "title", "value": "南区一层平面图"}],
            }
        ],
        annotations=[
            {
                "building_unit_key": "opera-west",
                "building_unit_display_name": "西区看台",
                "confidence": 1.0,
                "candidate_sources": [{"source": "manual", "value": "西区看台"}],
            }
        ],
    )

    by_key = {item["unit_key"]: item for item in options}
    assert set(by_key) == {"opera-west", "south"}
    assert by_key["opera-west"]["display_name"] == "西区看台"
    assert by_key["opera-west"]["source"] == "manual"
    assert by_key["south"]["candidate_sources"][0]["source"] == "title"


@pytest.mark.asyncio
async def test_save_drawing_annotation_upserts_manual_unit(fake_db):
    fake_db.fetch_one.return_value = {
        "project_id": "project-1",
        "drawing_id": "drawing-1",
        "building_unit_key": "opera-west",
        "building_unit_display_name": "西区看台",
        "story_key": "L1",
        "story_display_name": "首层",
        "candidate_sources": [{"source": "manual", "value": "西区看台"}],
        "confidence": 1.0,
    }

    saved = await save_drawing_annotation(
        fake_db,
        project_id="project-1",
        drawing_id="drawing-1",
        payload={
            "building_unit_key": "opera-west",
            "building_unit_display_name": "西区看台",
            "story_key": "L1",
            "story_display_name": "首层",
            "candidate_sources": [{"source": "manual", "value": "西区看台"}],
            "confidence": 1.0,
        },
        annotated_by="user-1",
    )

    sql = fake_db.fetch_one.await_args.args[0]
    params = fake_db.fetch_one.await_args.args[1]
    assert "INSERT INTO drawing_model_annotations" in sql
    assert params["building_unit_key"] == "opera-west"
    assert saved["story_key"] == "L1"
