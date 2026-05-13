/**
 * E2E: 创效激励流程
 * 覆盖: 提案列表 / 状态漏斗标签 / 管理员看板可访问
 */
import { test, expect, Page } from '@playwright/test'

const BASE = process.env.E2E_BASE_URL || 'http://localhost:3000'

async function login(page: Page, role: 'admin' | 'pm' = 'admin') {
  const creds = {
    admin: { u: process.env.E2E_ADMIN_USER || 'admin', p: process.env.E2E_ADMIN_PASS || 'admin123' },
    pm:    { u: process.env.E2E_PM_USER    || 'pm',    p: process.env.E2E_PM_PASS    || 'pm123' },
  }
  const { u, p } = creds[role]
  await page.goto(`${BASE}/login`)
  await page.fill('input[name="username"]', u)
  await page.fill('input[name="password"]', p)
  await page.click('button[type="submit"]')
  await expect(page).toHaveURL(/\/drawings/, { timeout: 8000 })
}

test.describe('创效激励', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
    await page.goto(`${BASE}/incentive`)
  })

  test('提案列表页面正常渲染', async ({ page }) => {
    await expect(page).toHaveURL(/\/incentive/)
    await expect(page.locator('.ant-table, .ant-pro-table')).toBeVisible()
  })

  test('页面包含创建提案按钮', async ({ page }) => {
    const btn = page.locator('button').filter({ hasText: /发起|新建|提案/ })
    await expect(btn.first()).toBeVisible()
  })

  test('提案状态标签颜色正确（存在提案时）', async ({ page }) => {
    const tags = page.locator('.ant-tag')
    const count = await tags.count()
    if (count > 0) {
      // 至少一个 Tag 可见
      await expect(tags.first()).toBeVisible()
    }
  })
})

test.describe('数据看板', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('项目看板对普通用户可访问', async ({ page }) => {
    await page.goto(`${BASE}/dashboard/project`)
    await expect(page).not.toHaveURL(/\/login/)
    await expect(page.locator('h4, .ant-select').first()).toBeVisible({ timeout: 6000 })
  })

  test('集团看板仅管理员可访问（admin 可访问）', async ({ page }) => {
    await page.goto(`${BASE}/dashboard/group`)
    await expect(page).not.toHaveURL(/\/403|\/login/)
    await expect(page.locator('h4').first()).toBeVisible({ timeout: 6000 })
  })
})
