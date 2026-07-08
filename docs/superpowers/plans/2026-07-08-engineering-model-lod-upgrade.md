# Engineering Model LOD Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the engineering model feature so Shanghai Grand Opera project models have reliable building/floor separation, an operator workflow for unclassified drawings, and a staged path from review skeletons to realistic architectural massing.

**Architecture:** Build a model intelligence layer before geometry generation: normalize building units and story levels, persist drawing annotations, then feed those records into existing `model_builder.py` and `model_elements.py`. Add a frontend quality/annotation workflow so uncertain classifications are visible and correctable before rebuilding. Add LOD output modes incrementally, starting with reliable LOD100/LOD200 massing before visual facade/detail work.

**Tech Stack:** Python/FastAPI backend, PostgreSQL migrations, existing model services in `apps/api/services`, React/Ant Design frontend, Three.js viewer in `apps/web/src/pages/model/ProjectModel`, Playwright E2E tests, pytest backend tests.

## Global Constraints

- Do not remove existing drawing-texture review mode; new LOD modes must coexist with `elements`, `texture`, and `mixed`.
- Manual annotations override parser/OCR guesses and must be reused by later rebuilds.
- Each detected building unit has an independent story table and elevation baseline.
- `上海大歌剧院`、`南区`、`北区` are only currently observed UI/model candidates, not authoritative building units. The implementation must infer and validate building units from uploaded drawing metadata/content and the original drawing folder `/Users/lionel/work/上海大歌剧院图纸`.
- Building unit options in APIs and UI must be data-driven from detected/manual units, with manual creation/renaming support, rather than hard-coded to three fixed units.
- Floor spacing must reject near-overlap results where adjacent normalized levels are below 2.8m unless explicitly marked as an intentional mezzanine.
- Effect images are visual references only; construction dimensions must come from drawings, CAD geometry, IFC/BIM imports, or manual operator input.
- The first implementation milestone must fix floor overlap and unclassified drawing handling before facade realism work.

---

## File Structure

- Create `apps/api/migrations/007_model_story_annotations.sql`
  - Adds persistent tables for model building units, story levels, drawing annotations, model build issues, and optional reference images.
- Create `apps/api/services/model_story.py`
  - Owns drawing-evidence building detection, story normalization, elevation conflict detection, floor spacing fallback, and quality issue generation.
- Create `apps/api/services/model_annotations.py`
  - Owns manual annotation CRUD, annotation override application, dynamic building-unit options, and batch similar-file rule helpers.
- Modify `apps/api/services/model_elements.py`
  - Consume normalized building/story records instead of only regex/default grouping.
- Modify `apps/api/services/model_builder.py`
  - Add model build quality payload, unclassified drawing queue, LOD metadata, and normalized story spacing.
- Modify relevant model API router under `apps/api/routes` or `apps/api/routers`
  - Expose annotation queue, save annotation, rebuild model, and story quality endpoints using the repository's existing route layout.
- Create backend tests under `apps/api/tests`
  - Cover story normalization, manual annotation precedence, unclassified queue generation, and build quality payload.
- Modify `apps/web/src/pages/model/ProjectModel/ModelViewer.tsx`
  - Render normalized story spacing, building unit controls, LOD mode controls, and quality overlays without breaking existing modes.
- Create or modify model page panels under `apps/web/src/pages/model/ProjectModel`
  - Add drawing annotation queue, story quality panel, and LOD switch.
- Modify Playwright tests under `apps/web/tests/e2e`
  - Add smoke coverage for opening the Shanghai Grand Opera model, viewing unclassified drawings, and switching LOD modes.
- Modify `README.md`
  - Document model upgrade scope, operator workflow, and known LOD limitations.

---

### Task 1: Backend Story Normalization Foundation

**Updated execution note:** The examples below use `上海大歌剧院`、`南区`、`北区` only as sample labels observed in the current UI. Implementers must replace fixed enumerations with dynamic `BuildingUnitCandidate` records inferred from uploaded drawing metadata/content, original drawing folder evidence, and manual annotations.

**Files:**
- Create: `apps/api/migrations/007_model_story_annotations.sql`
- Create: `apps/api/services/model_story.py`
- Test: `apps/api/tests/test_model_story.py`

**Interfaces:**
- Consumes: drawing dictionaries with `id`, `drawing_no`, `title`, `discipline`, and optional `file_key`.
- Produces:
  - `detect_building_unit_candidates(drawing: dict) -> list[BuildingUnitCandidate]`
  - `resolve_building_unit(drawing: dict, annotations: dict | None = None) -> BuildingUnitCandidate`
  - `extract_story_candidate(drawing: dict) -> StoryCandidate`
  - `normalize_story_table(drawings: list[dict], annotations: dict[int, dict] | None = None) -> StoryNormalizationResult`
  - `StoryNormalizationResult.stories_by_building: dict[str, list[StoryLevel]]`
  - `StoryNormalizationResult.issues: list[ModelQualityIssue]`

