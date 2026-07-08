from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

SUPPORTED_IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


class ReferenceImageValidationError(ValueError):
    pass


def build_reference_image_record(
    *,
    image_path: str,
    label: str | None = None,
    camera_preset: str | None = None,
    feature_points: Iterable[Mapping[str, Any]] | None = None,
    allowed_roots: Iterable[str | Path] | None = None,
) -> dict[str, Any]:
    path = Path(image_path)
    media_type = SUPPORTED_IMAGE_TYPES.get(path.suffix.lower())
    if not media_type:
        raise ReferenceImageValidationError("Unsupported image type; only jpg/jpeg/png are allowed.")

    resolved_path = path.expanduser().resolve(strict=True)
    normalized_roots = _normalize_roots(allowed_roots)
    if normalized_roots and not any(_is_within_root(resolved_path, root) for root in normalized_roots):
        raise ReferenceImageValidationError("Reference image path is outside allowed roots.")

    validated_feature_points = [
        _normalize_feature_point(index, feature_point)
        for index, feature_point in enumerate(feature_points or [])
    ]

    return {
        "path": str(resolved_path),
        "file_name": resolved_path.name,
        "media_type": media_type,
        "label": (label or resolved_path.stem).strip(),
        "camera_preset": (camera_preset or "unspecified").strip(),
        "feature_points": validated_feature_points,
        "usage": "visual_calibration_only",
    }


def _normalize_roots(allowed_roots: Iterable[str | Path] | None) -> list[Path]:
    roots: list[Path] = []
    for root in allowed_roots or []:
        roots.append(Path(root).expanduser().resolve())
    return roots


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _normalize_feature_point(index: int, feature_point: Mapping[str, Any]) -> dict[str, Any]:
    key = str(feature_point.get("key") or "").strip()
    if not key:
        raise ReferenceImageValidationError(f"Feature point #{index + 1} is missing key.")

    try:
        x = float(feature_point["x"])
        y = float(feature_point["y"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReferenceImageValidationError(
            f"Feature point {key} must include numeric x/y coordinates."
        ) from exc

    if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
        raise ReferenceImageValidationError(
            f"Feature point {key} coordinates must be normalized between 0 and 1."
        )

    normalized = {"key": key, "x": round(x, 4), "y": round(y, 4)}
    note = feature_point.get("note")
    if note:
        normalized["note"] = str(note).strip()
    return normalized
