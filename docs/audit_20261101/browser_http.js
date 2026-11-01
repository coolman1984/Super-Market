const {chromium}=require('playwright');
const fs=require('node:fs');
const base=process.argv[2],output=process.argv[3];
(async()=>{
 const browser=await chromium.launch();
 try{
  const page=await browser.newPage();await page.goto(base);
  await page.fill('input[autocomplete=username]','owner');await page.fill('input[type=password]','training-only');
  await page.click('button[type=submit]');await page.waitForSelector('.sidebar');
  const result=await page.evaluate(async()=>{
   const st=await SM.api('GET','/api/state');
   const loc=(await SM.api('GET','/api/locations')).find(l=>l.kind==='shelf');
   const p=await SM.api('POST','/api/products',{code:'HTTP-RACE',name_ar:'صنف اختبار',name_en:'Test item',kind:'grocery',measure:'unit',price:1500});
   await SM.command('/api/receipts',SM.newKey(),{lines:[{product_id:p.id,qty:10,line_cost:1000,location_id:loc.id}]});
   await SM.command('/api/shift/open',SM.newKey(),undefined,{opening_float:0});
   const line={lines:[{product_id:p.id,qty:1}]};line.quote_hash=(await SM.api('POST','/api/quote',{payload:line})).quote_hash;
   const all=await Promise.all(Array.from({length:40},async()=>{
    const k=SM.newKey();try{const sale=await SM.command('/api/sales',k,line);return{ok:true,k,sale};}
    catch(e){return{ok:false,status:e.status,error:e.code};}
   }));
   const successes=all.filter(x=>x.ok);let replays=0;
   for(const r of successes){const again=await SM.command('/api/sales',r.k,line);if(again.replayed&&again.sale===r.sale.sale)replays++;}
   const report=await SM.api('GET','/api/reports/day');
   const concurrency={attempts:40,succeeded:successes.length,rejected:all.filter(x=>!x.ok).map(x=>({status:x.status,error:x.error})),
    replays,stock:(await SM.api('GET','/api/products/'+p.id)).available,sales_total:report.sales_total,
    reconcile:(await SM.api('GET','/api/reconcile')).ok};
   await SM.command('/api/promotions',SM.newKey(),undefined,{product_id:p.id,buy_qty:2,free_qty:1,starts:st.business_day});
   const single=await SM.api('POST','/api/quote',{payload:{lines:[{product_id:p.id,qty:3}]}});
   const split=await SM.api('POST','/api/quote',{payload:{lines:Array.from({length:3},()=>({product_id:p.id,qty:1}))}});
   const raw=await SM.api('POST','/api/products',{code:'HTTP-MEAT',name_ar:'لحم اختبار',name_en:'Meat test',kind:'raw_meat',measure:'g',price:40000});
   await SM.command('/api/receipts',SM.newKey(),{lines:[{product_id:raw.id,qty:1000,line_cost:10000,location_id:loc.id,expires:'2026-11-15'}]});
   const cart={lines:[{product_id:raw.id,qty:500}]};cart.quote_hash=(await SM.api('POST','/api/quote',{payload:cart})).quote_hash;
   const meatSale=await SM.command('/api/sales',SM.newKey(),cart);
   return {concurrency,promotion:{single_total:single.total,split_total:split.total},raw_shelf:{accepted:true,location_kind:loc.kind,sold_g:500,total:meatSale.total}};
  });
  const counter=await browser.newPage();await counter.goto(base);
  await counter.fill('input[autocomplete=username]','counter');await counter.fill('input[type=password]','training-only');
  await counter.click('button[type=submit]');await counter.waitForSelector('.sidebar');
  const advertised=await counter.locator('.nav a[data-view="stock"]').count();
  await counter.evaluate(()=>SM.go('stock'));await counter.waitForTimeout(400);
  result.counter_stock_navigation={advertised:!!advertised,view:await counter.evaluate(()=>SM.state.view),
    api_status:(await counter.request.get(base+'/api/stock')).status(),
    screen:await counter.locator('#view').innerText(),toast:(await counter.locator('#toasts').allTextContents()).join(' ')};
  fs.writeFileSync(output,JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
