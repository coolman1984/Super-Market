/* Runs actual frontend functions in a tiny DOM substitute, not a browser QA run.
   Run from repo: node docs/audit/reproduce_ui_review.js */
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class N {
  constructor(tag='text',text='') {this.tag=tag;this._text=text;this.children=[];this.style={};this.value='';this.classList={add(){},remove(){}};}
  set textContent(v){this._text=String(v);this.children=[];}
  get textContent(){return this._text+this.children.map(x=>x.textContent).join('');}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;this._text='';}
  addEventListener(){} removeEventListener(){} focus(){} setAttribute(){}
  toString(){return '[object HTMLElement]';}
}
const ctx={Node:N,console,setTimeout,clearTimeout,document:{
  createElement:tag=>new N(tag),createElementNS:(ns,tag)=>new N(tag),createTextNode:s=>new N('text',s),
  addEventListener(){},removeEventListener(){},body:new N('body')},crypto:require('node:crypto').webcrypto};
ctx.window=ctx;vm.createContext(ctx);
vm.runInContext(fs.readFileSync('web/js/core.js','utf8'),ctx);
const s=ctx.SM;s.i18n={ar:{},en:{}};s.views={};
const broken=s.h('dl',{},[[s.h('dt',{text:'Field'}),s.h('dd',{text:'Value'})]]);
assert.equal(broken.textContent,'[object HTMLElement],[object HTMLElement]');
console.log(JSON.stringify({id:'R16',observed:{nested_description_list:broken.textContent,expected:'FieldValue'}}));
s.scanner={field:()=>s.h('input'),beep(){},handler:null};
s.toast=()=>{};s.state.user={id:1};
s.api=async(method,path)=>path==='/api/shift'?{shift:{id:1}}:
  {ok:true,product:{id:1,name_ar:'PRIVATE-CART',name_en:'PRIVATE-CART',measure:'unit',price:1000},qty:1,scan:{code:'CODE',kind:'code'}};
vm.runInContext(fs.readFileSync('web/js/views/sell.js','utf8'),ctx);
(async()=>{
  const first=new N('root');await s.views.sell.mount(first);
  await s.scanner.handler('CODE');
  assert.ok(first.textContent.includes('PRIVATE-CART'));
  s.views.sell.unmount();s.state.user={id:2};
  const second=new N('root');await s.views.sell.mount(second);
  assert.ok(second.textContent.includes('PRIVATE-CART'));
  console.log(JSON.stringify({id:'R17',observed:{previous_user_cart_visible_to_new_user:true}}));
  const sale={no:'S-OLD',at:'2000-01-01T00:00:00+02:00',total:1000,lines:[],payments:[]};
  const receipt=s.receipt(sale).textContent;
  assert.ok(!receipt.includes('2000'));
  console.log(JSON.stringify({id:'R18',observed:{saved_sale_time_ignored:!receipt.includes('2000'),receipt_uses_browser_now:true}}));
})().catch(e=>{console.error(e);process.exitCode=1;});
