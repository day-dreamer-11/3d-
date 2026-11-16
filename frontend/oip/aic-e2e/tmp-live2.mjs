import { chromium } from 'playwright'
const sleep=ms=>new Promise(r=>setTimeout(r,ms))
const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'})
const page=await browser.newPage({viewport:{width:1600,height:1050}})
const errors=[];page.on('pageerror',e=>errors.push(String(e)));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())})
await page.goto('http://127.0.0.1:8502',{waitUntil:'domcontentloaded'})
await page.waitForSelector('text=AIC FACTORY',{timeout:90000});await sleep(3000)
await page.locator('div[role="radiogroup"] label').filter({hasText:'场景编辑器'}).first().click({force:true});await sleep(5000)
await page.locator('[data-testid="stSelectbox"]').nth(0).click();await sleep(600)
await page.getByRole('option',{name:'OIP 开源示例产线'}).first().click();await sleep(3500)
await page.locator('[data-testid="stSelectbox"]').nth(1).click();await sleep(600)
const opts=await page.getByRole('option').allInnerTexts();const anim=opts.find(o=>o.includes('可动画'))
if(anim) await page.getByRole('option',{name:anim}).first().click()
await sleep(9000)
const frame=page.frames().find(f=>f.url().includes('/app/'))
for (let i=0;i<3;i++){
  await sleep(3000)
  console.log(JSON.stringify(await frame.evaluate(()=>{
    const st=window.__oip?.store?.getState?.();const p=document.querySelector('#aic-live-panel')
    return {sel:st?.selectedIds, running:st?.running, panel:p?getComputedStyle(p).display:'none', text:p?p.innerText.split('\n').slice(0,4).join(' | '):''}
  })))
}
await page.screenshot({path:'D:/AI数字工厂-交付/_e2e_data/shots/live-hud2.png',fullPage:true})
console.log('errors',errors.length,errors.slice(0,3))
await browser.close()