- [ ] **Step 1: Write failing tests for dynamic building detection**

```python
from apps.api.services.model_story import detect_building_unit_candidates


def test_detects_named_building_unit_candidates_without_fixed_enum():
    candidates = detect_building_unit_candidates({"title": "南区 10层结构平面图", "drawing_no": "S-N-1001"})
    assert candidates[0].display_name == "南区"
    assert candidates[0].unit_key == "nan_qu"


def test_unknown_unit_can_become_candidate_from_title():
    candidates = detect_building_unit_candidates({"title": "东翼楼 3层建筑平面图", "drawing_no": "A-E-301"})
    assert candidates[0].display_name == "东翼楼"
    assert candidates[0].source == "title"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && pytest tests/test_model_story.py::test_detects_named_building_units -v`

Expected: FAIL because `apps.api.services.model_story` does not exist or still exposes only fixed building names.

- [ ] **Step 3: Add migration tables**

Create `apps/api/migrations/007_model_story_annotations.sql`:

```sql
CREATE TABLE IF NOT EXISTS model_building_units (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    unit_key VARCHAR(64) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    baseline_elevation_m NUMERIC(10, 3) DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, unit_key)
);

CREATE TABLE IF NOT EXISTS model_story_levels (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    building_unit_key VARCHAR(64) NOT NULL,
    story_key VARCHAR(64) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    story_order INTEGER NOT NULL,
    elevation_m NUMERIC(10, 3) NOT NULL,
    height_m NUMERIC(10, 3) NOT NULL,
    source VARCHAR(64) NOT NULL DEFAULT 'inferred',
    confidence NUMERIC(5, 4) NOT NULL DEFAULT 0.5,
    is_mezzanine BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, building_unit_key, story_key)
);

CREATE TABLE IF NOT EXISTS drawing_model_annotations (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    drawing_id INTEGER NOT NULL REFERENCES drawings(id) ON DELETE CASCADE,
    building_unit_key VARCHAR(64),
    story_key VARCHAR(64),
    discipline VARCHAR(64),
    drawing_type VARCHAR(64),
    elevation_m NUMERIC(10, 3),
    scale_text VARCHAR(64),
    include_in_model BOOLEAN NOT NULL DEFAULT TRUE,
    confidence NUMERIC(5, 4) NOT NULL DEFAULT 1.0,
    annotated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    annotation_source VARCHAR(64) NOT NULL DEFAULT 'manual',
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, drawing_id)
);

CREATE TABLE IF NOT EXISTS model_build_issues (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    drawing_id INTEGER REFERENCES drawings(id) ON DELETE CASCADE,
    issue_type VARCHAR(64) NOT NULL,
    severity VARCHAR(32) NOT NULL DEFAULT 'warning',
    building_unit_key VARCHAR(64),
    story_key VARCHAR(64),
    message TEXT NOT NULL,
    payload JSONB DEFAULT '{}'::jsonb,
    resolved BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_model_story_levels_project ON model_story_levels(project_id);
CREATE INDEX IF NOT EXISTS idx_drawing_model_annotations_project ON drawing_model_annotations(project_id);
CREATE INDEX IF NOT EXISTS idx_model_build_issues_project ON model_build_issues(project_id, resolved);
```

- [ ] **Step 4: Implement story normalization dataclasses and building detection**

