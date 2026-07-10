/**
 * E2E (A-20) · 工程模型页 — Fragments(IFC) 加载与渲染模式切换
 *
 * 覆盖关键流：进入 ProjectModel → 因 scene.model_ifc.frag_key 存在，默认进入
 * IFC(Fragments) 模式并拉取 .frag → 切到「构件」「贴图」→ 切回「IFC 模型」，
 * 全程无未捕获异常。
 *
 * ── Mock 策略（全部走 page.route，无需真实后端 / DB）──────────────────
 *  - `GET /api/v1/projects/<id>/model`         → 含 scene.model_ifc.frag_key 的模型响应
 *  - `GET /api/v1/projects/<id>/model/asset-url?key=…` → { url: <同源 fixture 地址> }
 *  - `GET /fixtures/sample_building.frag`       → committed 静态 .frag（由
 *        apps/model-convert 从 fixtures/sample_building.ifc 转出，见 tests/e2e/fixtures/）
 *  鉴权：注入本地可解码的假 JWT（getInitialState 只做本地 JWT 解析，不发 /auth/me）。
 *
 * ── 运行前置（本仓库当前无常驻 dev server，故 CI/本地需自起）──────────
 *   cd apps/web
 *   npm run copy-thatopen-assets              # 落 public/thatopen/*（worker + wasm）
 *   E2E_SKIP_SEED=1 npm run dev &             # 起前端（默认 http://localhost:3000）
 *   E2E_SKIP_SEED=1 npx playwright test tests/e2e/project-model-fragments.spec.ts --project=chromium
 *
 * 说明：本 spec 用 page.route 全量拦截后端，故 global-setup 的 DB seed 非必需
 *   （设 E2E_SKIP_SEED=1 跳过）。Fragments 渲染依赖 WebGL——headless chromium
 *   走 SwiftShader 软件渲染即可；断言只依赖 DOM 状态（渲染徽标 / 分段选中），
 *   与 WebGL 是否成功着色解耦，避免超时假设。确定性等待，不用 waitForTimeout。
 */
import { expect, test, type Page, type Route } from '@playwright/test'
import { readFileSync } from 'node:fs'
import path from 'node:path'

const BASE = process.env.E2E_BASE_URL || 'http://localhost:3000'
const PROJECT_ID = 'project-fragments-e2e'
const FRAG_KEY = `projects/${PROJECT_ID}/model_ifc/sample_building.frag`
const FRAG_FIXTURE = path.resolve(__dirname, 'fixtures/sample_building.frag')
const IFC_BADGE = '渲染: IFC · Fragments'

/** 无签名假 JWT（alg:none）——getInitialState 仅本地解码，不校验签名。 */
function makeToken(): string {
  const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url')
  const payload = Buffer.from(
    JSON.stringify({
      sub: 'e2e-admin',
      role: 'group_admin',
      display_name: 'E2E Admin',
      exp: Math.floor(Date.now() / 1000) + 3600,
    }),
  ).toString('base64url')
  return `${header}.${payload}.`
}

/** 一个含确定性构件 + model_ifc.frag_key 的最小可渲染模型响应。 */
function modelResponseBody(): string {
  const floor = {
    key: '1f',
    label: '1F',
    elevation: 0,
    elevation_m: 0,
    order: 1,
    drawings: [
      {
        drawing_id: 'drawing-1',
        drawing_no: 'A-101',
        title: '首层平面图',
        discipline: 'architecture',
        status: 'reviewed',
        current_stage: '深化',
        image_key: '',
        issue_count: 0,
        critical_count: 0,
      },
    ],
    elements: { columns: [], walls: [], beams: [], slabs: [], pipes: [], equipment: [] },
    element_stats: { columns: 0, walls: 0, beams: 0, slabs: 0, pipes: 0, equipment: 0 },
  }

  return JSON.stringify({
    status: 'ready',
    version: 3,
    built_at: '2026-07-10T00:00:00Z',
    error: null,
    building_units: { detected: [{ key: 'tower-a', display_name: 'A塔' }], manual: [] },
    quality: {
      unassigned_story_count: 0,
      floor_conflict_count: 0,
      pending_manual_count: 0,
      pending_candidate_count: 0,
      semantic_conflict_count: 0,
      low_confidence_building_units: [],
      floor_conflicts: [],
    },
    semantic_tree: { version: 1, nodes: [] },
    semantic_review_queue: [],
    annotation_queue: [],
    lod_modes: {
      review_skeleton: { enabled: true, label: '审图骨架' },
      architectural_massing: { enabled: true, label: '建筑体量' },
      realistic_proxy: { enabled: true, label: '实景近似' },
    },
    lod_capabilities: {},
    scene: {
      schema_version: 2,
      project: { id: PROJECT_ID, name: 'Fragments E2E 项目' },
      // A-05 契约：有 frag_key ⇒ 默认 IFC(Fragments) 模式（pickDefaultViewMode）
      model_ifc: {
        ifc_key: `projects/${PROJECT_ID}/model_ifc/sample_building.ifc`,
        frag_key: FRAG_KEY,
        build_mode: 'ifc',
        is_estimated: true,
        generated_at: '2026-07-10T00:00:00Z',
      },
      buildings: [{ key: 'tower-a', label: 'A塔', origin: [0, 0], floors: [floor] }],
      floors: [floor],
      markers: [],
      cross_links: [],
      ifc_models: [],
      stats: {
        total_drawings: 1,
        total_issues: 0,
        by_severity: { critical: 0, major: 0, minor: 0, info: 0 },
        by_discipline: { architecture: 1 },
        floors: 1,
        reconstruction: 'ifc',
        elements_total: { columns: 0, walls: 0, beams: 0, slabs: 0, pipes: 0, equipment: 0 },
      },
      generated_at: '2026-07-10T00:00:00Z',
    },
  })
}

