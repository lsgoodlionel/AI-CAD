# Umi Dependency Risk Window

Last evaluated: 2026-05-13

## Current State

The frontend currently resolves `@umijs/max` and `umi` to `4.6.53`, which is the current npm `latest` version at the time of evaluation.

`npm audit --json` reports:

- critical: 0
- high: 0
- moderate: 27
- low: 9
- total: 36

The remaining moderate/low findings are transitive chains under Umi's build tool stack, primarily:

- `@umijs/max -> umi -> @umijs/* -> esbuild`
- `@umijs/max -> umi -> @umijs/preset-umi -> vite`
- `@umijs/max -> umi -> @umijs/preset-umi -> react-router/react-router-dom`
- legacy browser polyfill chain under `node-libs-browser` / `crypto-browserify`

The application is deployed through `max build` output served by nginx. The most relevant remaining findings are development-server or bundler-chain advisories, not runtime server dependencies in the production container.

## Upgrade Window Recommendation

Treat the remaining audit work as a framework upgrade batch instead of piecemeal overrides.

Recommended trigger:

1. Umi publishes a version newer than `4.6.53` that upgrades `esbuild`, `vite`, or `react-router` past the vulnerable ranges.
2. Ant Design 6 migration is planned, because `antd@6` and icon major updates can change UI behavior and snapshots.
3. The team can reserve a full frontend regression window for routing, access control, ProTable, layout, and build output verification.

## Acceptance Criteria

Before merging a Umi upgrade batch:

1. `npm ci` completes without peer dependency overrides beyond the existing explicit `overrides`.
2. `npm audit --audit-level=high` reports zero high/critical vulnerabilities.
3. `npm run lint` passes.
4. `npm run build` passes.
5. `npx playwright test --project=chromium` passes against the Docker production profile.
6. Manual smoke covers login, drawings, drawing detail, incentive list, project dashboard, group dashboard, and admin menu access.

## Current Decision

No Umi upgrade is applied in this batch because `@umijs/max@4.6.53` is already the current latest release and the remaining advisories are still present through Umi-managed transitive dependencies. The next actionable step is to re-run this evaluation when a newer Umi release is available.
