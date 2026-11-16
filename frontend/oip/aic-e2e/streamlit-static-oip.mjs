import { chromium } from 'playwright'
const OUT='D:/AI数字工厂-交付/_e2e_data/shots'
const sleep=ms=>new Promise(r=>setTimeout(r,ms))
const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'})
const page=await browser.newPage({viewport:{width:1600,height:1050}})
const errors=[]; page.on('pageerror',e=>errors.push(String(e))); page.on('console',m=>{if(m.type()==='error')errors.push(m.text())})
await page.goto('http://127.0.0.1:8502',{waitUntil:'domcontentloaded'})
await page.waitForSelector('text=AIC FACTORY',{timeout:60000}); await sleep(3000)
await page.locator('label').filter({hasText:'建模分析工作台'}).first().click({force:true})
await sleep(10000)
const iframe=page.locator('iframe').first()
const src=await iframe.getAttribute('src'); console.log('iframe',src)
const frame=page.frames().find(f=>f.url().includes('/app/'))
console.log('frame url', frame?.url())
await sleep(8000)
if (frame) {
  const parts=await frame.evaluate(()=>window.__oip?.store?.getState()?.scene?.parts?.length)
  console.log('OIP parts',parts)
  console.log('pill', (await frame.locator('#aic-sync-pill').innerText().catch(()=> 'MISSING')).replace(/\n/g,' | '))
  const btn=frame.locator('#aic-sync-pill button')
  if (await btn.count()) { await btn.evaluate(el=>el.click()); await sleep(2500); console.log('pill after sync',(await frame.locator('#aic-sync-pill').innerText()).replace(/\n/g,' | ')) }
}
await page.screenshot({path:`${OUT}/streamlit-14-static-oip.png`,fullPage:true})
console.log('errors',errors.length,errors.slice(0,5))
await browser.close()