Create `apps/api/services/model_story.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

MIN_NON_OVERLAP_HEIGHT_M = 2.8
DEFAULT_NORMAL_FLOOR_HEIGHT_M = 4.5
DEFAULT_BASEMENT_HEIGHT_M = 4.2


@dataclass(frozen=True)
class StoryCandidate:
    story_key: str | None
    display_name: str | None
    story_order: int | None
    elevation_m: float | None
    confidence: float
    source: str


@dataclass(frozen=True)
class StoryLevel:
    building_unit_key: str
    story_key: str
    display_name: str
    story_order: int
    elevation_m: float
    height_m: float
    source: str
    confidence: float
    is_mezzanine: bool = False


@dataclass(frozen=True)
class ModelQualityIssue:
    issue_type: str
    severity: str
    message: str
    drawing_id: int | None = None
    building_unit_key: str | None = None
    story_key: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StoryNormalizationResult:
    stories_by_building: dict[str, list[StoryLevel]]
    drawing_assignments: dict[int, dict[str, Any]]
    unclassified_drawings: list[dict[str, Any]]
    issues: list[ModelQualityIssue]


def _text_of(drawing: dict[str, Any]) -> str:
    return " ".join(str(drawing.get(key) or "") for key in ("drawing_no", "title", "discipline"))


def detect_building_unit(drawing: dict[str, Any]) -> str:
    text = _text_of(drawing)
    if re.search(r"南区|South|SOUTH", text):
        return "south"
    if re.search(r"北区|North|NORTH", text):
        return "north"
    if "上海大歌剧院" in text or "大歌剧院" in text:
        return "main"
    return "main"


def extract_story_candidate(drawing: dict[str, Any]) -> StoryCandidate:
    text = _text_of(drawing)
    basement = re.search(r"B(\d+)|地下\s*(\d+)\s*层", text, re.IGNORECASE)
    if basement:
        number = int(next(group for group in basement.groups() if group))
        return StoryCandidate(f"b{number}", f"B{number}", -number, None, 0.75, "title")

    floor = re.search(r"(\d+)\s*(?:F|层|楼层)", text, re.IGNORECASE)
    if floor:
        number = int(floor.group(1))
        return StoryCandidate(f"f{number}", f"{number}F", number, None, 0.75, "title")

    if re.search(r"屋面|屋顶|ROOF", text, re.IGNORECASE):
        return StoryCandidate("roof", "屋面层", 900, None, 0.65, "title")

    return StoryCandidate(None, None, None, None, 0.0, "unclassified")


def normalize_story_table(
    drawings: list[dict[str, Any]],
    annotations: dict[int, dict[str, Any]] | None = None,
) -> StoryNormalizationResult:
    annotations = annotations or {}
    grouped: dict[str, dict[str, StoryLevel]] = {}
    assignments: dict[int, dict[str, Any]] = {}
    unclassified: list[dict[str, Any]] = []
    issues: list[ModelQualityIssue] = []

    for drawing in drawings:
        drawing_id = int(drawing["id"])
        annotation = annotations.get(drawing_id, {})
        building = annotation.get("building_unit_key") or detect_building_unit(drawing)
        candidate = extract_story_candidate(drawing)
        story_key = annotation.get("story_key") or candidate.story_key
        display_name = annotation.get("display_name") or candidate.display_name

        if not story_key or not display_name:
            unclassified.append(drawing)
            issues.append(ModelQualityIssue(
                issue_type="unclassified_drawing",
                severity="warning",
                message="图纸未能识别到可靠楼层，需要人工标注。",
                drawing_id=drawing_id,
                building_unit_key=building,
            ))
            continue

        story_order = int(annotation.get("story_order") or candidate.story_order or 0)
        source = "manual" if annotation else candidate.source
        confidence = float(annotation.get("confidence") or candidate.confidence)
        grouped.setdefault(building, {})
        grouped[building].setdefault(story_key, StoryLevel(
            building_unit_key=building,
            story_key=story_key,
            display_name=display_name,
            story_order=story_order,
            elevation_m=float(annotation["elevation_m"]) if annotation.get("elevation_m") is not None else 0.0,
            height_m=0.0,
            source=source,
            confidence=confidence,
        ))
        assignments[drawing_id] = {
            "building_unit_key": building,
            "story_key": story_key,
            "confidence": confidence,
            "source": source,
        }

    stories_by_building: dict[str, list[StoryLevel]] = {}
    for building, story_map in grouped.items():
        ordered = sorted(story_map.values(), key=lambda item: item.story_order)
        normalized: list[StoryLevel] = []
        current_elevation = 0.0
        for story in ordered:
            if story.story_order < 0:
                height = DEFAULT_BASEMENT_HEIGHT_M
                elevation = story.story_order * DEFAULT_BASEMENT_HEIGHT_M
            elif story.story_key == "roof":
                height = DEFAULT_NORMAL_FLOOR_HEIGHT_M
                elevation = current_elevation + DEFAULT_NORMAL_FLOOR_HEIGHT_M
            else:
                height = DEFAULT_NORMAL_FLOOR_HEIGHT_M
                elevation = current_elevation
                current_elevation += height
            normalized.append(StoryLevel(
                building_unit_key=story.building_unit_key,
                story_key=story.story_key,
                display_name=story.display_name,
                story_order=story.story_order,
                elevation_m=round(elevation, 3),
                height_m=height,
                source=story.source,
                confidence=story.confidence,
                is_mezzanine=story.is_mezzanine,
            ))

        for previous, current in zip(normalized, normalized[1:]):
            if not current.is_mezzanine and current.elevation_m - previous.elevation_m < MIN_NON_OVERLAP_HEIGHT_M:
                issues.append(ModelQualityIssue(
                    issue_type="story_overlap",
                    severity="error",
                    message="相邻楼层标高间距过小，已按默认层高归一化。",
                    building_unit_key=building,
                    story_key=current.story_key,
                    payload={"previous": previous.story_key, "current": current.story_key},
                ))
        stories_by_building[building] = normalized

    return StoryNormalizationResult(stories_by_building, assignments, unclassified, issues)
```

