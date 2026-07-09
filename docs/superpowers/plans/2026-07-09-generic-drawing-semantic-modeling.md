# Generic Drawing Semantic Modeling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fixed building grouping with an evidence-driven semantic hierarchy and generate traceable LOD200 plus conditional LOD300 models for generic drawing sets.

**Architecture:** Deterministic extractors generate typed semantic candidates and immutable evidence. A persistence service resolves candidates into a human-correctable hierarchy, while model construction consumes only confirmed assignments and computes LOD capability per building/story from geometric evidence gates.

**Tech Stack:** FastAPI, async PostgreSQL, Celery, pytest/pytest-cov, React 18, Umi Max, Ant Design, Three.js, Playwright, Docker Compose.

## Global Constraints

- Production recognition rules must not contain the project name or known Shanghai Grand Opera labels.
- Canonical node types are exactly `building_unit`, `sub_zone`, `functional_space`, and `construction_zone`.
- Automatic, manual, and legacy results remain distinguishable and auditable.
- Unmatched drawings remain unassigned; `main` is never manufactured as a confirmed building.
- LOD200 is the PDF baseline; LOD300 is enabled only per scope after all geometric gates pass.
- Reference renderings are visual calibration inputs only, never geometric truth.
- Backend coverage remains at least 80%.
- Existing V2 model scenes remain readable during migration.

---

## File Structure

### Backend

- `apps/api/migrations/016_model_semantic_graph.sql`: semantic graph, evidence, assignments, and operations schema.
- `apps/api/services/drawing_semantics.py`: typed candidate extraction and generic semantic priors.
- `apps/api/services/model_semantics.py`: persistence, hierarchy validation, conflict resolution, and operations.
- `apps/api/services/model_lod.py`: per-scope LOD evidence gates and capability records.
- `apps/api/services/model_story.py`: story normalization consuming confirmed semantic assignments.
- `apps/api/services/model_builder.py`: semantic graph and LOD capability integration.
- `apps/api/routers/project_models.py`: semantic read/mutation/rebuild-impact endpoints.
- `apps/api/tasks/model_build.py`: scoped rebuild input and progress reporting.
- `apps/api/scripts/analyze_drawing_corpus.py`: read-only generic corpus benchmark and report.

### Frontend

- `apps/web/src/services/projectModel.ts`: semantic graph and mutation API contracts.
- `apps/web/src/pages/model/ProjectModel/types.ts`: UI semantic and LOD capability types.
- `apps/web/src/pages/model/ProjectModel/modelData.ts`: V2/V3 compatibility normalization.
- `apps/web/src/pages/model/ProjectModel/SemanticTreePanel.tsx`: hierarchy and state display.
- `apps/web/src/pages/model/ProjectModel/SemanticReviewQueue.tsx`: evidence review and operations.
- `apps/web/src/pages/model/ProjectModel/ModelQualityPanel.tsx`: LOD gate and semantic conflict summaries.
- `apps/web/src/pages/model/ProjectModel/index.tsx`: orchestration and incremental refresh.

### Tests and documentation

- `apps/api/tests/test_drawing_semantics.py`
- `apps/api/tests/test_model_semantics.py`
- `apps/api/tests/test_project_models_router.py`
- `apps/api/tests/test_model_builder_story_spacing.py`
- `apps/api/tests/test_model_lod.py`
- `apps/api/tests/test_router_task_integration.py`
- `apps/web/tests/e2e/model.spec.ts`
- `README.md`

---

### Task 1: Preserve the Verified Realistic-Proxy Fix

**Files:**
- Modify: `README.md`
- Modify: `apps/web/src/pages/model/ProjectModel/index.tsx`
- Modify: `apps/web/src/pages/model/ProjectModel/modelData.ts`
- Test: `apps/web/tests/e2e/model.spec.ts`

**Interfaces:**
- Consumes: existing `LodModeOption`.
- Produces: clickable `realistic_proxy` fallback when the API does not explicitly disable it.

- [ ] **Step 1: Re-run the existing regression test**

Run:

```bash
cd apps/web
E2E_SKIP_SEED=1 E2E_BASE_URL=http://127.0.0.1:3002 npx playwright test tests/e2e/model.spec.ts --project=chromium
```

Expected: `1 passed`.

- [ ] **Step 2: Re-run the production build**

Run:

```bash
cd apps/web
npm run build
```

Expected: `Compiled successfully`.

