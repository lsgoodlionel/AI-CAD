import { test, expect, Page } from '@playwright/test'

const BASE = process.env.E2E_BASE_URL || 'http://localhost:3000'

type RoleKey = 'admin' | 'pm' | 'economist' | 'designer'

const ROLE_CREDS: Record<RoleKey, { username: string; password: string; label: RegExp }> = {
  admin: {
    username: process.env.E2E_ADMIN_USER || 'admin',
    password: process.env.E2E_ADMIN_PASS || 'admin123',
    label: /集团管理员|系统管理员/,
  },
  pm: {
    username: process.env.E2E_PM_USER || 'pm',
    password: process.env.E2E_PM_PASS || 'pm123',
    label: /项目经理/,
  },
  economist: {
    username: process.env.E2E_ECONOMIST_USER || 'economist',
    password: process.env.E2E_ECONOMIST_PASS || 'econ123',
    label: /经济师/,
  },
  designer: {
    username: process.env.E2E_DESIGNER_USER || 'designer',
    password: process.env.E2E_DESIGNER_PASS || 'designer123',
    label: /设计人员|深化设计师/,
  },
}

async function loginAs(page: Page, role: RoleKey) {
  const creds = ROLE_CREDS[role]
  await page.goto(`${BASE}/login`)
  await page.fill('input[name="username"]', creds.username)
  await page.fill('input[name="password"]', creds.password)
  await page.click('button[type="submit"]')
  await expect(page).toHaveURL(/\/drawings/, { timeout: 8000 })
  await expect(page.getByText(creds.label).first()).toBeVisible({ timeout: 8000 })
}

test.describe('角色 smoke matrix', () => {
  for (const role of Object.keys(ROLE_CREDS) as RoleKey[]) {
    test(`${role} 可访问基础业务页面`, async ({ page }) => {
      await loginAs(page, role)

      await expect(page.locator('.ant-pro-table').first()).toBeVisible()
      await expect(page.getByText('图纸管理').first()).toBeVisible()

      await page.goto(`${BASE}/incentive`)
      await expect(page.locator('.ant-pro-table').first()).toBeVisible()

      await page.goto(`${BASE}/dashboard/project`)
      await expect(page).not.toHaveURL(/\/login/)
      await expect(page.locator('main').first()).toBeVisible()
    })
  }

  test('admin 可访问系统管理和集团看板', async ({ page }) => {
    await loginAs(page, 'admin')

    await expect(page.getByText('系统管理').first()).toBeVisible()

    await page.goto(`${BASE}/dashboard/group`)
    await expect(page).not.toHaveURL(/\/login/)
    await expect(page.locator('main').first()).toBeVisible()
    await expect(page.getByText(/403/)).toHaveCount(0)
  })

  for (const role of ['pm', 'economist', 'designer'] as RoleKey[]) {
    test(`${role} 不能访问管理员页面`, async ({ page }) => {
      await loginAs(page, role)

      await expect(page.getByText('系统管理')).toHaveCount(0)

      await page.goto(`${BASE}/dashboard/group`)
      await expect(page.getByText(/403|权限不足/).first()).toBeVisible({ timeout: 8000 })

      await page.goto(`${BASE}/admin/model-management`)
      await expect(page.getByText(/403|权限不足/).first()).toBeVisible({ timeout: 8000 })
    })
  }
})