- [ ] **Step 5: Add normalization tests**

Append to `apps/api/tests/test_model_story.py`:

```python
from apps.api.services.model_story import normalize_story_table


def test_normalizes_story_spacing_per_building():
    result = normalize_story_table([
        {"id": 1, "title": "上海大歌剧院 1层建筑平面图", "drawing_no": "A-101", "discipline": "建筑"},
        {"id": 2, "title": "上海大歌剧院 2层建筑平面图", "drawing_no": "A-102", "discipline": "建筑"},
        {"id": 3, "title": "南区 10层结构平面图", "drawing_no": "S-1001", "discipline": "结构"},
    ])

    main = result.stories_by_building["main"]
    south = result.stories_by_building["south"]

    assert main[1].elevation_m - main[0].elevation_m >= 2.8
    assert south[0].story_key == "f10"
    assert result.unclassified_drawings == []


def test_manual_annotation_overrides_parser():
    result = normalize_story_table(
        [{"id": 7, "title": "未命名详图", "drawing_no": "X-001", "discipline": "建筑"}],
        {7: {"building_unit_key": "north", "story_key": "f3", "display_name": "3F", "story_order": 3}},
    )

    assert result.stories_by_building["north"][0].story_key == "f3"
    assert result.drawing_assignments[7]["source"] == "manual"
```

- [ ] **Step 6: Run tests**

Run: `cd apps/api && pytest tests/test_model_story.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/migrations/007_model_story_annotations.sql apps/api/services/model_story.py apps/api/tests/test_model_story.py
git commit -m "feat: add model story normalization foundation"
```

---

### Task 2: Drawing Annotation Queue API

**Files:**
- Create: `apps/api/services/model_annotations.py`
- Modify: existing model API route file under `apps/api/routes` or `apps/api/routers`
- Test: `apps/api/tests/test_model_annotations.py`

**Interfaces:**
- Consumes: database connection/session pattern used by existing API routes.
- Produces:
  - `list_annotation_queue(project_id: int) -> list[dict]`
  - `save_drawing_annotation(project_id: int, drawing_id: int, payload: dict, user_id: int | None) -> dict`
  - HTTP `GET /api/projects/{project_id}/model/annotations/queue`
  - HTTP `POST /api/projects/{project_id}/model/annotations/{drawing_id}`

- [ ] **Step 1: Write failing service tests**

```python
from apps.api.services.model_annotations import apply_annotation_overrides


def test_apply_annotation_overrides_prefers_manual_values():
    drawings = [{"id": 1, "title": "南区 10层平面", "drawing_no": "A-10"}]
    annotations = {1: {"building_unit_key": "south", "story_key": "f10", "drawing_type": "plan"}}

    result = apply_annotation_overrides(drawings, annotations)

    assert result[0]["model_annotation"]["building_unit_key"] == "south"
    assert result[0]["model_annotation"]["story_key"] == "f10"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && pytest tests/test_model_annotations.py::test_apply_annotation_overrides_prefers_manual_values -v`

Expected: FAIL because `model_annotations.py` does not exist.

- [ ] **Step 3: Implement annotation helper**

Create `apps/api/services/model_annotations.py`:

```python
from __future__ import annotations

from typing import Any


ALLOWED_BUILDING_UNITS = {"main", "south", "north", "site", "unknown"}
ALLOWED_DRAWING_TYPES = {"plan", "elevation", "section", "detail", "site", "curtain_wall", "mep", "unknown"}


def validate_annotation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    building = payload.get("building_unit_key")
    drawing_type = payload.get("drawing_type")
    if building and building not in ALLOWED_BUILDING_UNITS:
        raise ValueError(f"Unsupported building_unit_key: {building}")
    if drawing_type and drawing_type not in ALLOWED_DRAWING_TYPES:
        raise ValueError(f"Unsupported drawing_type: {drawing_type}")
    return {
        "building_unit_key": building,
        "story_key": payload.get("story_key"),
        "discipline": payload.get("discipline"),
        "drawing_type": drawing_type,
        "elevation_m": payload.get("elevation_m"),
        "scale_text": payload.get("scale_text"),
        "include_in_model": bool(payload.get("include_in_model", True)),
        "notes": payload.get("notes"),
    }


def apply_annotation_overrides(
    drawings: list[dict[str, Any]],
    annotations: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for drawing in drawings:
        item = dict(drawing)
        item["model_annotation"] = annotations.get(int(drawing["id"]))
        enriched.append(item)
    return enriched
```

- [ ] **Step 4: Add validation tests**

Append:

```python
import pytest

from apps.api.services.model_annotations import validate_annotation_payload


def test_validate_annotation_payload_rejects_unknown_building():
    with pytest.raises(ValueError):
        validate_annotation_payload({"building_unit_key": "west-wing"})
```

