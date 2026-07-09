# Generic Drawing Semantic Modeling Design

**Date:** 2026-07-09

## Objective

Upgrade the platform from project-specific building grouping to a generic,
evidence-driven drawing interpretation and modeling pipeline. The Shanghai
Grand Opera drawing set is a validation corpus, not a source of hard-coded
product rules.

The system must:

- classify drawing semantics without assuming a fixed number of buildings;
- separate buildings, sub-zones, functional spaces, and construction zones;
- preserve evidence, confidence, conflicts, and human corrections;
- generate LOD200 for supported PDF projects;
- generate LOD300 only when geometric evidence is sufficient;
- degrade explicitly when evidence is incomplete rather than invent geometry.

## Design Principles

1. Project names and known Shanghai Grand Opera labels must not appear in
   production recognition rules.
2. A detected label is a candidate until evidence or a human operation confirms
   its semantic role.
3. `main` is a display fallback, not an inferred building fact.
4. Every semantic assignment and generated element must be traceable to source
   evidence.
5. Human decisions override automatic inference and remain auditable and
   reversible.
6. Rendering quality and semantic certainty are separate dimensions.

## Semantic Hierarchy

The canonical node types are:

| Type | Meaning | Typical examples |
| --- | --- | --- |
| `building_unit` | Independently modeled building or civil asset | tower, hall building, station |
| `sub_zone` | Design or coordination subdivision within a building/project | A zone, D1 roof zone |
| `functional_space` | Named use or operational space | auditorium, stage tower, plant room |
| `construction_zone` | Temporary or construction-oriented partition | excavation zone, enclosure zone |

Nodes form a directed hierarchy. A node may have one active parent and multiple
historical parent relationships through operations. The hierarchy supports a
project root with either one building or multiple building units.

Labels such as `A区`, `1区`, or `大歌剧厅` never imply a node type by themselves.
Their role is determined from context, document family, discipline, title-page
evidence, and cross-document consistency.

## Persistence Model

### `model_semantic_nodes`

- `id`, `project_id`
- `node_type`
- `canonical_name`, `normalized_key`
- `parent_id`
- `status`: `candidate`, `confirmed`, `rejected`, `merged`
- `confidence`
- `source`: `automatic`, `manual`, `legacy_inference`
- timestamps and version

The project and normalized key are indexed. Active sibling names are unique
after normalization.

### `model_semantic_evidence`

- `id`, `project_id`, `node_id`
- `drawing_id`
- `evidence_type`: `filename`, `folder`, `drawing_no`, `title_block`,
  `catalog`, `ocr`, `cross_discipline`, `manual`
- `source_value`
- `location` as JSON for page and bounding-box metadata
- `weight`, `extractor_version`

Evidence records are immutable. Superseded extraction runs create new records.

### `model_semantic_assignments`

- `id`, `project_id`, `drawing_id`
- `node_id`, `story_key`, `drawing_type`
- `status`: `candidate`, `confirmed`, `rejected`
- `confidence`
- `assignment_source`
- timestamps and version

A drawing may be assigned to several node types but only one confirmed
`building_unit` and one confirmed story at a time.

### `model_semantic_operations`

- `id`, `project_id`, `actor_id`
- `operation_type`: `confirm`, `reject`, `rename`, `merge`, `split`,
  `reparent`, `assign`, `unassign`
- `target_ids`, `before_state`, `after_state`
- timestamp

Operations are append-only and provide audit and rollback inputs.

## Extraction and Classification

### Metadata extraction

Upload processing retains:

- original relative path and batch identifier;
- parsed drawing number, revision, title, and discipline;
- document family and drawing type;
- PDF page count and detected title-block/catalog pages.

The filename parser remains deterministic and gains a structured result with
field confidence and evidence spans.

### Content extraction

PDF processing extracts:

- title-block text and drawing number;
- catalog entries and drawing relationships;
- floor and elevation expressions;
- scale and dimension evidence;
- grid labels and coordinates;
- section/elevation references;
- candidate semantic labels with page coordinates.

OCR is a weak signal unless corroborated by title-block, catalog, or repeated
cross-document evidence.

### Candidate generation

Independent classifiers emit candidates for each semantic node type. Candidate
generation uses generic lexical patterns and document context:

- explicit building markers (`栋`, `座`, `塔楼`, `#楼`, `单体`);
- directional names only when used consistently as top-level design groups;
- letter/number zones as `sub_zone` by default;
- hall, room, tower, warehouse, and auditorium terms as
  `functional_space` by default;