- [ ] **Step 3: Commit only the verified proxy fix**

```bash
git add README.md \
  apps/web/src/pages/model/ProjectModel/index.tsx \
  apps/web/src/pages/model/ProjectModel/modelData.ts \
  apps/web/tests/e2e/model.spec.ts
git commit -m "fix: allow realistic model proxy preview"
```

---

### Task 2: Add the Semantic Graph Schema

**Files:**
- Create: `apps/api/migrations/016_model_semantic_graph.sql`
- Test: `apps/api/tests/test_model_semantics.py`

**Interfaces:**
- Produces: `model_semantic_nodes`, `model_semantic_evidence`,
  `model_semantic_assignments`, and `model_semantic_operations`.
- Preserves: `drawing_model_annotations` and existing V2 scenes.

- [ ] **Step 1: Write a failing schema contract test**

Add a migration-text test:

```python
from pathlib import Path


def test_semantic_graph_migration_defines_required_tables_and_node_types():
    sql = Path("migrations/016_model_semantic_graph.sql").read_text()
    for table in (
        "model_semantic_nodes",
        "model_semantic_evidence",
        "model_semantic_assignments",
        "model_semantic_operations",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    for node_type in (
        "building_unit",
        "sub_zone",
        "functional_space",
        "construction_zone",
    ):
        assert node_type in sql
```

- [ ] **Step 2: Verify the test fails**

Run:

```bash
cd apps/api
.venv/bin/pytest tests/test_model_semantics.py::test_semantic_graph_migration_defines_required_tables_and_node_types -q
```

Expected: FAIL because migration 016 does not exist.

- [ ] **Step 3: Implement migration 016**

Use check constraints and optimistic versions:

```sql
CREATE TABLE IF NOT EXISTS model_semantic_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    node_type VARCHAR(32) NOT NULL CHECK (
        node_type IN ('building_unit', 'sub_zone', 'functional_space', 'construction_zone')
    ),
    canonical_name VARCHAR(160) NOT NULL,
    normalized_key VARCHAR(160) NOT NULL,
    parent_id UUID REFERENCES model_semantic_nodes(id) ON DELETE SET NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'confirmed', 'rejected', 'merged')),
    confidence NUMERIC(5, 4) NOT NULL DEFAULT 0.5000,
    source VARCHAR(32) NOT NULL DEFAULT 'automatic'
        CHECK (source IN ('automatic', 'manual', 'legacy_inference')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_model_semantic_active_sibling
ON model_semantic_nodes(project_id, COALESCE(parent_id, project_id), normalized_key)
WHERE status IN ('candidate', 'confirmed');
```

Create evidence, assignment, and operation tables with JSONB location/state,
foreign keys, indexes, and the statuses specified in the design.

- [ ] **Step 4: Add legacy migration SQL**

Insert distinct rows from `model_building_units` as `candidate` nodes with
`source='legacy_inference'`; do not mark them confirmed:

```sql
INSERT INTO model_semantic_nodes (
    project_id, node_type, canonical_name, normalized_key,
    status, confidence, source
)
SELECT project_id, 'building_unit', display_name, unit_key,
       'candidate', confidence, 'legacy_inference'
FROM model_building_units
ON CONFLICT DO NOTHING;
```

- [ ] **Step 5: Verify migration tests pass**

Run:

```bash
cd apps/api
.venv/bin/pytest tests/test_model_semantics.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/migrations/016_model_semantic_graph.sql \
  apps/api/tests/test_model_semantics.py
git commit -m "feat: add model semantic graph schema"
```

---

### Task 3: Build Generic Semantic Candidate Extraction

**Files:**
- Create: `apps/api/services/drawing_semantics.py`
- Modify: `apps/api/services/drawing_filename_parser.py`
- Test: `apps/api/tests/test_drawing_semantics.py`
- Test: `apps/api/tests/test_filename_parser.py`

**Interfaces:**
- Produces:
  `extract_semantic_candidates(drawing: Mapping[str, Any]) -> list[SemanticCandidate]`.
- Produces:
  `parse_drawing_filename_evidence(filename: str) -> ParsedDrawingMetadata`.
- Candidate fields: `node_type`, `label`, `normalized_key`, `confidence`,
  `source`, `source_value`, `context`.

- [ ] **Step 1: Write failing positive and negative tests**

