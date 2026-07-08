from pathlib import Path

import pytest

from services.model_reference_images import (
    ReferenceImageValidationError,
    build_reference_image_record,
)


REFERENCE_ROOT = Path("/Users/lionel/work/上海大歌剧院图纸/效果图")
REFERENCE_JPG = REFERENCE_ROOT / "1.jpg"


def test_build_reference_image_record_accepts_valid_jpg_and_feature_points():
    record = build_reference_image_record(
        image_path=str(REFERENCE_JPG),
        label="south-west dusk",
        camera_preset="aerial_oblique",
        feature_points=[
            {"key": "roof_crown", "x": 0.42, "y": 0.18},
            {"key": "lobby_edge", "x": 0.51, "y": 0.63, "note": "facade rhythm only"},
        ],
        allowed_roots=[REFERENCE_ROOT],
    )

    assert record["path"] == str(REFERENCE_JPG.resolve())
    assert record["label"] == "south-west dusk"
    assert record["camera_preset"] == "aerial_oblique"
    assert record["media_type"] == "image/jpeg"
    assert record["feature_points"][0]["key"] == "roof_crown"
    assert record["feature_points"][1]["note"] == "facade rhythm only"


def test_build_reference_image_record_rejects_non_png_jpg_suffix():
    with pytest.raises(ReferenceImageValidationError, match="Unsupported image type"):
        build_reference_image_record(
            image_path="/tmp/reference.webp",
            allowed_roots=[REFERENCE_ROOT],
        )


def test_build_reference_image_record_rejects_path_outside_allowed_roots(tmp_path: Path):
    other = tmp_path / "outside.jpg"
    other.write_bytes(b"not important")

    with pytest.raises(ReferenceImageValidationError, match="outside allowed roots"):
        build_reference_image_record(
            image_path=str(other),
            allowed_roots=[REFERENCE_ROOT],
        )
