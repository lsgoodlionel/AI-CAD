from services.model_lod import (
    ModelScopeEvidence,
    aggregate_lod_modes,
    build_initial_lod_volumes,
    evaluate_lod_capability,
)


def _complete_scope_evidence() -> ModelScopeEvidence:
    return ModelScopeEvidence(
        scope_key="unit-a",
        scope_label="Unit A",
        has_plan_boundary=True,
        has_story_order=True,
        has_scale=True,
        has_coordinates=True,
        has_registered_grid=True,
        has_dimensions=True,
        has_cross_view_match=True,
        has_stable_component_boundaries=True,
        geometry_consistent=True,
    )


def test_build_initial_lod_volumes_uses_dynamic_building_units_and_source_bounds():
    stories_by_building = {
        "tower": [
            {"story_key": "B1", "story_order": -1, "height_m": 4.8},
            {"story_key": "F1", "story_order": 1, "height_m": 5.2},
            {"story_key": "F2", "story_order": 2, "height_m": 4.2},
        ],
        "podium": [
            {"story_key": "F1", "story_order": 1, "height_m": 6.0},
        ],
    }
    building_units = [
        {
            "unit_key": "tower",
            "display_name": "Tower",
            "source_bounds": {
                "min_x": 0.0,
                "min_y": 0.0,
                "max_x": 24.0,
                "max_y": 18.0,
            },
        },
        {
            "unit_key": "podium",
            "display_name": "Podium",
            "footprint": [[30.0, 5.0], [48.0, 5.0], [48.0, 17.0], [30.0, 17.0]],
        },
    ]

    volumes = build_initial_lod_volumes(
        stories_by_building=stories_by_building,
        building_units=building_units,
    )

    by_key = {volume["unit_key"]: volume for volume in volumes}
    assert set(by_key) == {"tower", "podium"}

    tower = by_key["tower"]
    assert tower["height_m"] == 14.2
    assert tower["confidence"] == 0.95
    assert tower["geometry"]["source"] == "source_bounds"
    assert tower["geometry"]["width_m"] == 24.0
    assert tower["geometry"]["depth_m"] == 18.0

    podium = by_key["podium"]
    assert podium["height_m"] == 6.0
    assert podium["confidence"] == 0.9
    assert podium["geometry"]["source"] == "footprint"
    assert podium["geometry"]["width_m"] == 18.0
    assert podium["geometry"]["depth_m"] == 12.0


def test_build_initial_lod_volumes_falls_back_for_missing_geometry_with_low_confidence():
    volumes = build_initial_lod_volumes(
        stories_by_building={
            "unit-alpha": [
                {"story_key": "F1", "story_order": 1, "height_m": 4.5},
                {"story_key": "F2", "story_order": 2, "height_m": 4.5},
            ],
            "unit-beta": [
                {"story_key": "F1", "story_order": 1},
            ],
        },
        building_units=[
            {"unit_key": "unit-alpha", "display_name": "Unit Alpha"},
            {"unit_key": "unit-beta", "display_name": "Unit Beta"},
        ],
    )

    by_key = {volume["unit_key"]: volume for volume in volumes}
    alpha = by_key["unit-alpha"]
    beta = by_key["unit-beta"]

    assert alpha["geometry"]["source"] == "fallback"
    assert alpha["confidence"] < 0.5
    assert alpha["height_m"] == 9.0
    assert "missing source bounds or footprint" in alpha["notes"][0]

    assert beta["geometry"]["source"] == "fallback"
    assert beta["height_m"] == 4.5
    assert beta["confidence"] < alpha["confidence"]
    assert any("default story height" in note for note in beta["notes"])


def test_pdf_scope_gets_lod200_and_reports_missing_lod300_evidence():
    capability = evaluate_lod_capability(
        ModelScopeEvidence(
            scope_key="pdf-floor-stack",
            has_plan_boundary=True,
            has_story_order=True,
            has_scale=True,
        )
    )

    assert capability.level == 200
    assert capability.enabled_modes["realistic_proxy"] is True
    assert set(capability.missing_evidence) >= {
        "registered_grid",
        "dimensions",
        "cross_view_match",
        "stable_component_boundaries",
        "geometry_consistent",
    }


def test_lod300_requires_all_geometric_gates():
    capability = evaluate_lod_capability(_complete_scope_evidence())

    assert capability.level == 300
    assert capability.missing_evidence == []
    assert capability.enabled_modes["realistic_proxy"] is True


def test_reference_images_do_not_satisfy_any_geometric_gate():
    capability = evaluate_lod_capability(
        ModelScopeEvidence(
            scope_key="reference-renderings",
            reference_images=(
                {
                    "path": "/tmp/reference.jpg",
                    "usage": "visual_calibration_only",
                },
            ),
        )
    )

    assert capability.level == 100
    assert capability.enabled_modes["realistic_proxy"] is False
    assert capability.passed_gates == []
    assert "plan_boundary" in capability.missing_evidence
    assert capability.provenance["reference_images"]["count"] == 1


def test_lod_modes_keep_realistic_proxy_approximate_until_all_scopes_lod300():
    modes = aggregate_lod_modes(
        {
            "unit-a": evaluate_lod_capability(_complete_scope_evidence()),
            "unit-b": evaluate_lod_capability(
                ModelScopeEvidence(
                    scope_key="unit-b",
                    has_plan_boundary=True,
                    has_story_order=True,
                    has_coordinates=True,
                )
            ),
        }
    )

    assert modes["realistic_proxy"]["enabled"] is True
    assert modes["realistic_proxy"]["label"] == "实景近似（近似）"
    assert "LOD300" in modes["realistic_proxy"]["reason"]