```python
def test_structure_zone_is_sub_zone_not_building():
    drawing = {
        "title": "A、B、C区上人屋面总体布置图",
        "discipline": "structure",
        "folder_path": "结构竣工图",
    }
    candidates = extract_semantic_candidates(drawing)
    assert {(item.node_type, item.label) for item in candidates} >= {
        ("sub_zone", "A区"),
        ("sub_zone", "B区"),
        ("sub_zone", "C区"),
    }
    assert not any(item.node_type == "building_unit" for item in candidates)


def test_functional_hall_and_enclosure_zone_keep_distinct_types():
    hall = extract_semantic_candidates({"title": "大歌剧厅舞台结构剖面图"})
    enclosure = extract_semantic_candidates({
        "title": "2-2区车道联通道围护体平面图",
        "folder_path": "围护图纸",
    })
    assert any(item.node_type == "functional_space" for item in hall)
    assert any(item.node_type == "construction_zone" for item in enclosure)
```

Also add generic cases for `3#楼`, `A座`, campus directional groups,
industrial plant areas, underground garage, bridge sections, and tunnel work
sections. Do not use project-name assertions.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
cd apps/api
.venv/bin/pytest tests/test_drawing_semantics.py tests/test_filename_parser.py -q
```

Expected: FAIL because the semantic extractor and structured evidence API are missing.

- [ ] **Step 3: Implement typed candidates**

```python
@dataclass(frozen=True)
class SemanticCandidate:
    node_type: str
    label: str
    normalized_key: str
    confidence: float
    source: str
    source_value: str
    context: dict[str, Any]
```

Use separate ordered rule sets:

```python
EXPLICIT_BUILDING_RE = re.compile(
    r"(?:\d+\s*#\s*楼|[A-Za-z]\d?\s*(?:栋|座|塔楼)|[^，。；]{1,20}单体)"
)
SUB_ZONE_RE = re.compile(r"(?<!\d)([A-Za-z]\d?|D\d+)\s*区")
FUNCTIONAL_SPACE_RE = re.compile(
    r"(?:大歌剧厅|中歌剧厅|小歌剧厅|观众厅|舞台|台塔|台仓|机房|厂房)"
)
CONSTRUCTION_CONTEXT_RE = re.compile(
    r"(?:围护|基坑|施工|工区|标段|联通道|连通道)"
)
```

Rules may contain generic domain vocabulary but no project names. Directional
labels are emitted as candidates with context-dependent confidence, not
confirmed buildings.

- [ ] **Step 4: Add structured filename evidence**

Retain `parse_drawing_filename()` compatibility and add:

```python
@dataclass(frozen=True)
class ParsedField:
    value: str
    confidence: float
    span: tuple[int, int] | None
    source: str = "filename"


@dataclass(frozen=True)
class ParsedDrawingMetadata:
    drawing_no: ParsedField
    discipline: ParsedField
    title: ParsedField
    version: ParsedField
```

- [ ] **Step 5: Verify focused tests pass**

Run:

```bash
cd apps/api
.venv/bin/pytest tests/test_drawing_semantics.py tests/test_filename_parser.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/services/drawing_semantics.py \
  apps/api/services/drawing_filename_parser.py \
  apps/api/tests/test_drawing_semantics.py \
  apps/api/tests/test_filename_parser.py
git commit -m "feat: extract generic drawing semantics"
```

---

### Task 4: Persist, Resolve, and Correct the Semantic Hierarchy

**Files:**
- Create: `apps/api/services/model_semantics.py`
- Modify: `apps/api/services/model_annotations.py`
- Test: `apps/api/tests/test_model_semantics.py`

**Interfaces:**
- Produces:
  `build_semantic_graph(db, project_id, drawings) -> SemanticGraph`.
- Produces:
  `apply_semantic_operation(db, project_id, actor_id, operation, expected_version)`.
- Produces:
  `load_confirmed_assignments(db, project_id) -> dict[str, SemanticAssignment]`.

- [ ] **Step 1: Write failing resolver tests**

Cover:

```python
def test_resolver_requires_independent_evidence_before_promoting_directional_unit():
    graph = resolve_candidates([
        candidate("building_unit", "南区", "filename", 0.62),
        candidate("sub_zone", "南区", "ocr", 0.48),
    ])
    assert graph.nodes[0].status == "candidate"


def test_manual_merge_wins_and_is_audited():
    result = apply_operation_to_graph(
        graph_with_nodes("A区", "A 区"),
        {"operation_type": "merge", "target_ids": ["a", "b"], "name": "A区"},
    )
    assert len(result.active_nodes) == 1
    assert result.operations[-1].operation_type == "merge"
