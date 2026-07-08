from services.model_lod import build_initial_lod_volumes


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
