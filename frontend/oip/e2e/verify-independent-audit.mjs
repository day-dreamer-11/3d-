// Independent audit of the Open Industry Project editor, driven through the real UI.
// Run with the dev server on 127.0.0.1:5178: node e2e/verify-independent-audit.mjs
/* global process, console, window, document */
import { chromium } from 'playwright'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { createProject, runProjectMenu } from './_projectMenu.mjs'
import { cleanShot } from './_shot.mjs'
import { withGpuArgs } from './_gpuArgs.mjs'

const URL = process.env.OIP_AUDIT_URL ?? 'http://127.0.0.1:5178/app/'
const OUT = 'D:/OI-verify/artifacts'
const NAME = 'OI Independent Audit'
mkdirSync(OUT, { recursive: true })

const results = []
const checks = []
const log = (...a) => console.log(...a)
function check(name, ok, detail = '') {
  results.push({ name, ok: !!ok, detail })
  log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`)
  if (!ok) checks.push(name)
}

async function waitForApp(page) {
  await page.waitForFunction(
    () => !!(window.__oip?.store && window.__oip.view && window.__oip.sim && window.__oip.controller),
    null,
    { timeout: 30_000, polling: 200 },
  )
  await page.waitForTimeout(1800)
}

const partsOf = (page) =>
  page.evaluate(() =>
    window.__oip.store.getState().scene.parts.map((p) => ({
      id: p.id,
      type: p.type,
      packageId: p.packageId,
      name: p.name,
      position: p.position,
      rotationY: p.rotationY,
      params: p.params ?? {},
      parent: p.parent ?? null,
    })),
  )

async function shot(page, name, opts = {}) {
  const path = join(OUT, name)
  await page.screenshot({ path, ...opts })
  log('shot:', path)
  return path
}

async function setCamera(page) {
  await page.evaluate(() => {
    const v = window.__oip.view
    if (v.setCameraPose) v.setCameraPose({ px: 5, py: 9, pz: 13, tx: 0, ty: 0.5, tz: 0 }, { instant: true })
    else {
      v.camera.position.set(5, 9, 13)
      v.camera.lookAt(0, 0.5, 0)
      v.camera.updateMatrixWorld(true)
    }
  })
  await page.waitForTimeout(500)
}

async function screenAt(page, x, y, z) {
  return page.evaluate(([x, y, z]) => window.__oip.view.worldToScreen(x, y, z), [x, y, z])
}

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

async function placeFromCatalog(page, tab, label, wx, wy, wz, beforeIds) {
  const tabBtn = page.locator('button.catalog-tab', { hasText: new RegExp(`^${escapeRe(tab)}$`) }).first()
  await tabBtn.click({ force: true })
  await page.waitForTimeout(250)
  const card = page
    .locator('button.catalog-card-main')
    .filter({ has: page.locator('.catalog-card-label', { hasText: new RegExp(`^${escapeRe(label)}$`) }) })
    .first()
  await card.scrollIntoViewIfNeeded()
  await card.click({ force: true })
  await page.waitForTimeout(350)
  const p = await screenAt(page, wx, wy, wz)
  if (!p.visible) throw new Error(`target off-screen for ${label} at ${wx},${wy},${wz}: ${JSON.stringify(p)}`)
  await page.mouse.move(p.x, p.y, { steps: 8 })
  await page.mouse.move(p.x + 1, p.y + 1)
  await page.mouse.move(p.x, p.y, { steps: 3 })
  await page.waitForTimeout(250)
  await page.mouse.down()
  await page.mouse.up()
  await page.keyboard.press('Escape')
  await page.waitForTimeout(500)
  const after = await partsOf(page)
  const created = after.find((q) => !beforeIds.includes(q.id))
  return created ?? null
}

async function clickPart(page, part) {
  const p = await screenAt(page, part.position[0], part.position[1] + 0.05, part.position[2])
  if (!p.visible) throw new Error('part off-screen')
  await page.mouse.click(p.x, p.y)
  await page.waitForTimeout(350)
  return page.evaluate(() => window.__oip.store.getState().selectedIds)
}

async function repositionTo(page, part, wx, wz, useAlt = false) {
  const p = await screenAt(page, part.position[0], part.position[1] + 0.05, part.position[2])
  await page.mouse.move(p.x, p.y, { steps: 4 })
  await page.waitForTimeout(100)
  await page.keyboard.down('Shift')
  await page.keyboard.press('g')
  await page.keyboard.up('Shift')
  await page.waitForTimeout(200)
  if (useAlt) await page.keyboard.down('Alt')
  const t = await screenAt(page, wx, 0, wz)
  await page.mouse.move(t.x, t.y, { steps: 10 })
  await page.waitForTimeout(250)
  await page.mouse.down()
  await page.mouse.up()
  if (useAlt) await page.keyboard.up('Alt')
  await page.waitForTimeout(400)
}

function conveyorEnds(part) {
  const L = typeof part.params.length === 'number' ? part.params.length : null
  if (L === null) return null
  const c = Math.cos(part.rotationY)
  const s = Math.sin(part.rotationY)
  const hx = (L / 2) * c
  const hz = -(L / 2) * s
  const [x, y, z] = part.position
  return { front: [x + hx, y, z + hz], back: [x - hx, y, z - hz], dir: [c, 0, -s] }
}

function dist(a, b) {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])
}

function seam(a, b) {
  const ea = conveyorEnds(a)
  const eb = conveyorEnds(b)
  if (!ea || !eb) return null
  const dFB = dist(ea.front, eb.back)
  const dBF = dist(ea.back, eb.front)
  return dFB <= dBF ? { gap: dFB, which: 'A.front-B.back', dot: ea.dir[0] * eb.dir[0] + ea.dir[2] * eb.dir[2] } : { gap: dBF, which: 'A.back-B.front', dot: -(ea.dir[0] * eb.dir[0] + ea.dir[2] * eb.dir[2]) }
}

async function main() {
  const launchOpts = withGpuArgs({ headless: true })
  if (process.env.OIP_BROWSER_PATH) launchOpts.executablePath = process.env.OIP_BROWSER_PATH
  const browser = await chromium.launch(launchOpts)
  const context = await browser.newContext({ viewport: { width: 1600, height: 950 }, acceptDownloads: true })
  const page = await context.newPage()
  const consoleErrors = []
  page.on('pageerror', (e) => consoleErrors.push(String(e)))
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text())
  })
  await page.addInitScript(() => {
    try {
      localStorage.setItem('oip-web/tutorial-done', '1')
    } catch {
      /* ignore */
    }
  })

  try {
    // ── 1. boot ────────────────────────────────────────────────────────────────
    await page.goto(URL)
    await waitForApp(page)
    await page.evaluate(() => window.localStorage.clear())
    await page.addInitScript(() => {
      try {
        localStorage.setItem('oip-web/tutorial-done', '1')
      } catch {
        /* ignore */
      }
    })
    await page.reload()
    await waitForApp(page)
    const skip = page.locator('button', { hasText: /^Skip tour$/ })
    if (await skip.count()) await skip.first().click().catch(() => {})
    await page.waitForTimeout(500)
    check('editor boots and window.__oip hooks are present', true)
    await shot(page, '01-editor-boot.png')

    // ── 2. new project from the projects list (real UI) ───────────────────────
    await createProject(page, NAME)
    await waitForApp(page)
    await page.waitForTimeout(1200)
    const blank = await partsOf(page)
    check('new project opens a fresh document', blank.length <= 1, `parts=${blank.length}`)
    check('new project starts with the default building shell (blank floor)', blank.length === 1 && blank[0].type === 'building', JSON.stringify(blank.map((p) => p.type)))
    await setCamera(page)
    await shot(page, '02-new-blank-scene.png')

    // delete the default building → truly empty scene, then undo
    await page.evaluate((id) => window.__oip.store.select(id), blank[0].id)
    await page.waitForTimeout(200)
    await page.keyboard.press('Delete')
    await page.waitForTimeout(400)
    const afterDelete = await partsOf(page)
    await page.keyboard.press('Control+z')
    await page.waitForTimeout(500)
    const afterUndo = await partsOf(page)
    check('default building can be deleted (empty floor) and undone', afterDelete.length === 0 && afterUndo.length === 1, `delete=${afterDelete.length} undo=${afterUndo.length}`)
    await setCamera(page)

    // ── 3. add devices from the catalog ───────────────────────────────────────
    let ids = (await partsOf(page)).map((p) => p.id)
    const beltA = await placeFromCatalog(page, 'Equipment', 'Roller Conveyor', -2.5, 0, 0, ids)
    check('place Roller Conveyor #1 from catalog', !!beltA)
    ids = (await partsOf(page)).map((p) => p.id)
    const beltB = await placeFromCatalog(page, 'Equipment', 'Roller Conveyor', 1.8, 0, 0, ids)
    check('place Roller Conveyor #2 from catalog', !!beltB)
    ids = (await partsOf(page)).map((p) => p.id)
    const spawner = await placeFromCatalog(page, 'Products', 'Spawner', -4.2, 0, 0, ids)
    check('place Spawner from catalog', !!spawner)
    ids = (await partsOf(page)).map((p) => p.id)
    const despawner = await placeFromCatalog(page, 'Products', 'Despawner', 4.4, 0, 0, ids)
    check('place Despawner from catalog', !!despawner)

    let parts = await partsOf(page)
    log('placed parts:', JSON.stringify(parts.map((p) => ({ id: p.id, pkg: p.packageId, pos: p.position.map((n) => +n.toFixed(2)), ry: +p.rotationY.toFixed(3), len: p.params.length }))))
    check('scene now holds 5 parts (building + 4 devices)', parts.length === 5, `parts=${parts.length}`)
    await setCamera(page)
    await page.evaluate(() => window.__oip.store.clearSelection?.())
    await page.waitForTimeout(300)
    await shot(page, '03-parts-added.png')
    await cleanShot(page, join(OUT, '03b-parts-added-clean.png'))

    // ── 4. select by clicking + move with Shift+G ─────────────────────────────
    let b = (await partsOf(page)).find((p) => p.packageId === 'oip.roller-conveyor' && p.id !== beltA.id)
    const sel = await clickPart(page, b)
    check('clicking a device in the viewport selects it', sel.includes(b.id), JSON.stringify(sel))
    const beforePos = b.position.slice()
    await repositionTo(page, b, 4.2, 2.5, true) // Alt: flat ground move, away from the line
    b = (await partsOf(page)).find((p) => p.id === b.id)
    const moved = dist(beforePos, b.position)
    check('Shift+G drag moves the selected device', moved > 0.5, `moved ${moved.toFixed(2)} m to ${JSON.stringify(b.position.map((n) => +n.toFixed(2)))}`)
    await shot(page, '04-device-moved.png')

    // ── 5. snap it back end-to-end (connection) ───────────────────────────────
    let a = (await partsOf(page)).find((p) => p.id === beltA.id)
    b = (await partsOf(page)).find((p) => p.id === b.id)
    await clickPart(page, b)
    await repositionTo(page, b, 1.5, 0, false)
    a = (await partsOf(page)).find((p) => p.id === a.id)
    b = (await partsOf(page)).find((p) => p.id === b.id)
    let s = seam(a, b)
    log('seam after snap-back:', JSON.stringify(s), 'B pos', JSON.stringify(b.position.map((n) => +n.toFixed(3))))
    check('second conveyor snaps end-to-end to the first (geometry adjacency)', s && s.gap < 0.08 && s.dot > 0.99, JSON.stringify(s))
    check('snapped conveyor adopted the first bed’s stand height (same Y)', Math.abs(a.position[1] - b.position[1]) < 0.02, `yA=${a.position[1]} yB=${b.position[1]}`)
    await page.evaluate(() => window.__oip.store.clearSelection?.())
    await page.waitForTimeout(300)
    await shot(page, '05-conveyors-connected.png')
    await cleanShot(page, join(OUT, '05b-conveyors-connected-clean.png'))

    // ── 6. edit a parameter in the Inspector ─────────────────────────────────
    await clickPart(page, b)
    const row = page.locator('.inspector .prop-row[data-param="length"]').first()
    await row.waitFor({ timeout: 10_000 })
    const input = row.locator('input').first()
    await input.fill('3')
    await input.press('Enter')
    await page.waitForTimeout(700)
    b = (await partsOf(page)).find((p) => p.id === b.id)
    check('Inspector edit changes the conveyor length param', Math.abs(b.params.length - 3) < 1e-6, `length=${b.params.length}`)
    await shot(page, '06-param-edited.png')

    // length change breaks the seam; reposition it to butt again
    await repositionTo(page, b, 1.0, 0, false)
    a = (await partsOf(page)).find((p) => p.id === a.id)
    b = (await partsOf(page)).find((p) => p.id === b.id)
    s = seam(a, b)
    check('resized conveyor re-snaps end-to-end', s && s.gap < 0.08 && s.dot > 0.99, JSON.stringify(s))
    await page.evaluate(() => window.__oip.store.clearSelection?.())
    await page.waitForTimeout(300)
    await shot(page, '07-resnapped-after-resize.png')

    // ── 7. save (Ctrl+S) and reload ──────────────────────────────────────────
    await page.keyboard.press('Control+s')
    await page.waitForTimeout(1200)
    const savedParts = await partsOf(page)
    await shot(page, '08-saved.png')

    await page.reload()
    await waitForApp(page)
    await page.waitForTimeout(2500)
    let reloaded = await partsOf(page)
    if (reloaded.length < 4) {
      // boot may not reopen the last document in this environment: open it from the projects list, real UI
      const { openProjectNamed } = await import('./_projectMenu.mjs')
      await openProjectNamed(page, NAME)
      await waitForApp(page)
      await page.waitForTimeout(2000)
      reloaded = await partsOf(page)
    }
    check('project survives save + full page reload', reloaded.length === savedParts.length && reloaded.length >= 5, `before=${savedParts.length} after=${reloaded.length}`)
    const rb = reloaded.find((p) => p.id === b.id)
    const ra = reloaded.find((p) => p.id === a.id)
    check('edited params survive reload', rb && Math.abs(rb.params.length - 3) < 1e-6, `length=${rb?.params.length}`)
    const rs = ra && rb ? seam(ra, rb) : null
    check('positions/snap relationship survive reload', rs && rs.gap < 0.08, JSON.stringify(rs))
    await setCamera(page)
    await page.evaluate(() => window.__oip.store.clearSelection?.())
    await page.waitForTimeout(300)
    await shot(page, '09-after-reload.png')
    await cleanShot(page, join(OUT, '09b-after-reload-clean.png'))

    // ── 8. scene JSON snapshot (what a downstream exporter sees) ─────────────
    const snapshot = await page.evaluate(() => {
      const s = window.__oip.store
      try {
        return s.snapshotProject()
      } catch (e) {
        const st = s.getState()
        return { name: st.projectName, scene: st.scene }
      }
    })
    writeFileSync(join(OUT, 'scene-snapshot.json'), JSON.stringify(snapshot, null, 2))
    const scene = snapshot.scene
    check('scene JSON has version/parts', !!scene && Array.isArray(scene.parts) && typeof scene.version === 'number', `version=${scene?.version} parts=${scene?.parts?.length}`)
    const device = scene.parts.find((p) => p.type === 'part')
    check(
      'scene JSON carries id/type/packageId/position/rotationY/params',
      !!device && typeof device.id === 'string' && typeof device.packageId === 'string' && Array.isArray(device.position) && typeof device.rotationY === 'number' && !!device.params,
      JSON.stringify({ id: device?.id, packageId: device?.packageId, pos: device?.position, ry: device?.rotationY }),
    )
    const projectPackages = snapshot.packages ?? scene.packages
    check('project JSON carries the part packages (definitions kept once, beside the scene)', Array.isArray(projectPackages) && projectPackages.length > 0, `packages=${projectPackages?.length}`)
    check('project JSON is a manifest over scene + parts (layout-only project has no stProgram, by design)', typeof snapshot.name === 'string' && !!scene && Array.isArray(projectPackages), Object.keys(snapshot).join(','))
    check('scene JSON stores NO explicit connection list (connections are derived)', !('connections' in scene) && !scene.parts.some((p) => 'connections' in p || 'connectedTo' in p), Object.keys(scene).join(','))

    // ── 9. export the project to .oip through the UI ─────────────────────────
    const downloadPromise = page.waitForEvent('download', { timeout: 20_000 }).catch(() => null)
    await runProjectMenu(page, 'file.exportProject').catch(() => {})
    await page.waitForTimeout(800)
    const dialog = page.locator('[data-testid="export-project-dialog"]')
    if (await dialog.count()) {
      await shot(page, '10-export-dialog.png')
      await page.locator('[data-testid="export-project-confirm"]').click({ force: true })
    }
    const download = await downloadPromise
    if (download) {
      await download.saveAs(join(OUT, 'project-oip-export.oip'))
      check('.oip project export downloads through the UI', true, await download.suggestedFilename())
    } else {
      check('.oip project export downloads through the UI', false, 'no download event')
    }

    // ── 10. Part Editor / SDK entry point in the browser ─────────────────────
    const newPart = page.locator('button', { hasText: /New part/ }).first()
    if (await newPart.count()) {
      await newPart.click({ force: true })
      await page.waitForTimeout(2500)
      await shot(page, '11-part-editor.png')
      const inspText = await page.locator('.inspector').innerText().catch(() => '')
      const partEditorOpen = /new part|behavior|add child node|kind/i.test(inspText) && (await page.locator('canvas').count()) > 0
      check('browser Part Editor opens for custom parts (rig + behavior entry visible)', partEditorOpen, inspText.split('\n').filter(Boolean).slice(0, 4).join(' | '))
    } else {
      check('browser Part Editor opens for custom parts', false, 'New part button missing')
    }
  } finally {
    writeFileSync(join(OUT, 'browser-results.json'), JSON.stringify({ results, consoleErrors }, null, 2))
    await browser.close()
  }
  return checks.length
}

main()
  .then((failed) => {
    log(`\n${results.filter((r) => r.ok).length}/${results.length} checks passed`)
    process.exit(failed ? 1 : 0)
  })
  .catch((e) => {
    console.error('audit crashed:', e)
    process.exit(2)
  })