```

Add cycle-rejection, duplicate-sibling, optimistic-version conflict, confirmed
building uniqueness, and unmatched drawing tests.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
cd apps/api
.venv/bin/pytest tests/test_model_semantics.py -q
```

Expected: FAIL because the resolver does not exist.

- [ ] **Step 3: Implement graph resolution**

Aggregate by normalized label and type. Promote only when:

```python
AUTO_CONFIRM_THRESHOLD = 0.88
MIN_INDEPENDENT_EVIDENCE = 2

is_confirmable = (
    aggregate.confidence >= AUTO_CONFIRM_THRESHOLD
    and len(aggregate.independent_evidence_types) >= MIN_INDEPENDENT_EVIDENCE
    and not aggregate.conflicts
)
```

Construction context must cap building confidence below the automatic threshold.
Store conflicts instead of choosing a type silently.

- [ ] **Step 4: Implement transactional operations**

Validate versions and hierarchy before mutation:

```python
async with db.transaction():
    node = await lock_node(db, project_id, node_id)
    if node["version"] != expected_version:
        raise SemanticVersionConflict(node)
    before = dict(node)
    after = apply_validated_operation(node, operation)
    await persist_node_change(db, after)
    await append_operation(db, actor_id, before, after, operation)
```

Manual operations use `source='manual'`, `status='confirmed'`, and cannot be
overwritten by later automatic extraction.

- [ ] **Step 5: Migrate annotation compatibility**

Convert existing `drawing_model_annotations` into confirmed assignments on
read. New writes go through semantic operations while retaining the legacy row
until V2 compatibility is removed.

- [ ] **Step 6: Verify service tests pass**

Run:

```bash
cd apps/api
.venv/bin/pytest tests/test_model_semantics.py tests/test_model_annotations.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/services/model_semantics.py \
  apps/api/services/model_annotations.py \
  apps/api/tests/test_model_semantics.py \
  apps/api/tests/test_model_annotations.py
git commit -m "feat: resolve and correct model semantics"
```

---

### Task 5: Expose Semantic Graph and Operations APIs

**Files:**
- Modify: `apps/api/routers/project_models.py`
- Test: `apps/api/tests/test_project_models_router.py`

**Interfaces:**
- Produces:
  `GET /api/v1/projects/{project_id}/model/semantics`.
- Produces:
  `POST /api/v1/projects/{project_id}/model/semantic-operations`.
- Produces:
  `GET /api/v1/projects/{project_id}/model/rebuild-impact`.

- [ ] **Step 1: Write failing router tests**

```python
def test_get_semantics_returns_tree_evidence_conflicts_and_unassigned(client):
    response = client.get(f"/api/v1/projects/{PROJECT_ID}/model/semantics")
    assert response.status_code == 200
    assert set(response.json()) >= {
        "nodes", "evidence", "conflicts", "unassigned_drawings", "version"
    }


def test_semantic_operation_returns_409_for_stale_version(client):
    response = client.post(
        f"/api/v1/projects/{PROJECT_ID}/model/semantic-operations",
        json={
            "operation_type": "rename",
            "target_ids": [NODE_ID],
            "canonical_name": "新名称",
            "expected_version": 1,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SEMANTIC_VERSION_CONFLICT"
```

Add tests for hierarchy `422`, audit actor, merge, split, reparent, and rebuild
impact.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
cd apps/api
.venv/bin/pytest tests/test_project_models_router.py -q
```

Expected: FAIL with missing endpoints.

- [ ] **Step 3: Implement read API**

Return stable JSON:

```python
{
    "nodes": [...],
    "evidence": [...],
    "conflicts": [...],
    "unassigned_drawings": [...],
    "version": graph.version,
}
```

- [ ] **Step 4: Implement operation and impact APIs**

Map service exceptions:

```python
except SemanticVersionConflict as exc:
    raise HTTPException(
        409,
        {"code": "SEMANTIC_VERSION_CONFLICT", "latest": exc.latest},
    )
except SemanticHierarchyError as exc:
    raise HTTPException(422, {"code": "INVALID_SEMANTIC_HIERARCHY", "message": str(exc)})
```

Return affected node, story, drawing, and asset identifiers from rebuild impact.

- [ ] **Step 5: Verify router tests pass**

Run:

```bash
cd apps/api
.venv/bin/pytest tests/test_project_models_router.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/routers/project_models.py \
  apps/api/tests/test_project_models_router.py
