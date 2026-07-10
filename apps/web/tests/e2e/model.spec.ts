import { expect, test, type Page } from '@playwright/test'

const BASE = process.env.E2E_BASE_URL || 'http://localhost:3000'
const PROJECT_ID = 'project-model-e2e'

function makeToken() {
  const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url')
  const payload = Buffer.from(JSON.stringify({
    sub: 'e2e-admin',
    role: 'group_admin',
    display_name: 'E2E Admin',
    exp: Math.floor(Date.now() / 1000) + 3600,
  })).toString('base64url')
  return `${header}.${payload}.`
}

async function mockModelPage(page: Page) {
  let savedBody: Record<string, unknown> | null = null
  const semanticBodies: Record<string, unknown>[] = []
  const impactBodies: Record<string, unknown>[] = []

  await page.addInitScript((token: string) => {
    localStorage.setItem('cad_token', token)
  }, makeToken())

  await page.route(`**/api/v1/projects/${PROJECT_ID}/model`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ready',
        version: 8,
        built_at: '2026-07-08T11:30:00Z',
        error: null,
        building_units: {
          detected: [
            { key: 'tower-a', display_name: 'A塔' },
            { key: 'west-podium', display_name: '西裙房' },
          ],
          manual: [
            { key: 'manual-north-wing', display_name: '手工北翼' },
          ],
        },
        quality: {
          unassigned_story_count: 3,
          floor_conflict_count: 1,
          pending_manual_count: 2,
          pending_candidate_count: 3,
          semantic_conflict_count: 1,
          low_confidence_building_units: [
            { key: 'tower-a', display_name: 'A塔', confidence: 0.58 },
            { key: 'manual-north-wing', display_name: '手工北翼', confidence: 0.42 },
          ],
          floor_conflicts: [
            { building_unit_key: 'tower-a', story_key: '2f', count: 1, message: '2F 标高冲突' },
          ],
        },
        semantic_tree: {
          version: 7,
          nodes: [
            {
              id: 'b1',
              node_type: 'building_unit',
              canonical_name: 'A座',
              normalized_key: 'a-building',
              status: 'confirmed',
              confidence: 0.98,
              source: 'manual',
              version: 3,
            },
            {
              id: 'z1',
              node_type: 'sub_zone',
              canonical_name: 'D1区',
              normalized_key: 'd1-zone',
              parent_id: 'b1',
              status: 'candidate',
              confidence: 0.71,
              source: 'automatic',
              version: 1,
            },
            {
              id: 'f1',
              node_type: 'functional_space',
              canonical_name: '观众厅',
              normalized_key: 'auditorium',
              parent_id: 'b1',
              status: 'candidate',
              confidence: 0.87,
              source: 'legacy_inference',
              version: 1,
            },
            {
              id: 'c1',
              node_type: 'construction_zone',
              canonical_name: '2-1区',
              normalized_key: '2-1-zone',
              status: 'candidate',
              confidence: 0.64,
              source: 'automatic',
              version: 1,
            },
          ],
        },
        semantic_review_queue: [
          {
            node_id: 'z1',
            title: 'D1区',
            node_type: 'sub_zone',
            status: 'candidate',
            canonical_name: 'D1区',
            current_parent_id: 'b1',
            version: 7,
            confidence: 0.71,
            evidence: [
              {
                id: 'evidence-z1-1',
                label: '总平定位图',
                detail: 'OCR 命中 D1 区并关联到 A 座',
                score: 0.71,
              },
            ],
            valid_targets: {
              merge: ['c1'],
              reparent: ['b1', 'c1'],
            },
          },
          {
            node_id: 'f1',
            title: '观众厅',
            node_type: 'functional_space',
            status: 'candidate',
            canonical_name: '观众厅',
            current_parent_id: 'b1',
            version: 7,
            confidence: 0.87,
            evidence: [
              {
                id: 'evidence-f1-1',
                label: '观众厅平面',
                detail: '房间名称与功能空间规则一致',
                score: 0.87,
              },
            ],
            valid_targets: {
              merge: [],
              reparent: ['b1'],
            },
          },
          {
            node_id: 'c1',
            title: '2-1区',
            node_type: 'construction_zone',
            status: 'candidate',
            canonical_name: '2-1区',
            version: 7,
            confidence: 0.64,
            evidence: [
              {
                id: 'evidence-c1-1',
                label: '施工区划分表',
                detail: '节点详图中出现 2-1 区',
                score: 0.64,
              },
            ],
            valid_targets: {
              merge: ['z1'],
              reparent: ['b1'],
            },
          },
        ],
        annotation_queue: [
          {
            id: 'queue-1',
            drawing_id: 'drawing-1',
            drawing_no: 'A-101',
            title: 'A塔首层平面图',
            clue_text: ['标题命中 A塔', '楼层候选 1F'],
            detected: {
              building_unit_key: 'tower-a',
              story_key: '1f',
              drawing_type: 'plan',
              confidence: 0.78,
            },
          },
          {
            id: 'queue-2',
            drawing_id: 'drawing-2',
            drawing_no: 'M-201',
            title: '机房夹层平面图',
            clue_text: ['OCR: 北翼夹层', '未命中已知楼层'],
            detected: {
              building_unit_key: 'manual-north-wing',
              story_key: null,
              drawing_type: 'mep_plan',
              confidence: 0.31,
            },
          },
        ],
        lod_modes: {
          review_skeleton: { enabled: true, label: '审图骨架' },
          architectural_massing: { enabled: true, label: '建筑体量' },
          realistic_proxy: { enabled: true, label: '实景近似（近似）' },
        },
        lod_capabilities: {
          b1: {
            level: 200,
            missing_evidence: ['registered_grid', 'dimensions'],
            degradation_reasons: ['LOD300 构件证据缺失，已回退到代理体量'],
            available_modes: ['review_skeleton', 'architectural_massing', 'realistic_proxy'],
          },
        },
        scene: {
          schema_version: 2,
          project: { id: PROJECT_ID, name: '模型 E2E 项目' },
          buildings: [
            {
              key: 'tower-a',
              label: 'A塔',
              origin: [0, 0],
              floors: [
                {
                  key: '1f',
                  label: '1F',
                  elevation: 0,
                  elevation_m: 0,
                  order: 1,
                  drawings: [
                    {
                      drawing_id: 'drawing-1',
                      drawing_no: 'A-101',
                      title: 'A塔首层平面图',
                      discipline: 'architecture',
                      status: 'reviewed',
                      current_stage: '深化',
                      image_key: '',
                      issue_count: 2,
                      critical_count: 1,
                    },
                  ],
                  elements: {
                    columns: [],
                    walls: [],
                    beams: [],
                    slabs: [],
                    pipes: [],
                    equipment: [],
                  },
                  element_stats: {
                    columns: 0,
                    walls: 0,
                    beams: 0,
                    slabs: 0,
                    pipes: 0,
                    equipment: 0,
                  },
                },
              ],
            },
            {
              key: 'west-podium',
              label: '西裙房',
              origin: [0, 0],
              floors: [
                {
                  key: 'b1',
                  label: 'B1',
                  elevation: -1,
                  elevation_m: -4.2,
                  order: -1,
                  drawings: [],
                  elements: {
                    columns: [],
                    walls: [],
                    beams: [],
                    slabs: [],
                    pipes: [],
                    equipment: [],
                  },
                  element_stats: {
                    columns: 0,
                    walls: 0,
                    beams: 0,
                    slabs: 0,
                    pipes: 0,
                    equipment: 0,
                  },
                },
              ],
            },
          ],
          floors: [
            {
              key: '1f',
              label: '1F',
              elevation: 0,
              elevation_m: 0,
              order: 1,
              drawings: [
                {
                  drawing_id: 'drawing-1',
                  drawing_no: 'A-101',
                  title: 'A塔首层平面图',
                  discipline: 'architecture',
                  status: 'reviewed',
                  current_stage: '深化',
                  image_key: '',
                  issue_count: 2,
                  critical_count: 1,
                },
              ],
              elements: {
                columns: [],
                walls: [],
                beams: [],
                slabs: [],
                pipes: [],
                equipment: [],
              },
              element_stats: {
                columns: 0,
                walls: 0,
                beams: 0,
                slabs: 0,
                pipes: 0,
                equipment: 0,
              },
            },
            {
              key: 'b1',
              label: 'B1',
              elevation: -1,
              elevation_m: -4.2,
              order: -1,
              drawings: [],
              elements: {
                columns: [],
                walls: [],
                beams: [],
                slabs: [],
                pipes: [],
                equipment: [],
              },
              element_stats: {
                columns: 0,
                walls: 0,
                beams: 0,
                slabs: 0,
                pipes: 0,
                equipment: 0,
              },
            },
          ],
          markers: [],
          cross_links: [],
          ifc_models: [],
          lod_modes: {
            review_skeleton: { enabled: true, label: '审图骨架' },
            architectural_massing: { enabled: true, label: '建筑体量' },
            realistic_proxy: { enabled: true, label: '实景近似（近似）' },
          },
          lod_capabilities: {
            b1: {
              level: 200,
              missing_evidence: ['registered_grid', 'dimensions'],
              passed_gates: ['plan_boundary', 'story_order', 'scale_or_coordinates'],
            },
          },
          stats: {
            total_drawings: 2,
            total_issues: 2,
            by_severity: {
              critical: 1,
              major: 1,
              minor: 0,
              info: 0,
            },
            by_discipline: {
              architecture: 1,
              mep: 1,
            },
            floors: 2,
            reconstruction: 'mixed',
            elements_total: {
              columns: 0,
              walls: 0,
              beams: 0,
              slabs: 0,
              pipes: 0,
              equipment: 0,
            },
          },
          generated_at: '2026-07-08T11:30:00Z',
        },
      }),
    })
  })

  await page.route(`**/api/v1/projects/${PROJECT_ID}/model/annotations`, async (route) => {
    savedBody = JSON.parse(route.request().postData() || '{}')
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ok: true }),
    })
  })

  await page.route(`**/api/v1/projects/${PROJECT_ID}/model/rebuild-impact**`, async (route) => {
    const url = new URL(route.request().url())
    const body = {
      operation: url.searchParams.get('operation_type'),
      node_id: url.searchParams.get('node_id'),
      version: Number(url.searchParams.get('expected_version') || 0),
      target_node_id: url.searchParams.get('target_node_id') || undefined,
    }
    impactBodies.push(body)
    const operation = String(body.operation ?? '')
    const targetName = body.target_node_id === 'c1' ? '2-1区' : 'A座'
    const response = operation === 'reparent'
      ? {
        affected_scope: ['A座 / D1区'],
        summary: `将 D1区 调整到 ${targetName}，会重建对应分支`,
        rebuild_scope: 'branch',
      }
      : {
        affected_scope: ['A座 / 观众厅'],
        summary: '确认后将锁定观众厅命名并更新空间统计',
        rebuild_scope: 'node',
      }

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(response),
    })
  })

  await page.route(`**/api/v1/projects/${PROJECT_ID}/model/semantic-operations`, async (route) => {
    const body = JSON.parse(route.request().postData() || '{}')
    semanticBodies.push({
      operation: body.operation_type,
      node_id: body.target_ids?.[0],
      version: body.expected_version,
      target_node_id: body.target_node_id,
    })
    if (body.operation_type === 'reparent') {
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: {
            code: 'SEMANTIC_VERSION_CONFLICT',
            latest: { version: 8 },
          },
        }),
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        semantic_tree_version: 8,
      }),
    })
  })

  return {
    getSavedBody: () => savedBody,
    getSemanticBodies: () => semanticBodies,
    getImpactBodies: () => impactBodies,
  }
}

