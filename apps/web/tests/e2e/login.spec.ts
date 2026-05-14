/**
 * E2E: 登录流程
 * 覆盖: 正常登录跳转 / 错误凭证提示 / 登出后重定向
 */
import { test, expect } from '@playwright/test'

const BASE = process.env.E2E_BASE_URL || 'http://localhost:3000'
const ADMIN = {
  username: process.env.E2E_ADMIN_USER || 'admin',
  password: process.env.E2E_ADMIN_PASS || 'admin123',
}

test.describe('登录页', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE}/login`)
  })

  test('正确凭证登录后跳转到图纸列表', async ({ page }) => {
    await page.fill('input[name="username"]', ADMIN.username)
    await page.fill('input[name="password"]', ADMIN.password)
    await page.click('button[type="submit"]')

    await expect(page).toHaveURL(/\/drawings/, { timeout: 8000 })
    await expect(page.getByRole('main').getByText('图纸列表')).toBeVisible()
  })

  test('错误密码显示错误提示', async ({ page }) => {
    await page.fill('input[name="username"]', 'wrong_user')
    await page.fill('input[name="password"]', 'wrong_pass')
    await page.click('button[type="submit"]')

    await expect(
      page.locator('.ant-form-item-explain-error, .ant-alert-error, .ant-message-error')
    ).toBeVisible({ timeout: 5000 })
    await expect(page).toHaveURL(/\/login/)
  })

  test('未登录访问受保护页面重定向到 /login', async ({ page }) => {
    // 清除 localStorage token 确保未登录状态
    await page.evaluate(() => localStorage.clear())
    await page.goto(`${BASE}/drawings`)
    await expect(page).toHaveURL(/\/login/, { timeout: 5000 })
  })
})