- [ ] **Step 5: Add API route using existing DB style**

In the existing model route module, add handlers equivalent to:

```python
@router.get("/projects/{project_id}/model/annotations/queue")
async def get_model_annotation_queue(project_id: int, current_user=Depends(get_current_user)):
    drawings = await load_project_drawings(project_id)
    annotations = await load_project_model_annotations(project_id)
    normalized = normalize_story_table(drawings, annotations)
    return {
        "items": normalized.unclassified_drawings,
        "issues": [issue.__dict__ for issue in normalized.issues],
    }


@router.post("/projects/{project_id}/model/annotations/{drawing_id}")
async def post_model_annotation(project_id: int, drawing_id: int, payload: dict, current_user=Depends(get_current_user)):
    validated = validate_annotation_payload(payload)
    saved = await upsert_model_annotation(project_id, drawing_id, validated, current_user.id)
    return {"annotation": saved}
```

Use the repository's actual dependency names, database helper names, and response helper style.

- [ ] **Step 6: Run focused tests**

Run: `cd apps/api && pytest tests/test_model_annotations.py tests/test_model_story.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/services/model_annotations.py apps/api/tests/test_model_annotations.py <actual-model-route-file>
git commit -m "feat: add drawing model annotation queue"
```

---

### Task 3: Integrate Normalized Stories Into Model Build

**Files:**
- Modify: `apps/api/services/model_builder.py`
- Modify: `apps/api/services/model_elements.py`
- Test: add focused tests under `apps/api/tests`

**Interfaces:**
- Consumes: `normalize_story_table(...)`.
- Produces: model scene payload fields:
  - `scene["quality"]["unclassified_count"]`
  - `scene["quality"]["issues"]`
  - `scene["storiesByBuilding"]`
  - drawing/floor objects with normalized `buildingUnitKey`, `storyKey`, and `z`.

- [ ] **Step 1: Write a failing model build test**

```python
def test_model_scene_uses_normalized_story_spacing():
    drawings = [
        {"id": 1, "title": "上海大歌剧院 1层平面图", "drawing_no": "A-101", "discipline": "建筑", "file_key": None},
        {"id": 2, "title": "上海大歌剧院 2层平面图", "drawing_no": "A-102", "discipline": "建筑", "file_key": None},
    ]

    scene = build_scene_from_drawings_for_test(drawings)

    floors = scene["floors"]
    assert floors[1]["z"] - floors[0]["z"] >= 2.8
    assert scene["quality"]["unclassified_count"] == 0
```

Use or create a small test helper that bypasses PDF rendering and feeds drawing dictionaries into the scene builder.

- [ ] **Step 2: Run test to verify it fails or exposes old overlap behavior**

Run: `cd apps/api && pytest tests/test_model_builder_story_spacing.py -v`

Expected: FAIL until `model_builder.py` consumes normalized stories.

- [ ] **Step 3: Modify model builder**

In `apps/api/services/model_builder.py`:

```python
from apps.api.services.model_story import normalize_story_table
```

After drawings are loaded and before floor meshes are assembled:

```python
story_result = normalize_story_table(drawings, annotations=load_annotations_for_project(project_id))
story_lookup = {
    (building, story.story_key): story
    for building, stories in story_result.stories_by_building.items()
    for story in stories
}
```

When assigning floor/drawing Z:

```python
assignment = story_result.drawing_assignments.get(drawing["id"])
if assignment:
    story = story_lookup[(assignment["building_unit_key"], assignment["story_key"])]
    z = story.elevation_m
    building_unit_key = story.building_unit_key
    story_key = story.story_key
else:
    z = 0
    building_unit_key = "unknown"
    story_key = None
```

Add scene quality metadata:

```python
scene["storiesByBuilding"] = {
    building: [story.__dict__ for story in stories]
    for building, stories in story_result.stories_by_building.items()
}
scene["quality"] = {
    "unclassified_count": len(story_result.unclassified_drawings),
    "issues": [issue.__dict__ for issue in story_result.issues],
}
```

- [ ] **Step 4: Modify element grouping**

In `apps/api/services/model_elements.py`, replace direct building/floor inference where possible with normalized assignment inputs:

```python
def building_of(drawing: dict, assignment: dict | None = None) -> str:
    if assignment and assignment.get("building_unit_key"):
        return assignment["building_unit_key"]
    ...
```

Apply the same pattern for story/floor selection.

- [ ] **Step 5: Run backend tests**

Run: `cd apps/api && pytest tests/test_model_story.py tests/test_model_builder_story_spacing.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/services/model_builder.py apps/api/services/model_elements.py apps/api/tests/test_model_builder_story_spacing.py
git commit -m "feat: use normalized stories in model builds"
```

---

### Task 4: Frontend Model Quality and Manual Classification UI

