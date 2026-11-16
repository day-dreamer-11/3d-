import { chromium } from 'playwright'
import { mkdirSync, writeFileSync } from 'node:fs'
const URL = 'http://127.0.0.1:8610/app/?api=http%3A%2F%2F127.0.0.1%3A8705&projectId=PRJ-20261030-10DEE0&sceneId=SCN-20261030-215033-06A6'
const OUT = 'D:/AI数字工厂-交付/_e2e_data/shots'
mkdirSync(OUT, { recursive: true })
const sleep = (ms) => new Promise(r => setTimeout(r, ms))
const browser = await chromium.launch({ headless: true, executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' })
const ctx = await browser.newContext({ viewport: { width: 1600, height: 950 } })
const page = await ctx.newPage()
await page.addInitScript(() => { try { localStorage.setItem('oip-web/tutorial-done','1') } catch(e) {} })
const errors = []
page.on('pageerror', e => errors.push(String(e)))
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })
const log = (...a) => console.log(...a)
try {
  await page.goto(URL)
  await page.waitForFunction(() => !!window.__oip?.store, null, { timeout: 30000, polling: 200 })
  await sleep(3500)
  const skip = page.locator('button', { hasText: /^Skip tour$/ })
  if (await skip.count()) { await skip.first().click().catch(()=>{}) ; await sleep(400) }
  log('store ready, parts =', await page.evaluate(() => window.__oip.store.getState().scene.parts.length))
  await page.waitForSelector('#aic-sync-pill', { timeout: 10000 })
  log('pill:', await page.locator('#aic-sync-pill').innerText())
  await page.screenshot({ path: `${OUT}/01-oip-loaded-empty.png` })

  // Real UI: place a Roller Conveyor from the catalog
  const tab = page.locator('button.catalog-tab', { hasText: /^Equipment$/ }).first()
  await tab.click({ force: true }); await sleep(300)
  const card = page.locator('button.catalog-card-main').filter({ has: page.locator('.catalog-card-label', { hasText: /^Roller Conveyor$/ }) }).first()
  await card.click({ force: true }); await sleep(400)
  const pt = await page.evaluate(() => window.__oip.view.worldToScreen(0, 0, 0))
  await page.mouse.move(pt.x, pt.y, { steps: 6 }); await page.mouse.move(pt.x + 1, pt.y + 1); await page.mouse.move(pt.x, pt.y)
  await sleep(200); await page.mouse.down(); await page.mouse.up(); await page.keyboard.press('Escape'); await sleep(600)
  log('after add parts =', await page.evaluate(() => window.__oip.store.getState().scene.parts.length))
  await page.locator('#aic-sync-pill button').click({ force: true })
  await sleep(1500)
  log('pill after sync:', await page.locator('#aic-sync-pill').innerText())
  await page.screenshot({ path: `${OUT}/02-oip-added-and-synced.png` })

  // Ctrl+S real keyboard save path
  await page.keyboard.press('Control+s')
  await sleep(2000)
  log('pill after ctrl+s:', await page.locator('#aic-sync-pill').innerText())
  writeFileSync(`${OUT}/bridge-errors.json`, JSON.stringify(errors, null, 2))
} finally {
  await browser.close()
}