- excavation, enclosure, work-section, and connection-area terms as
  `construction_zone` by default.

Defaults are priors, not final assignments.

### Evidence aggregation

Candidates are clustered by normalized label, folder family, drawing-number
sequence, catalog membership, shared grids, and cross-discipline repetition.
The resolver:

- promotes candidates when independent evidence agrees;
- records conflicts when the same label has competing types or parents;
- prevents a construction zone from becoming a building solely through label
  frequency;
- sends ambiguous candidates to manual review;
- leaves unmatched drawings unassigned instead of manufacturing a `main`
  building assignment.

Large-language-model inference may propose candidates for unresolved cases but
cannot directly confirm nodes or geometry.

## Human Correction Workflow

The project model page provides:

- a semantic tree grouped by node type and confirmation state;
- a candidate review queue;
- evidence inspection with source drawing and page location;
- confirm, reject, rename, merge, split, reparent, assign, and unassign
  operations;
- conflict and downstream rebuild impact previews.

Confirmed manual operations have priority over automatic runs. A later
extraction may add evidence but cannot silently overwrite a manual decision.
Only affected nodes, stories, and model assets are rebuilt after an operation.

## Model Construction

### LOD200 baseline

LOD200 generation requires:

- at least one confirmed project/building scope;
- ordered stories or an explicit unassigned-story state;
- usable planar boundaries or a documented massing fallback;
- scale or coordinate normalization confidence above the configured threshold.

It generates:

- building and story volumes;
- floor slabs and primary walls;
- major columns and beams where plan evidence exists;
- primary MEP paths where system/plan evidence can be registered;
- semantic grouping and source links.

Fallback dimensions are visibly marked as inferred and are excluded from
measurement and quantity workflows.

### Conditional LOD300

LOD300 is enabled per building/story, not globally. It requires:

- reliable scale or dimensions;
- registered grid coordinates;
- matched plan, elevation, and/or section references;
- stable component boundaries;
- geometric consistency checks within tolerance.

Eligible scopes may add:

- openings and refined wall/slab geometry;
- facade and curtain-wall subdivisions;
- detailed structural members;
- registered equipment and service routes;
- material zones when supported by drawings.

If any gate fails, the scope remains LOD200 and reports the missing evidence.
Reference renderings are visual calibration inputs only and never establish
dimensions or component facts.

### Element provenance

Every generated element stores:

- semantic node and story;
- source drawing/page references;
- extractor and algorithm version;
- confidence and LOD;
- fallback or degradation reason;
- geometry validation results.

## API Contracts

Project model responses expose:

- semantic tree and candidate counts;
- conflicts and evidence summaries;
- per-node/per-story LOD capability;
- unassigned drawings;
- rebuild impact and progress.

Mutation endpoints use optimistic version checks. Conflicting edits return
`409` with the latest node state. Invalid hierarchy changes return structured
`422` errors. Rebuild failures preserve the last usable model and expose the
failed scope and stage.

## Compatibility and Migration

Existing inferred `main`, `south`, `north`, directional, and block records are
migrated as `candidate` nodes with source `legacy_inference`. They are not
automatically confirmed.

Existing manual annotations migrate to confirmed assignments and manual
operations. Existing scenes remain readable while a background semantic rebuild
creates the new representation. The frontend supports both schemas during the
migration window.

## Validation

### Generic automated corpus

Tests cover:

- residential towers and podiums;
- multi-building campuses;
- underground garages;
- industrial plants;
- bridge and tunnel packages;
- architecture, structure, enclosure, and MEP document families.

Required negative cases include:

- letter/number zones not automatically promoted to buildings;
- functional halls not automatically promoted to buildings;
- construction/enclosure zones not contaminating building hierarchy;
- unmatched drawings remaining explicitly unassigned.

### Shanghai Grand Opera validation

All 2309 PDFs are used as an external regression corpus. No production rule may
contain the project name or its known labels. Validation reports candidate
nodes, evidence, conflicts, unassigned drawings, LOD gates, extraction runtime,
and peak memory.

### Delivery gates

- backend unit and router/task integration tests pass;
- backend coverage remains at least 80%;
- frontend production build passes;
- model and annotation E2E flows pass;
- Docker services are healthy;
- representative browser workflows pass for semantic correction and LOD
  switching;
- performance regressions are reported against the 2309-file corpus.

## Out of Scope

- claiming construction-grade BIM accuracy from PDF-only inputs;
- using reference renderings as geometric truth;
- project-name-specific recognition rules;
- silently assigning ambiguous drawings to a generic building;
- automatically confirming large-language-model output.
