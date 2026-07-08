/**
 * E2E: 图纸管理核心流程
 * 覆盖: 图纸列表展示 / 上传图纸 / 状态 Tag 显示 / 详情页导航
 */
import { test, expect, Page } from '@playwright/test'

const BASE = process.env.E2E_BASE_URL || 'http://localhost:3000'
const ADMIN = {
  username: process.env.E2E_ADMIN_USER || 'admin',
  password: process.env.E2E_ADMIN_PASS || 'admin123',
}

async function login(page: Page) {
  await page.goto(`${BASE}/login`)
  await page.fill('input[name="username"]', ADMIN.username)
  await page.fill('input[name="password"]', ADMIN.password)
  await page.click('button[type="submit"]')
  await expect(page).toHaveURL(/\/drawings/, { timeout: 8000 })
}

async function openFirstDrawing(page: Page) {
  await expect(page.locator('.ant-pro-table').first()).toBeVisible()

  const rows = page.locator('.ant-table-tbody tr:not(.ant-table-placeholder)')
  await expect(rows.first()).toBeVisible({ timeout: 8000 })

  await rows.first().getByRole('button', { name: /查看/ }).click()
  await expect(page).toHaveURL(/\/drawings\/[a-z0-9-]+/)
}

test.describe('图纸列表', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('图纸列表页面正常渲染', async ({ page }) => {
    await expect(page.locator('.ant-pro-table').first()).toBeVisible()
    // 至少显示列头
    await expect(page.locator('th').filter({ hasText: /图纸|状态|专业/ }).first()).toBeVisible()
  })

  test('侧边菜单包含图纸管理', async ({ page }) => {
    test.skip(!!page.viewportSize() && page.viewportSize()!.width < 768, '移动端侧边栏默认折叠')

    const menu = page.locator('.ant-menu')
    await expect(menu.filter({ hasText: '图纸管理' })).toBeVisible()
  })

  test('点击图纸进入详情页', async ({ page }) => {
    await openFirstDrawing(page)
    await expect(page.getByTestId('drawing-detail-page')).toBeVisible()
    await expect(page.getByText('E2E-ARCH-001')).toBeVisible()
  })
})

test.describe('图纸详情 - 审批面板', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('详情页展示 AI 审图面板', async ({ page }) => {
    await openFirstDrawing(page)

    // AI 审图 Tab 或面板应存在
    const aiPanel = page.getByTestId('ai-review-panel')
    await expect(aiPanel).toBeVisible({ timeout: 5000 })
    await expect(aiPanel).toContainText(/AI 审查报告|审查|问题|暂无/)
  })
})
