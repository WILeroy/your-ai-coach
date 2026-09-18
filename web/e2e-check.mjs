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
check('无侧栏导航(单页)', (await page.locator('.side-item').count()) === 0)
check('快捷提问按钮', await page.locator('.quick button').count() >= 3)
// 画布默认今日概览
try {
  await page.waitForSelector('.canvas .view-card', { timeout: 10000 })
  check('画布默认今日概览', (await page.locator('.canvas .view-card').count()) >= 1
    && (await page.locator('.badge').isVisible()))
} catch { check('画布默认今日概览', false) }

// 第一条消息(读类)
await page.fill('textarea', '杠铃卧推趋势如何')
await page.click('.send-btn')
await page.waitForSelector('.view-card', { timeout: 60000 })
check('画布自动渲染图表卡片', true)
try {
  await page.waitForSelector('.view-card canvas', { timeout: 8000 })
  check('ECharts canvas 渲染', true)
} catch { check('ECharts canvas 渲染', false) }
// 等第一轮完全结束(发送按钮恢复可用)
await page.waitForFunction(() => !document.querySelector(".streaming-cursor"), null, { timeout: 60000 })
const reply1 = await page.locator('.msg-assistant .md').last().textContent()
check('AI 文字回复', !!(reply1 && reply1.length > 10))

// 第二条消息(写类 -> 确认卡片)
await page.fill('textarea', '记录：静息心率 55，HRV 52')
await page.click('.send-btn')
await page.waitForSelector('.confirm-card', { timeout: 60000 })
check('确认卡片弹出', true)
const previewText = await page.locator('.confirm-card').textContent()
check('预览内容含静息心率/HRV数据', previewText.includes('55') && previewText.includes('52'))
await page.click('.confirm-card .btn.no')
await page.waitForTimeout(2000)
check('取消流程完成', (await page.locator('.msg-assistant .md').last().textContent()).includes('取消'))

// 刷新恢复
await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(1500)
check('刷新后恢复对话历史', (await page.locator('.msg').count()) >= 4)

// 联网搜索 → 来源卡片
await page.click('.hbtn:has-text("新会话")')
await page.waitForTimeout(500)
await page.fill('textarea', '搜一下壶铃摇摆动作要点')
await page.click('.send-btn')
try {
  await page.waitForSelector('.sr-item', { timeout: 60000 })
  const links = await page.locator('.sr-item').count()
  check('联网搜索来源卡片渲染', links >= 2)
  await page.waitForFunction(() => !document.querySelector('.streaming-cursor'), null, { timeout: 60000 })
} catch {
  check('联网搜索来源卡片渲染', false)
}

// 会话删除
await page.click('.hbtn:has-text("历史")')
await page.waitForSelector('.session-item', { timeout: 5000 })
const before = await page.locator('.session-item').count()
await page.locator('.session-item .s-del').first().hover()
await page.locator('.session-item .s-del').first().click()   // 第一次: 进入确认态
await page.waitForTimeout(300)
await page.locator('.session-item .s-del.armed').first().click()  // 第二次: 确认删除
await page.waitForTimeout(800)
const after = await page.locator('.session-item').count()
check('会话删除(列表减少+重置)', after === before - 1 && (await page.locator('.msg').count()) <= 1)

// 旧路由回退到 coach
await page.goto('http://127.0.0.1:5200/trends', { waitUntil: 'networkidle' })
await page.waitForTimeout(600)
check('旧路由回退教练页', page.url().includes('/coach'))

// 画布"回到概览"
if (await page.locator('.clear-btn').count()) {
  await page.click('.clear-btn')
  await page.waitForTimeout(600)
  check('回到默认概览', (await page.locator('.badge').count()) >= 1)
}

// 移动端(同视口切换, 单页无底部导航)
await page.setViewportSize({ width: 390, height: 844 })
await page.waitForTimeout(600)
check('移动端无底部导航', (await page.locator('.bottom-item').count()) === 0)
check('移动端输入框可见', await page.locator('textarea').isVisible())

// 登出
await page.setViewportSize({ width: 1380, height: 850 })
await page.click('.hbtn.out')
await page.waitForTimeout(800)
check('登出回登录页', page.url().includes('/login'))

await browser.close()
const failed = results.filter(([, ok]) => !ok).length
console.log(`\n${results.length - failed}/${results.length} passed`)
process.exit(failed ? 1 : 0)
