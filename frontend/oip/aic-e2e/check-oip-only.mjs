import { chromium } from 'playwright'
const OUT='D:/AI数字工厂-交付/_e2e_data/shots'
const sleep=ms=>new Promise(r=>setTimeout(r,ms))
const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'})
const page=await browser.newPage({viewport:{width:1600,height:1050}})
const errors=[]; page.on('pageerror',e=>errors.push(String(e))); page.on('console',m=>{if(m.type()==='error')errors.push(m.text())})
await page.goto('http://127.0.0.1:8502',{waitUntil:'domcontentloaded'})
await page.waitForSelector('text=AIC FACTORY',{timeout:60000}); await sleep(3500)
const labels=await page.locator('div[role="radiogroup"] label').allInnerTexts().catch(()=>[])
console.log('nav:', labels.map(x=>x.trim()).filter(Boolean).join(' | '))
for (const legacy of ['工厂搭建','生产线配置','三维仿真','生产数据分析','边缘计算','调度策略实验','AI 优化建议','AI 服务生成']) {
  if (labels.some(x=>x.includes(legacy))) console.log('LEGACY STILL PRESENT:', legacy)
}
async function go(label, shot){
  const el=page.locator('div[role="radiogroup"] label').filter({hasText:label}).first()
  await el.click({force:true}); await sleep(4500)
  const bad=await page.locator('text=页面渲染出现异常').count()
  console.log(label, 'render-error', bad)
  await page.screenshot({path:`${OUT}/oiponly-${shot}.png`,fullPage:true})
}
await go('平台总览','01-overview')
await go('建模分析工作台','02-workspace'); await sleep(7000)
const frame=page.frames().find(f=>f.url().includes('/app/'))
if (frame) console.log('OIP parts', await frame.evaluate(()=>window.__oip?.store?.getState()?.scene?.parts?.length))
await go('实验历史','05-history')
console.log('history table rows', await page.locator('table tbody tr').count())
await go('系统设置','06-system')
console.log('errors',errors.length,errors.slice(0,5))
await browser.close()