git commit -m "feat: expose model semantic operations"
```

---

### Task 6: Integrate Confirmed Semantics into Story and Model Building

**Files:**
- Modify: `apps/api/services/model_story.py`
- Modify: `apps/api/services/model_elements.py`
- Modify: `apps/api/services/model_builder.py`
- Modify: `apps/api/tasks/model_build.py`
- Test: `apps/api/tests/test_model_story.py`
- Test: `apps/api/tests/test_model_builder_story_spacing.py`
- Test: `apps/api/tests/test_model_builder_v2.py`
- Test: `apps/api/tests/test_router_task_integration.py`

**Interfaces:**
- Consumes: confirmed `SemanticAssignment` records.
- Produces: schema V3 scene fields `semantic_tree`, `unassigned_drawings`,
  `semantic_version`, and provenance on buildings/elements.
- Preserves: V2 `buildings`, `floors`, `markers`, and `stats`.

- [ ] **Step 1: Replace fixed-group tests with hierarchy tests**

```python
def test_unconfirmed_candidates_do_not_create_buildings():
    result = normalize_story_table(
        drawings=[drawing("d1", "A区屋面布置图")],
        annotations={},
        semantic_assignments={},
    )
    assert result.building_units == []
    assert result.unclassified_drawings[0]["drawing_id"] == "d1"


def test_confirmed_building_and_sub_zone_remain_separate():
    assignments = {
        "d1": {
            "building_unit": {"key": "building-a", "name": "A座"},
            "sub_zone": {"key": "zone-d1", "name": "D1区"},
        }
    }
    result = normalize_story_table([drawing("d1", "D1区屋面")], {}, assignments)
    assert result.drawing_assignments["d1"]["building_unit_key"] == "building-a"
    assert result.drawing_assignments["d1"]["sub_zone_key"] == "zone-d1"
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
cd apps/api
.venv/bin/pytest \
  tests/test_model_story.py \
  tests/test_model_builder_story_spacing.py \
  tests/test_model_builder_v2.py \
  tests/test_router_task_integration.py -q
```

Expected: FAIL because confirmed semantic assignments are not consumed.

- [ ] **Step 3: Remove automatic `main` assignment**

Change `detect_building_unit()` fallback to an explicit unmatched result:

```python
return BuildingUnitMatch(
    unit_key="",
    display_name="",
    confidence=0.0,
    source="unassigned",
    candidate_sources=[],
)
```

Keep legacy display compatibility in the frontend normalizer, not in semantic
truth.

- [ ] **Step 4: Build only confirmed hierarchy scopes**

Load confirmed assignments before normalization:

```python
semantic_graph = await model_semantics.build_semantic_graph(db, project_id, drawings)
confirmed = semantic_graph.confirmed_assignments_by_drawing()
normalization = model_story.normalize_story_table(
    drawings,
    annotation_overrides,
    semantic_assignments=confirmed,
)
```

Add semantic keys and source evidence to each building, floor, and recognized
element.

- [ ] **Step 5: Add scoped task rebuild**

Extend task input:

```python
def build_project_model(
    project_id: str,
    affected_node_ids: list[str] | None = None,
    affected_story_keys: list[str] | None = None,
) -> None:
```

Full rebuild remains the default. Scoped rebuild preserves the last ready scene
until replacement assets succeed.

- [ ] **Step 6: Verify focused integration tests pass**

Run:

```bash
cd apps/api
.venv/bin/pytest \
  tests/test_model_story.py \
  tests/test_model_builder_story_spacing.py \
  tests/test_model_builder_v2.py \
  tests/test_router_task_integration.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/services/model_story.py \
  apps/api/services/model_elements.py \
  apps/api/services/model_builder.py \
  apps/api/tasks/model_build.py \
  apps/api/tests/test_model_story.py \
  apps/api/tests/test_model_builder_story_spacing.py \
  apps/api/tests/test_model_builder_v2.py \
  apps/api/tests/test_router_task_integration.py
