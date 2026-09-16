import { chromium } from 'playwright-core'
const PW = process.env.FIT_PW
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1380, height: 850 } })
const results = []
const check = (name, ok) => { results.push([name, ok]); console.log((ok ? '✅' : '❌') + ' ' + name) }

await page.goto('http://127.0.0.1:5200/login', { waitUntil: 'networkidle' })
check('登录页渲染', await page.locator('input[type=password]').isVisible())
await page.fill('input[type=password]', PW)
await page.click('button[type=submit]')
await page.waitForURL('**/coach', { timeout: 8000 })
check('登录跳转 Coach 页', page.url().includes('/coach'))
check('侧栏导航', await page.locator('.side-item').count() === 5)
check('快捷提问按钮', await page.locator('.quick button').count() >= 3)
check('动态画布空态', await page.locator('.canvas-empty').isVisible())

// 第一条消息(读类)
await page.fill('textarea', '杠铃卧推趋势如何')
await page.click('.send-btn')
await page.waitForSelector('.view-card', { timeout: 60000 })
check('画布自动渲染图表卡片', true)
check('ECharts canvas 渲染', (await page.locator('.view-card canvas').count()) >= 1)
// 等第一轮完全结束(发送按钮恢复可用)
await page.waitForFunction(() => !document.querySelector(".streaming-cursor"), null, { timeout: 60000 })
const reply1 = await page.locator('.msg-assistant .md').last().textContent()
check('AI 文字回复', !!(reply1 && reply1.length > 10))

// 第二条消息(写类 -> 确认卡片)
await page.fill('textarea', '记录：晨脉 55')
await page.click('.send-btn')
await page.waitForSelector('.confirm-card', { timeout: 60000 })
check('确认卡片弹出', true)
const previewText = await page.locator('.confirm-card').textContent()
check('预览内容含晨脉数据', previewText.includes('55'))
await page.click('.confirm-card .btn.no')
await page.waitForTimeout(2000)
check('取消流程完成', (await page.locator('.msg-assistant .md').last().textContent()).includes('取消'))

// 刷新恢复
await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(1500)
check('刷新后恢复对话历史', (await page.locator('.msg').count()) >= 4)

// 其他页面
for (const p of ['dashboard', 'trends', 'review', 'plan']) {
  await page.goto('http://127.0.0.1:5200/' + p, { waitUntil: 'networkidle' })
  await page.waitForTimeout(800)
  check(p + ' 页渲染', (await page.locator('.card').count()) >= 1)
}

// 移动端(同一页面改视口，共享登录态)
await page.goto('http://127.0.0.1:5200/coach', { waitUntil: 'networkidle' })
await page.setViewportSize({ width: 390, height: 844 })
await page.waitForTimeout(600)
check('移动端底部导航', (await page.locator('.bottom-item').count()) === 5)
check('移动端输入框可见', await page.locator('textarea').isVisible())

await browser.close()
const failed = results.filter(([, ok]) => !ok).length
console.log(`\n${results.length - failed}/${results.length} passed`)
process.exit(failed ? 1 : 0)