test.describe('工程模型页 - LOD 与人工识别', () => {
  test('展示语义树、候选审查和 LOD 质量信息，并驱动语义操作', async ({ page }) => {
    const api = await mockModelPage(page)

    await page.goto(`${BASE}/model/${PROJECT_ID}`)

    await expect(page.getByText('模型质量')).toBeVisible()
    await expect(page.getByText('未分层 3').first()).toBeVisible()
    await expect(page.getByText('楼层冲突 1').first()).toBeVisible()
    await expect(page.getByText('低置信度单体 2').first()).toBeVisible()
    await expect(page.getByText('待人工识别 2').first()).toBeVisible()
    await expect(page.getByText('待审语义 3').first()).toBeVisible()
    await expect(page.getByText('语义冲突 1').first()).toBeVisible()
    await expect(page.getByText('LOD 200').first()).toBeVisible()
    await expect(page.getByText('registered_grid')).toBeVisible()
    await expect(page.getByText('dimensions')).toBeVisible()

    await expect(page.getByTestId('semantic-group-building_unit')).toContainText('A座')
    await expect(page.getByTestId('semantic-group-sub_zone')).toContainText('D1区')
    await expect(page.getByTestId('semantic-group-functional_space')).toContainText('观众厅')
    await expect(page.getByTestId('semantic-group-construction_zone')).toContainText('2-1区')
    await expect(page.getByTestId('semantic-group-building_unit').getByText('D1区')).toHaveCount(0)

    await expect(page.getByRole('button', { name: '审图骨架' })).toBeVisible()
    await expect(page.getByRole('button', { name: '建筑体量' })).toBeVisible()
    await expect(page.getByRole('button', { name: '实景近似' })).toBeEnabled()
    await page.getByRole('button', { name: '实景近似' }).click()
    await expect(page.getByRole('button', { name: '实景近似' })).toHaveClass(/ant-btn-primary/)
    await expect(page.getByText('构件', { exact: true }).first()).toBeVisible()
    await expect(page.getByText('贴图', { exact: true }).first()).toBeVisible()
    await expect(page.getByText('混合', { exact: true }).first()).toBeVisible()

    await page.getByRole('button', { name: '查看证据 D1区' }).click()
    await expect(page.getByRole('dialog', { name: 'D1区 证据' })).toBeVisible()
    await expect(page.getByText('OCR 命中 D1 区并关联到 A 座')).toBeVisible()
    await page.getByRole('button', { name: '关闭证据' }).click()

    await page.getByRole('button', { name: '确认观众厅' }).click()
    await expect(page.getByRole('dialog', { name: '确认语义节点' })).toBeVisible()
    await expect(page.getByText('确认后将锁定观众厅命名并更新空间统计')).toBeVisible()
    await page.getByRole('button', { name: '提交确认' }).click()

    await page.getByRole('button', { name: '调整父级 D1区' }).click()
    await expect(page.getByRole('dialog', { name: '调整父级' })).toBeVisible()
    await page.getByRole('combobox', { name: '新的父级' }).click()
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('Enter')
    await expect(page.getByText('将 D1区 调整到 2-1区，会重建对应分支')).toBeVisible()
    await page.getByRole('button', { name: '提交父级调整' }).click()
    await expect.poll(() => api.getSemanticBodies().length).toBe(2)

    await expect(page.getByRole('button', { name: '刷新到 v8' })).toBeVisible()

    expect(api.getImpactBodies()).toEqual([
      { operation: 'confirm', node_id: 'f1', version: 7 },
      { operation: 'reparent', node_id: 'z1', version: 7, target_node_id: 'c1' },
    ])

    expect(api.getSemanticBodies()).toEqual([
      { operation: 'confirm', node_id: 'f1', version: 7 },
      { operation: 'reparent', node_id: 'z1', version: 7, target_node_id: 'c1' },
    ])

    expect(api.getSavedBody()).toBeNull()
  })
})