git commit -m "feat: build models from confirmed semantics"
```

---

### Task 7: Implement Per-Scope LOD200 and LOD300 Gates

**Files:**
- Modify: `apps/api/services/model_lod.py`
- Modify: `apps/api/services/model_builder.py`
- Test: `apps/api/tests/test_model_lod.py`

**Interfaces:**
- Produces:
  `evaluate_lod_capability(scope: ModelScopeEvidence) -> LodCapability`.
- `LodCapability` fields: `level`, `enabled_modes`, `passed_gates`,
  `missing_evidence`, `confidence`, `provenance`.

- [ ] **Step 1: Write failing gate tests**

```python
def test_pdf_scope_gets_lod200_and_reports_missing_lod300_evidence():
    capability = evaluate_lod_capability(ModelScopeEvidence(
        has_plan_boundary=True,
        has_story_order=True,
        has_scale=True,
        has_registered_grid=False,
        has_dimensions=False,
        has_cross_view_match=False,
        geometry_consistent=True,
    ))
    assert capability.level == 200
    assert capability.enabled_modes["realistic_proxy"] is True
    assert set(capability.missing_evidence) >= {
        "registered_grid", "dimensions", "cross_view_match"
    }


def test_lod300_requires_all_geometric_gates():
    capability = evaluate_lod_capability(complete_scope_evidence())
    assert capability.level == 300
    assert capability.missing_evidence == []
```

Add a test proving reference images do not satisfy any geometric gate.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
cd apps/api
.venv/bin/pytest tests/test_model_lod.py -q
```

Expected: FAIL because capability evaluation is missing.

- [ ] **Step 3: Implement explicit gates**

```python
LOD200_GATES = ("plan_boundary", "story_order", "scale_or_coordinates")
LOD300_GATES = (
    "scale",
    "registered_grid",
    "dimensions",
    "cross_view_match",
    "stable_component_boundaries",
    "geometry_consistent",
)
```

No fallback dimension may satisfy `scale`, `dimensions`, or
`stable_component_boundaries`.

- [ ] **Step 4: Emit scene capabilities**

Add:

```python
scene["lod_capabilities"] = {
    scope.scope_key: evaluate_lod_capability(scope).as_dict()
    for scope in model_scopes
}
scene["lod_modes"] = aggregate_lod_modes(scene["lod_capabilities"])
```

`realistic_proxy` remains available at LOD200 and is labeled approximate.
LOD300 controls detailed geometry, not whether proxy mode can be clicked.

- [ ] **Step 5: Verify tests pass**

Run:

```bash
cd apps/api
.venv/bin/pytest tests/test_model_lod.py tests/test_model_builder_story_spacing.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/services/model_lod.py \
  apps/api/services/model_builder.py \
  apps/api/tests/test_model_lod.py \
  apps/api/tests/test_model_builder_story_spacing.py
git commit -m "feat: gate lod300 by geometric evidence"
```

---

### Task 8: Add Semantic Tree and Review Workflow to the Frontend

**Files:**
- Modify: `apps/web/src/services/projectModel.ts`
- Modify: `apps/web/src/pages/model/ProjectModel/types.ts`
- Modify: `apps/web/src/pages/model/ProjectModel/modelData.ts`
- Create: `apps/web/src/pages/model/ProjectModel/SemanticTreePanel.tsx`
- Create: `apps/web/src/pages/model/ProjectModel/SemanticReviewQueue.tsx`
- Modify: `apps/web/src/pages/model/ProjectModel/ModelQualityPanel.tsx`
- Modify: `apps/web/src/pages/model/ProjectModel/index.tsx`
- Test: `apps/web/tests/e2e/model.spec.ts`

**Interfaces:**
- Consumes: semantic graph, operation API, rebuild impact, and LOD capabilities.
- Produces: confirm/reject/rename/merge/split/reparent UI and evidence display.

- [ ] **Step 1: Extend the mocked E2E API and write failing interaction tests**

Mock:

```typescript
semantic_tree: {
  version: 7,
  nodes: [
    { id: 'b1', node_type: 'building_unit', canonical_name: 'A座', status: 'confirmed', version: 3 },
    { id: 'z1', node_type: 'sub_zone', canonical_name: 'D1区', parent_id: 'b1', status: 'candidate', version: 1 },
    { id: 'f1', node_type: 'functional_space', canonical_name: '观众厅', parent_id: 'b1', status: 'candidate', version: 1 },
    { id: 'c1', node_type: 'construction_zone', canonical_name: '2-1区', status: 'candidate', version: 1 },
  ],
},
lod_capabilities: {
  b1: { level: 200, missing_evidence: ['registered_grid', 'dimensions'] },
},
```

Test that:

