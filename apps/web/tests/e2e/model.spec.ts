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
          low_confidence_building_units: [
            { key: 'tower-a', display_name: 'A塔', confidence: 0.58 },
            { key: 'manual-north-wing', display_name: '手工北翼', confidence: 0.42 },
          ],
          floor_conflicts: [
            { building_unit_key: 'tower-a', story_key: '2f', count: 1, message: '2F 标高冲突' },
          ],
        },
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
          realistic_proxy: {
            enabled: false,
            label: '实景近似',
            reason: '需要 LOD300 数据',
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

  return {
    getSavedBody: () => savedBody,
  }
}

test.describe('工程模型页 - LOD 与人工识别', () => {
  test('展示质量面板、数据驱动单体、人工识别队列，并可保存标注', async ({ page }) => {
    const api = await mockModelPage(page)

    await page.goto(`${BASE}/model/${PROJECT_ID}`)

    await expect(page.getByText('模型质量')).toBeVisible()
    await expect(page.getByText('未分层 3')).toBeVisible()
    await expect(page.getByText('楼层冲突 1')).toBeVisible()
    await expect(page.getByText('低置信度单体 2')).toBeVisible()
    await expect(page.getByText('待人工识别 2').first()).toBeVisible()

    await expect(page.getByText('A塔', { exact: true }).first()).toBeVisible()
    await expect(page.getByText('西裙房', { exact: true }).first()).toBeVisible()
    await expect(page.getByText('手工北翼', { exact: true }).first()).toBeVisible()

    await expect(page.getByRole('button', { name: '审图骨架' })).toBeVisible()
    await expect(page.getByRole('button', { name: '建筑体量' })).toBeVisible()
    await expect(page.getByRole('button', { name: '实景近似' })).toBeDisabled()
    await expect(page.getByText('构件', { exact: true }).first()).toBeVisible()
    await expect(page.getByText('贴图', { exact: true }).first()).toBeVisible()
    await expect(page.getByText('混合', { exact: true }).first()).toBeVisible()

    await expect(page.getByText('机房夹层平面图')).toBeVisible()
    await expect(page.getByText('M-201')).toBeVisible()
    await expect(page.getByText('OCR: 北翼夹层')).toBeVisible()

    const targetQueueItem = page.locator('.ant-list-item').filter({ hasText: '机房夹层平面图' })
    await targetQueueItem.scrollIntoViewIfNeeded()
    const fields = targetQueueItem.getByRole('combobox')
    await fields.nth(0).fill('北翼扩展段')
    await fields.nth(1).fill('夹层')
    await fields.nth(2).fill('机电平面')
    await targetQueueItem.getByRole('button', { name: '保存标注' }).click()

    await expect(page.getByText('机房夹层平面图')).toHaveCount(0)
    await expect(page.getByText('待人工识别 1').first()).toBeVisible()

    expect(api.getSavedBody()).toMatchObject({
      drawing_id: 'drawing-2',
      building_unit_name: '北翼扩展段',
      story_name: '夹层',
      drawing_type: '机电平面',
    })
  })
})