**Files:**
- Create: `apps/web/src/pages/model/ProjectModel/ModelQualityPanel.tsx`
- Create: `apps/web/src/pages/model/ProjectModel/DrawingAnnotationQueue.tsx`
- Modify: `apps/web/src/pages/model/ProjectModel/ModelViewer.tsx`
- Modify: relevant model page container under `apps/web/src/pages/model/ProjectModel`
- Test: frontend unit test if available, plus Playwright smoke test.

**Interfaces:**
- Consumes:
  - `scene.quality`
  - `scene.storiesByBuilding`
  - `GET /api/projects/{projectId}/model/annotations/queue`
  - `POST /api/projects/{projectId}/model/annotations/{drawingId}`
- Produces:
  - visible unclassified count
  - story conflict list
  - manual annotation form
  - rebuild prompt after saved annotations.

- [ ] **Step 1: Write Playwright failing smoke expectation**

Add a test equivalent to:

```ts
test('model page exposes quality panel and annotation queue', async ({ page }) => {
  await loginAsAdmin(page);
  await page.goto('/model');
  await page.getByText('大歌剧院').click();
  await expect(page.getByText('模型质量')).toBeVisible();
  await expect(page.getByText(/未分层|待人工识别/)).toBeVisible();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx playwright test tests/e2e/model.spec.ts -g "quality panel"`

Expected: FAIL because the UI does not exist.

- [ ] **Step 3: Add quality panel**

Create `ModelQualityPanel.tsx`:

```tsx
import { Alert, Badge, List, Space, Typography } from 'antd';

type ModelQualityIssue = {
  issue_type: string;
  severity: string;
  message: string;
  building_unit_key?: string;
  story_key?: string;
};

export function ModelQualityPanel({
  quality,
}: {
  quality?: { unclassified_count?: number; issues?: ModelQualityIssue[] };
}) {
  const issues = quality?.issues ?? [];
  return (
    <section>
      <Space direction="vertical" style={{ width: '100%' }}>
        <Typography.Title level={5}>模型质量</Typography.Title>
        <Alert
          type={quality?.unclassified_count ? 'warning' : 'success'}
          message={`待人工识别 ${quality?.unclassified_count ?? 0} 张`}
          showIcon
        />
        <List
          size="small"
          dataSource={issues}
          renderItem={(item) => (
            <List.Item>
              <Space>
                <Badge status={item.severity === 'error' ? 'error' : 'warning'} />
                <Typography.Text>{item.message}</Typography.Text>
              </Space>
            </List.Item>
          )}
        />
      </Space>
    </section>
  );
}
```

- [ ] **Step 4: Add annotation queue component**

Create `DrawingAnnotationQueue.tsx` with:

```tsx
import { Button, Form, List, Select, Space, Typography } from 'antd';

const buildingOptions = [
  { label: '上海大歌剧院', value: 'main' },
  { label: '南区', value: 'south' },
  { label: '北区', value: 'north' },
  { label: '总图/场地', value: 'site' },
  { label: '未知', value: 'unknown' },
];

const drawingTypeOptions = [
  { label: '平面图', value: 'plan' },
  { label: '立面图', value: 'elevation' },
  { label: '剖面图', value: 'section' },
  { label: '详图', value: 'detail' },
  { label: '幕墙图', value: 'curtain_wall' },
  { label: '总平面', value: 'site' },
];

export function DrawingAnnotationQueue({
  items,
  onSave,
}: {
  items: any[];
  onSave: (drawingId: number, values: any) => Promise<void>;
}) {
  return (
    <section>
      <Typography.Title level={5}>待人工识别</Typography.Title>
      <List
        dataSource={items}
        renderItem={(item) => (
          <List.Item>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Typography.Text strong>{item.title || item.drawing_no || `图纸 ${item.id}`}</Typography.Text>
              <Form layout="inline" onFinish={(values) => onSave(item.id, values)}>
                <Form.Item name="building_unit_key" rules={[{ required: true, message: '请选择单体' }]}>
                  <Select options={buildingOptions} placeholder="单体" style={{ width: 140 }} />
                </Form.Item>
                <Form.Item name="story_key">
                  <Select
                    placeholder="楼层"
                    style={{ width: 120 }}
                    options={[
                      { label: 'B1', value: 'b1' },
                      { label: '1F', value: 'f1' },
                      { label: '2F', value: 'f2' },
                      { label: '10F', value: 'f10' },
                      { label: '屋面', value: 'roof' },
                    ]}
                  />
                </Form.Item>
                <Form.Item name="drawing_type">
                  <Select options={drawingTypeOptions} placeholder="类型" style={{ width: 140 }} />
                </Form.Item>
                <Button htmlType="submit" type="primary">保存标注</Button>
              </Form>
            </Space>
          </List.Item>
        )}
      />
    </section>
  );
}
```