- nodes are grouped by type;
- `D1区` is not displayed as a building;
- evidence opens for a candidate;
- confirm and reparent send the expected version;
- stale version displays a refresh action;
- LOD200 missing evidence is visible;
- realistic proxy remains clickable.

- [ ] **Step 2: Verify E2E fails**

Run:

```bash
cd apps/web
E2E_SKIP_SEED=1 E2E_BASE_URL=http://127.0.0.1:3002 \
  npx playwright test tests/e2e/model.spec.ts --project=chromium
```

Expected: FAIL because semantic controls are absent.

- [ ] **Step 3: Add API and UI types**

```typescript
export type SemanticNodeType =
  | 'building_unit'
  | 'sub_zone'
  | 'functional_space'
  | 'construction_zone'

export interface SemanticNode {
  id: string
  node_type: SemanticNodeType
  canonical_name: string
  normalized_key: string
  parent_id?: string | null
  status: 'candidate' | 'confirmed' | 'rejected' | 'merged'
  confidence: number
  source: 'automatic' | 'manual' | 'legacy_inference'
  version: number
}
```

Add service calls for graph read, operations, and rebuild impact.

- [ ] **Step 4: Build `SemanticTreePanel`**

Use Ant Design `Tree`, `Tag`, icon buttons, and tooltips. Node icons and colors
must distinguish semantic type and status without relying on color alone.
Expose selection through `onSelectNode(node)`.

- [ ] **Step 5: Build `SemanticReviewQueue`**

Use a flat review list with evidence drawer and explicit operation dialogs.
Merge and reparent selectors only show valid targets. Display the affected
model scope before submission.

- [ ] **Step 6: Integrate quality and LOD state**

Show:

- pending candidate and conflict counts;
- unassigned drawings;
- per-selected-scope LOD level;
- passed gates and missing evidence;
- degradation/fallback reasons.

- [ ] **Step 7: Verify frontend**

Run:

```bash
cd apps/web
npm run build
E2E_SKIP_SEED=1 E2E_BASE_URL=http://127.0.0.1:3002 \
  npx playwright test tests/e2e/model.spec.ts --project=chromium
```

Expected: build succeeds and model E2E passes.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/services/projectModel.ts \
  apps/web/src/pages/model/ProjectModel/types.ts \
  apps/web/src/pages/model/ProjectModel/modelData.ts \
  apps/web/src/pages/model/ProjectModel/SemanticTreePanel.tsx \
  apps/web/src/pages/model/ProjectModel/SemanticReviewQueue.tsx \
  apps/web/src/pages/model/ProjectModel/ModelQualityPanel.tsx \
  apps/web/src/pages/model/ProjectModel/index.tsx \
  apps/web/tests/e2e/model.spec.ts
git commit -m "feat: add semantic model correction workflow"
```

---

### Task 9: Add Generic Corpus and Shanghai Regression Analysis

**Files:**
- Create: `apps/api/scripts/analyze_drawing_corpus.py`
- Create: `apps/api/tests/fixtures/semantic_corpus.json`
- Test: `apps/api/tests/test_drawing_semantics.py`
- Modify: `README.md`

**Interfaces:**
- Produces a JSON report with counts, candidate types, conflicts, unassigned
  drawings, LOD gates, runtime, and peak memory.
- Reads files and metadata only; it never writes to source drawing directories.

- [ ] **Step 1: Add generic fixture cases**

Create fixtures for:

```json
[
  {"title": "1#楼三层建筑平面图", "expected": [["building_unit", "1#楼"]]},
  {"title": "A区屋面结构布置图", "expected": [["sub_zone", "A区"]]},
  {"title": "主厂房汽机间结构图", "expected": [["functional_space", "主厂房"]]},
  {"title": "隧道2工区开挖支护图", "expected": [["construction_zone", "2工区"]]},
  {"title": "地下车库B2层平面图", "expected_floor": "B2"}
]
```

- [ ] **Step 2: Write failing corpus contract test**

```python
def test_generic_semantic_corpus_matches_expected_types():
    cases = json.loads(FIXTURE.read_text())
    for case in cases:
        actual = {
            (item.node_type, item.label)
            for item in extract_semantic_candidates(case)
        }
        assert set(map(tuple, case.get("expected", []))) <= actual
