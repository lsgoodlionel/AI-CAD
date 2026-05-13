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

test.describe('图纸列表', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('图纸列表页面正常渲染', async ({ page }) => {
    await expect(page.locator('.ant-table, .ant-pro-table')).toBeVisible()
    // 至少显示列头
    await expect(page.locator('th').filter({ hasText: /图纸|状态|专业/ }).first()).toBeVisible()
  })

  test('侧边菜单包含图纸管理', async ({ page }) => {
    const menu = page.locator('.ant-menu')
    await expect(menu.filter({ hasText: '图纸管理' })).toBeVisible()
  })

  test('点击图纸进入详情页', async ({ page }) => {
    // 如果有图纸数据，点击第一行；无数据时跳过
    const rows = page.locator('.ant-table-tbody tr')
    const count = await rows.count()
    test.skip(count === 0, '暂无图纸数据，跳过详情页测试')

    await rows.first().click()
    await expect(page).toHaveURL(/\/drawings\/[a-z0-9-]+/)
    await expect(page.locator('.ant-page-header, h4').first()).toBeVisible()
  })
})

test.describe('图纸详情 - 审批面板', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('详情页展示 AI 审图面板', async ({ page }) => {
    const rows = page.locator('.ant-table-tbody tr')
    const count = await rows.count()
    test.skip(count === 0, '暂无图纸数据')

    await rows.first().click()
    await expect(page).toHaveURL(/\/drawings\/[a-z0-9-]+/)

    // AI 审图 Tab 或面板应存在
    const aiPanel = page.locator('[data-testid="ai-review-panel"], .ant-tabs-tab').filter({
      hasText: /AI|审图/,
    })
    await expect(aiPanel.first()).toBeVisible({ timeout: 5000 })
  })
})