interface FragmentsMockState {
  fragRequestCount: () => number
}

async function mockFragmentsModelPage(page: Page): Promise<FragmentsMockState> {
  let fragRequests = 0

  await page.addInitScript((token: string) => {
    localStorage.setItem('cad_token', token)
  }, makeToken())

  await page.route(`**/api/v1/projects/${PROJECT_ID}/model`, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: modelResponseBody(),
    })
  })

  // presigned URL 换取：回一个同源 fixture 地址（规避跨源 CSP connect-src）
  await page.route(`**/api/v1/projects/${PROJECT_ID}/model/asset-url**`, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ url: `${BASE}/fixtures/sample_building.frag` }),
    })
  })

  // 静态 .frag 资产：serve committed fixture 二进制
  await page.route('**/fixtures/sample_building.frag', async (route: Route) => {
    fragRequests += 1
    await route.fulfill({
      status: 200,
      contentType: 'application/octet-stream',
      body: readFileSync(FRAG_FIXTURE),
    })
  })

  return { fragRequestCount: () => fragRequests }
}

/** antd Segmented 选项（按可见文案点击，作用域限定在分段控件内）。 */
function segmentedOption(page: Page, label: string) {
  return page.locator('.ant-segmented').getByText(label, { exact: true })
}

test.describe('工程模型页 - Fragments 加载与模式切换', () => {
  test('默认进入 IFC(Fragments)、拉取 .frag，并在三种模式间切换无未捕获异常', async ({
    page,
  }) => {
    const pageErrors: string[] = []
    page.on('pageerror', (err) => pageErrors.push(err.message))

    const api = await mockFragmentsModelPage(page)

    await page.goto(`${BASE}/model/${PROJECT_ID}`)

    // 1) frag_key 存在 → 默认 IFC 模式，FragmentsScene 挂载（渲染徽标可见）
    await expect(page.getByText(IFC_BADGE)).toBeVisible()

    // 2) 加载器确实拉取了 .frag（确定性等待请求发生，而非固定超时）
    await expect.poll(() => api.fragRequestCount()).toBeGreaterThanOrEqual(1)

    // 3) 分段控件出现全部候选（IFC 模型 / 构件 / 贴图 / 混合）
    await expect(segmentedOption(page, 'IFC 模型')).toBeVisible()
    await expect(segmentedOption(page, '构件')).toBeVisible()
    await expect(segmentedOption(page, '贴图')).toBeVisible()

    // 4) 切到「构件」→ FragmentsScene 卸载（IFC 徽标消失，ModelViewer 接管）
    await segmentedOption(page, '构件').click()
    await expect(page.getByText(IFC_BADGE)).toBeHidden()

    // 5) 切到「贴图」→ 仍非 IFC 模式
    await segmentedOption(page, '贴图').click()
    await expect(page.getByText(IFC_BADGE)).toBeHidden()

    // 6) 切回「IFC 模型」→ FragmentsScene 重挂载、重新拉取 .frag
    const before = api.fragRequestCount()
    await segmentedOption(page, 'IFC 模型').click()
    await expect(page.getByText(IFC_BADGE)).toBeVisible()
    await expect.poll(() => api.fragRequestCount()).toBeGreaterThan(before)

    // 7) 全流程无未捕获异常（切换未泄漏/未崩溃）
    expect(pageErrors).toEqual([])
  })
})