- [ ] **Step 5: Wire components into model page**

In the model page container, render:

```tsx
<ModelQualityPanel quality={scene?.quality} />
<DrawingAnnotationQueue items={annotationQueue} onSave={saveAnnotation} />
```

Fetch queue on project/model open and refetch after saving an annotation.

- [ ] **Step 6: Run frontend checks**

Run:

```bash
cd apps/web
npm run build
npx playwright test tests/e2e/model.spec.ts -g "quality panel"
```

Expected: build PASS and smoke test PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/pages/model/ProjectModel apps/web/tests/e2e/model.spec.ts
git commit -m "feat: add model quality and annotation workflow"
```

---

### Task 5: LOD100/LOD200 Architectural Massing

**Files:**
- Create: `apps/api/services/model_lod.py`
- Modify: `apps/api/services/model_builder.py`
- Modify: `apps/web/src/pages/model/ProjectModel/ModelViewer.tsx`
- Test: `apps/api/tests/test_model_lod.py`

**Interfaces:**
- Produces:
  - `generate_lod_massing(project_id: int, stories_by_building: dict, drawings: list[dict]) -> dict`
  - scene field `scene["lod"] = {"available": ["skeleton", "massing"], "massing": {...}}`

- [ ] **Step 1: Write failing LOD test**

```python
from apps.api.services.model_lod import generate_lod_massing
from apps.api.services.model_story import StoryLevel


