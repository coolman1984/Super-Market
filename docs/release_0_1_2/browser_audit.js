/* All-page, all-role audit against a disposable local server; does not patch the app. */
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const base = process.argv[2] || 'http://127.0.0.1:8891';
const out = process.argv[3] || '/tmp/auditd-browser';
fs.mkdirSync(out, { recursive: true });
const views = ['today','sell','returns','cash','receive','stock','prepare','count','reports','parties','catalog','scanner','settings','help'];
const result = { source: '15985263e298dee1d6b4ef394423e4a4eed46a79', pages: [], roles: [], dialogs: [], errors: [], requests500: [], findings: [], interactions: [] };
let browser;
function save() { fs.writeFileSync(path.join(out,'browser-results.json'), JSON.stringify(result,null,2)); }
async function login(role, username=role, password='training-only') {
  const context = await browser.newContext({ viewport:{width:1366,height:900} });
  const page = await context.newPage();
  page.on('pageerror', e => { result.errors.push({role,view:page.url().split('#')[1],message:e.message}); save(); });
  page.on('response', r => { if(r.status()>=500) result.requests500.push({role,status:r.status(),url:r.url().replace(base,'')}); });
  page.on('dialog', async d => { result.dialogs.push({role,view:page.url().split('#')[1],type:d.type(),text:d.message()}); await d.dismiss(); });
  await page.goto(base);
  await page.evaluate(async ({username,password}) => SM.api('POST','/api/login',{username,password,workstation:SM.workstation()}),{username,password});
  await page.reload();
  await page.waitForFunction(() => !!document.querySelector('.sidebar') || !!document.querySelector('input[autocomplete="new-password"]'));
  if (!(await page.locator('.sidebar').count())) {
    await page.evaluate(() => SM.api('POST','/api/account/password',{old:'training-only',new:'audit-role-password-123'}));
    await page.reload(); await page.waitForSelector('.sidebar');
  }
  return {context,page};
}
async function navigate(page,view) {
  await page.evaluate(v=>SM.go(v),view);
  await page.waitForFunction(() => !document.querySelector('#view .skeleton') && !!document.querySelector('#view .page'));
  await page.waitForTimeout(180);
  return page.evaluate(()=>SM.state.view);
}
async function snapshot(page, view, variant, capture=true) {
  const actual=await navigate(page,view);
  const data=await page.evaluate(()=>{
    const root=document.querySelector('#view');
    const r=root.getBoundingClientRect();
    const txt=root.innerText;
    const missing=[...new Set(txt.match(/\b(?:err|help|nav|set|party|scn)\.[a-z_0-9.]+\b/g)||[])];
    const overflowing=(selector)=>[...root.querySelectorAll(selector)].filter(el=>{
      const b=el.getBoundingClientRect();
      if(!b.width||!b.height) return false;
      const clip=el.closest('.card'); if(!clip) return false;
      const c=clip.getBoundingClientRect();
      return b.left<c.left-2||b.right>c.right+2;
    }).slice(0,8).map(el=>({text:el.innerText?.slice(0,60),tag:el.tagName,left:el.getBoundingClientRect().left}));
    const buttonOverflow=overflowing('button'),fieldOverflow=overflowing('input,select,textarea');
    return {title:root.querySelector('h1')?.innerText,textLength:txt.length,missingKeys:missing,
      rootLeft:r.left,rootRight:r.right,viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,
      buttonOverflow,fieldOverflow,hasPageError:txt.includes('Traceback')||txt.includes('TypeError:')};
  });
  const name=`${variant}-${view}.png`;
  if(capture) await page.screenshot({path:path.join(out,name),fullPage:true,animations:'disabled',mask:[page.locator('[data-guide="set.password"]'),page.locator('input[type=password]')]});
  result.pages.push({view,actual,variant,...data,screenshot:capture?name:null});
  if(data.missingKeys.length||data.buttonOverflow.length||data.fieldOverflow.length||data.hasPageError)
    result.findings.push({kind:'page_observation',view,variant,missingKeys:data.missingKeys,buttonOverflow:data.buttonOverflow,fieldOverflow:data.fieldOverflow,hasPageError:data.hasPageError});
  save();
}
(async()=>{
  browser=await chromium.launch();
  const {page,context}=await login('owner');
  // Additional roles use known fictional credentials, never external accounts.
  await page.evaluate(async()=>{
    for(const role of ['receiver','accountant','viewer']) await SM.api('POST','/api/users',{
      username:'audit_'+role,name:'Audit '+role,role,password:'training-only'});
  });
  for(const spec of [{tag:'ar-desktop',width:1366,height:900,lang:'ar',theme:'daylight'},
                     {tag:'ar-tablet',width:768,height:1024,lang:'ar',theme:'daylight'},
                     {tag:'ar-phone',width:390,height:844,lang:'ar',theme:'daylight'},
                     {tag:'en-dark',width:1366,height:900,lang:'en',theme:'night'}]){
    await page.setViewportSize({width:spec.width,height:spec.height});
    await page.evaluate(s=>{SM.prefs.set('lang',s.lang);SM.prefs.set('theme',s.theme);SM.applyPrefs();SM.renderShell();},spec);
    await page.waitForTimeout(350);
    for(const v of views) await snapshot(page,v,spec.tag);
  }
  // Real forms: invalid scan, embedded markup, double click, pending payment.
  await page.evaluate(()=>{SM.prefs.set('lang','ar');SM.prefs.set('theme','daylight');SM.applyPrefs();SM.renderShell();});
  await navigate(page,'scanner');
  const scanControls=await page.locator('#view input').count();
  result.interactions.push({name:'scanner_page_controls',count:scanControls});
  // Probe server robustness from the same browser's authenticated origin.
  const apiProbe=await page.evaluate(async()=>{
    const paths=['/api/quote','/api/sales','/api/receipts','/api/production','/api/supplier-credit-notes'];
    const types=[null,[],{},'text',42]; const out=[];
    for(const p of paths)for(const payload of types){
      const response=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json','X-SM':'1'},body:JSON.stringify({key:SM.newKey(),payload})});
      out.push({path:p,type:payload===null?'null':Array.isArray(payload)?'array':typeof payload,status:response.status});
    }
    return out;
  });
  result.interactions.push({name:'malformed_payloads',results:apiProbe});
  const burst=await page.evaluate(async()=>{
    const product=await SM.api('POST','/api/products',{code:'AUD-RACE',name_ar:'صنف اختبار التزامن',name_en:'Concurrent sale item',kind:'grocery',measure:'unit',price:1500});
    const loc=(await SM.api('GET','/api/locations')).find(l=>l.kind==='shelf');
    await SM.command('/api/receipts',SM.newKey(),{lines:[{product_id:product.id,qty:10,line_cost:1000,location_id:loc.id}]});
    await SM.command('/api/shift/open',SM.newKey(),undefined,{opening_float:0});
    const outcomes=await Promise.all(Array.from({length:40},async()=>{
      const key=SM.newKey(),payload={lines:[{product_id:product.id,qty:1}]};
      payload.quote_hash=(await SM.api('POST','/api/quote',{payload})).quote_hash;
      try{return {status:200,sale:await SM.command('/api/sales',key,payload),key,payload};}
      catch(e){return {status:e.status,error:e.code};}
    }));
    const sold=outcomes.filter(x=>x.status===200),rejections=outcomes.filter(x=>x.status!==200);
    for(const x of sold)await SM.command('/api/sales',x.key,x.payload);
    return {requests:40,sold:sold.length,rejections:rejections.map(x=>({status:x.status,error:x.error})),
      remaining:(await SM.api('GET','/api/products/'+product.id)).available,
      reconcile:(await SM.api('GET','/api/reconcile')).ok};
  });
  result.interactions.push({name:'concurrent_http_sales_and_replays',...burst});
  await page.setViewportSize({width:1366,height:900});await page.waitForTimeout(350);
  await navigate(page,'catalog');
  await page.locator('#view .tbl tbody tr').first().click();await page.waitForSelector('.drawer');
  const priceText=await page.evaluate(()=>SM.t('cat.change_price'));
  await page.locator('.drawer').getByRole('button',{name:priceText,exact:true}).click();
  if(await page.locator('.dialog').count()){ result.interactions.push({name:'price_uses_app_dialog',passed:true}); await page.locator('.dialog > header button').click(); }
  await page.locator('.drawer-head button').click();
  // XSS and long names are fictional records. No script is executed by the harness.
  await page.evaluate(async()=>{
    await SM.api('POST','/api/products',{code:'AUD-XSS',name_ar:'<img src=x onerror="window.__auditXss=1">',name_en:'XSS probe',kind:'grocery',measure:'unit',price:1000});
    await SM.api('POST','/api/products',{code:'AUD-LONG',name_ar:'اسم صنف طويل لاختبار التفاف النص '.repeat(3),name_en:'Long product name '.repeat(5),kind:'grocery',measure:'unit',price:1000});
  });
  await page.setViewportSize({width:390,height:844});await page.waitForTimeout(350);
  await snapshot(page,'catalog','ar-phone-long-names');
  result.interactions.push({name:'xss_text_render',executed:await page.evaluate(()=>!!window.__auditXss)});
  await context.close();
  for(const [role,user] of [['manager','manager'],['cashier','cashier'],['receiver','audit_receiver'],['butcher','butcher'],
                           ['counter','counter'],['accountant','audit_accountant'],['viewer','audit_viewer']]){
    const {page:p,context:c}=await login(role,user);
    const links=await p.locator('.nav a').evaluateAll(els=>els.map(e=>e.dataset.view));
    const routeChecks=[];
    for(const v of views){
      const actual=await navigate(p,v);
      const info=await p.evaluate(()=>({title:document.querySelector('#view h1')?.textContent,length:document.querySelector('#view')?.innerText.length}));
      routeChecks.push({view:v,actual,advertised:links.includes(v),...info});
    }
    const probes=await p.evaluate(async()=>{
      const paths=['/api/users','/api/settings','/api/backup/status','/api/reports/day','/api/stock','/api/parties?kind=supplier'];
      const out=[];for(const path of paths){const r=await fetch(path);out.push({path,status:r.status});}return out;
    });
    await p.screenshot({path:path.join(out,`role-${role}.png`),fullPage:true,animations:'disabled'});
    result.roles.push({role,advertisedViews:links,routeChecks,apiAccess:probes});save();await c.close();
  }
  save(); console.log(JSON.stringify({pages:result.pages.length,roles:result.roles.length,errors:result.errors,requests500:result.requests500,observations:result.findings.length}));
})().catch(e=>{result.fatal={message:e.message,stack:e.stack};save();console.error(e);process.exitCode=1;})
  .finally(async()=>{if(browser)await browser.close();});