```

- [ ] **Step 3: Verify test fails, then adjust only generic rules**

Run:

```bash
cd apps/api
.venv/bin/pytest tests/test_drawing_semantics.py -q
```

Expected before adjustment: at least one fixture fails. Modify generic patterns
or context weights; do not add project names.

- [ ] **Step 4: Implement read-only corpus analyzer**

CLI:

```bash
.venv/bin/python scripts/analyze_drawing_corpus.py \
  --root "/Users/lionel/work/上海大歌剧院图纸" \
  --output /tmp/shanghai-opera-semantic-report.json
```

Use `Path.rglob("*.pdf")`, filename/path extraction, optional bounded PDF text
sampling, `time.perf_counter()`, and `tracemalloc`. The JSON report must include:

```python
{
    "files": total,
    "by_discipline": {...},
    "candidate_nodes": {...},
    "conflicts": [...],
    "unassigned": [...],
    "lod_gate_summary": {...},
    "runtime_seconds": elapsed,
    "peak_memory_mb": peak,
    "extractor_version": EXTRACTOR_VERSION,
}
```

- [ ] **Step 5: Run generic and real-corpus validation**

Run:

```bash
cd apps/api
.venv/bin/pytest tests/test_drawing_semantics.py -q
.venv/bin/python scripts/analyze_drawing_corpus.py \
  --root "/Users/lionel/work/上海大歌剧院图纸" \
  --output /tmp/shanghai-opera-semantic-report.json
```

Expected: tests pass; report processes all visible PDFs without modifying the
source directory.

- [ ] **Step 6: Document results and limitations**

README must state that the corpus validates generic behavior, list candidate
and conflict counts from the report, and explicitly state that PDF-only input
does not imply construction-grade BIM accuracy.

- [ ] **Step 7: Commit**

```bash
git add apps/api/scripts/analyze_drawing_corpus.py \
  apps/api/tests/fixtures/semantic_corpus.json \
  apps/api/tests/test_drawing_semantics.py \
  README.md
git commit -m "test: add generic drawing corpus regression"
```

---

### Task 10: Full Migration, Docker, Coverage, and Browser Verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Verifies the complete semantic-to-model workflow in production-style Docker.
- Any verification failure returns to the task that owns the failing file;
  Task 10 does not introduce new production behavior.

- [ ] **Step 1: Run all backend tests with coverage**

```bash
cd apps/api
.venv/bin/pytest --cov=. --cov-report=term-missing --cov-fail-under=80
```

Expected: all tests pass and coverage is at least 80%.

- [ ] **Step 2: Run the frontend production build**

```bash
cd apps/web
npm run build
```

Expected: compilation succeeds.

- [ ] **Step 3: Rebuild Docker services**

```bash
docker compose -p cad \
  -f infra/docker-compose.yml \
  -f infra/docker-compose.alt-ports.yml \
  --profile app up -d --build
```

Expected: images build and containers start.

- [ ] **Step 4: Apply migration 016**

```bash
docker exec -i cad_postgres \
  psql -U cad_user -d cad_db \
  < apps/api/migrations/016_model_semantic_graph.sql
```

Expected: migration completes without errors and remains idempotent on a second
execution.

- [ ] **Step 5: Run E2E suites**

```bash
cd apps/web
E2E_SKIP_SEED=1 E2E_BASE_URL=http://127.0.0.1:3002 \
  npx playwright test tests/e2e/model.spec.ts tests/e2e/drawings.spec.ts \
  --project=chromium
```

Expected: all selected tests pass.

- [ ] **Step 6: Perform browser workflow verification**

At `http://127.0.0.1:3002` verify:

1. Log in with the configured local admin account.
2. Open a project model.
3. Confirm semantic nodes are grouped by type.
4. Open evidence for a candidate.
5. Rename and confirm a test candidate.
6. Reparent a sub-zone under a confirmed building.
7. Verify incremental rebuild progress.
8. Switch review skeleton, massing, and realistic proxy modes.
9. Confirm LOD200/LOD300 gate explanations match the selected scope.
10. Confirm no console errors or failed API requests.

- [ ] **Step 7: Verify service health and repository state**

```bash
docker compose -p cad \
  -f infra/docker-compose.yml \
  -f infra/docker-compose.alt-ports.yml \
  --profile app ps
git diff --check
git status --short
```

Expected: application containers are healthy, no whitespace errors, and only
intentional documentation/report changes remain.

- [ ] **Step 8: Update README and commit final verification**

Document schema V3, migration command, semantic operations, LOD gates, test
counts, coverage, Docker URLs, and remaining PDF limitations.

```bash
git add README.md
git commit -m "docs: document semantic modeling upgrade"
```