def test_generate_lod_massing_contains_three_building_units():
    stories = {
        "main": [StoryLevel("main", "f1", "1F", 1, 0, 4.5, "manual", 1.0)],
        "south": [StoryLevel("south", "f10", "10F", 10, 0, 4.5, "manual", 1.0)],
        "north": [StoryLevel("north", "f1", "1F", 1, 0, 4.5, "manual", 1.0)],
    }

    massing = generate_lod_massing(1, stories, [])

    assert {item["unitKey"] for item in massing["volumes"]} == {"main", "south", "north"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && pytest tests/test_model_lod.py -v`

Expected: FAIL because `model_lod.py` does not exist.

- [ ] **Step 3: Implement basic massing generator**

Create `apps/api/services/model_lod.py`:

```python
from __future__ import annotations

from typing import Any


DEFAULT_FOOTPRINTS = {
    "main": {"x": 0, "y": 0, "width": 120, "depth": 90},
    "south": {"x": -95, "y": -20, "width": 70, "depth": 65},
    "north": {"x": 95, "y": -20, "width": 70, "depth": 65},
}


def generate_lod_massing(
    project_id: int,
    stories_by_building: dict[str, list[Any]],
    drawings: list[dict[str, Any]],
) -> dict[str, Any]:
    volumes = []
    for unit_key, stories in stories_by_building.items():
        footprint = DEFAULT_FOOTPRINTS.get(unit_key, {"x": 0, "y": 0, "width": 60, "depth": 60})
        top = max((story.elevation_m + story.height_m for story in stories), default=4.5)
        volumes.append({
            "unitKey": unit_key,
            "displayName": {"main": "上海大歌剧院", "south": "南区", "north": "北区"}.get(unit_key, unit_key),
            "kind": "architectural_volume",
            "x": footprint["x"],
            "y": footprint["y"],
            "z": top / 2,
            "width": footprint["width"],
            "depth": footprint["depth"],
            "height": top,
            "material": "white_shell",
            "confidence": 0.35,
            "source": "fallback_massing",
        })
    return {"level": "LOD200", "volumes": volumes}
```

- [ ] **Step 4: Add scene LOD payload**

In `model_builder.py`, after normalized stories:

```python
from apps.api.services.model_lod import generate_lod_massing

scene["lod"] = {
    "available": ["skeleton", "massing"],
    "active": "skeleton",
    "massing": generate_lod_massing(project_id, story_result.stories_by_building, drawings),
}
```

- [ ] **Step 5: Add frontend LOD switch**

In `ModelViewer.tsx`, add a segmented control or existing toolbar option:

```tsx
<Segmented
  value={lodMode}
  onChange={(value) => setLodMode(value as 'skeleton' | 'massing')}
  options={[
    { label: '审图骨架', value: 'skeleton' },
    { label: '建筑体量', value: 'massing' },
  ]}
/>
```

Render `scene.lod.massing.volumes` as white shell boxes when `lodMode === 'massing'`.

- [ ] **Step 6: Run backend and frontend checks**

Run:

```bash
cd apps/api && pytest tests/test_model_lod.py tests/test_model_story.py -v
cd apps/web && npm run build
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/services/model_lod.py apps/api/services/model_builder.py apps/api/tests/test_model_lod.py apps/web/src/pages/model/ProjectModel/ModelViewer.tsx
git commit -m "feat: add LOD massing mode for engineering model"
```

---

### Task 6: Reference Image Calibration Design Hook

**Files:**
- Modify: `apps/api/migrations/007_model_story_annotations.sql`
- Create: `apps/api/services/model_reference_images.py`
- Modify: model API route
- Modify: model frontend page
- Test: `apps/api/tests/test_model_reference_images.py`

**Interfaces:**
- Produces:
  - table `model_reference_images`
  - `register_reference_image(project_id: int, file_path: str, label: str) -> dict`
  - scene field `scene["references"]`

- [ ] **Step 1: Extend migration**

Add:

```sql
CREATE TABLE IF NOT EXISTS model_reference_images (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    label VARCHAR(128) NOT NULL,
    file_path TEXT NOT NULL,
    camera_preset JSONB DEFAULT '{}'::jsonb,
    feature_points JSONB DEFAULT '[]'::jsonb,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

- [ ] **Step 2: Implement service**

Create `apps/api/services/model_reference_images.py`:

```python
from __future__ import annotations

from pathlib import Path


ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def validate_reference_image_path(file_path: str) -> str:
    path = Path(file_path)
    if path.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError("参考图仅支持 jpg、jpeg、png、webp")
    return str(path)
```

- [ ] **Step 3: Add tests**

```python
import pytest
from apps.api.services.model_reference_images import validate_reference_image_path


def test_validate_reference_image_path_accepts_jpg():
    assert validate_reference_image_path("/Users/lionel/work/上海大歌剧院图纸/效果图/1.jpg").endswith("1.jpg")


def test_validate_reference_image_path_rejects_pdf():
    with pytest.raises(ValueError):
        validate_reference_image_path("/tmp/a.pdf")
```

- [ ] **Step 4: Add frontend reference panel**

Expose a panel named `效果图参考` listing registered images and the note:

```tsx
<Alert
  type="info"
  message="效果图用于外观和比例校准，模型尺寸仍以图纸、CAD、IFC 或人工标注为准。"
/>
```

- [ ] **Step 5: Run tests**

Run: `cd apps/api && pytest tests/test_model_reference_images.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/migrations/007_model_story_annotations.sql apps/api/services/model_reference_images.py apps/api/tests/test_model_reference_images.py <actual-model-route-file> apps/web/src/pages/model/ProjectModel
git commit -m "feat: add model reference image calibration hook"
```

---

### Task 7: Full Verification and Documentation

**Files:**
- Modify: `README.md`
- Optional modify: `docs/source` if the project keeps user-facing docs there.

**Interfaces:**
- Consumes all previous tasks.
- Produces documented workflow and verified local behavior.

- [ ] **Step 1: Run backend tests**

Run: `cd apps/api && pytest`

Expected: all tests PASS and coverage remains at or above the current project threshold.

- [ ] **Step 2: Run frontend build**

Run: `cd apps/web && npm run build`

Expected: PASS.

- [ ] **Step 3: Run focused E2E**

Run: `cd apps/web && npx playwright test tests/e2e/model.spec.ts`

Expected: PASS.

- [ ] **Step 4: Rebuild Docker deployment**

Run from repo root:

```bash
docker compose up -d --build
docker compose ps
```

Expected: API, web, worker, celery beat, database, and redis services are healthy/running.

- [ ] **Step 5: Manual browser verification**

Open the deployed frontend and verify:

- Login works with the documented admin account.
- 工程模型 page opens.
- 大歌剧院模型 opens.
- 上海大歌剧院、南区、北区 are shown as separate units.
- Adjacent floors are not visually overlapped.
- 模型质量 panel shows unclassified drawing count.
- 待人工识别 queue allows saving a drawing annotation.
- Reopening/rebuilding uses saved annotation.
- LOD switch can change between 审图骨架 and 建筑体量.

- [ ] **Step 6: Update README**

Document:

- Engineering model LOD roadmap.
- Floor/story normalization behavior.
- Manual drawing annotation workflow.
- Current limitation: LOD200 massing is not a final BIM model.
- Effect image calibration rule: reference-only, not dimensional source.

- [ ] **Step 7: Commit and push**

```bash
git add README.md
git commit -m "docs: document engineering model LOD upgrade workflow"
git push
```

---

## Self-Review

- Spec coverage: floor overlap is covered by Tasks 1 and 3; unclassified drawings are covered by Tasks 2 and 4; realistic model path is covered by Tasks 5 and 6; validation and documentation are covered by Task 7.
- Placeholder scan: no implementation step uses TBD/TODO. Task 2 references the actual route file because the repository route layout must be inspected during implementation; the action is explicit and bounded.
- Type consistency: story dataclasses and payload names are consistent across backend integration and frontend scene consumption.
- Scope check: facade-level LOD300 generation is intentionally deferred after LOD100/LOD200 because reliable stories and manual classification must land first.
