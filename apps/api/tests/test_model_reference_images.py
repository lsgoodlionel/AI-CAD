from pathlib import Path

import pytest

from services.model_reference_images import (
    ReferenceImageValidationError,
    build_reference_image_record,
)


def test_build_reference_image_record_accepts_valid_jpg_and_feature_points(tmp_path: Path):
    reference_root = tmp_path / "效果图"
    reference_root.mkdir()
    reference_jpg = reference_root / "1.jpg"
    reference_jpg.write_bytes(b"\xff\xd8\xff\xe0dummy-jpeg-bytes")

    record = build_reference_image_record(
        image_path=str(reference_jpg),
        label="south-west dusk",
        camera_preset="aerial_oblique",
        feature_points=[
            {"key": "roof_crown", "x": 0.42, "y": 0.18},
            {"key": "lobby_edge", "x": 0.51, "y": 0.63, "note": "facade rhythm only"},
        ],
        allowed_roots=[reference_root],
    )

    assert record["path"] == str(reference_jpg.resolve())
    assert record["label"] == "south-west dusk"
    assert record["camera_preset"] == "aerial_oblique"
    assert record["media_type"] == "image/jpeg"
    assert record["feature_points"][0]["key"] == "roof_crown"
    assert record["feature_points"][1]["note"] == "facade rhythm only"


def test_build_reference_image_record_rejects_non_png_jpg_suffix(tmp_path: Path):
    with pytest.raises(ReferenceImageValidationError, match="Unsupported image type"):
        build_reference_image_record(
            image_path=str(tmp_path / "reference.webp"),
            allowed_roots=[tmp_path],
        )


def test_build_reference_image_record_rejects_path_outside_allowed_roots(tmp_path: Path):
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    other = tmp_path / "outside.jpg"
    other.write_bytes(b"not important")

    with pytest.raises(ReferenceImageValidationError, match="outside allowed roots"):
        build_reference_image_record(
            image_path=str(other),
            allowed_roots=[allowed_root],
        )
