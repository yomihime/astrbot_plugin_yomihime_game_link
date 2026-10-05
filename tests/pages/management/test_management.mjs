// Official bridge payload/context contract, synthetic DOM; no browser acceptance.
import assert from "node:assert/strict";
import {createManagementPage, validateSnapshot, updateBody} from "../../../pages/management/app.js";
const flush = async () => { for (let i=0;i<25;i++) await Promise.resolve(); };
const deferred = () => { let resolve; const promise = new Promise(r => resolve=r); return {promise,resolve}; };
class Node {
  children=[]; listeners=new Map(); disabled=false; value=""; checked=false; textContent="";
  constructor(tag) {this.tagName=tag;}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.children=nodes;}
  setAttribute() {}
  addEventListener(name,callback) {this.listeners.set(name,callback);}
  all() {return [this,...this.children.flatMap(n=>n.all())];}
  click() {if (!this.disabled) this.listeners.get("click")?.();}
}
function doc() {
  const nodes=Object.fromEntries(["status","fields","refresh","rollback","recover","replacement"].map(id=>{const n=new Node(["refresh","rollback","recover"].includes(id)?"button":"div");n.id=id;return [id,n];}));
  return {nodes,getElementById:id=>nodes[id],createElement:tag=>new Node(tag),querySelectorAll:()=>Object.values(nodes).flatMap(n=>n.all()).filter(n=>n.tagName==="button")};
}
function snapshot(revision=1) {
  const field=value=>({value,state:"valid",present:false,source:"default"});
  return {"game_link/core":{revision,fields:{default_region:field("cn")}},"ff14/ff14":{revision,fields:{ff14_calendar_default_days:field(3),ff14_calendar_default_timezone:field("Asia/Shanghai"),ff14_calendar_default_delivery_time:field("09:00")}}};
}
function fixture(existing) {
  const calls=[], ready=deferred();let callback;
  const bridge={onContext(fn){callback=fn;if(existing)fn(existing);return()=>callback=null;},ready:()=>ready.promise,
    apiPost(endpoint,body){const call={endpoint,body,...deferred()};calls.push(call);return call.promise;}};
  return {calls,bridge,ready,context(value){callback?.(value);}};
}
const context={pluginName:"astrbot_plugin_yomihime_game_link",pageName:"management"};
// Context is already present: synchronous onContext and ready resolve the same
// object. There must be one read, and that read must be allowed to render.
{
  const f=fixture(context),d=doc(),app=createManagementPage(d,f.bridge,{addEventListener(){}});
  app.start();f.ready.resolve(context);await flush();assert.equal(f.calls.length,1);
  f.calls[0].resolve(snapshot());await flush();assert.equal(d.nodes.fields.children.length,2);assert.equal(d.nodes.refresh.disabled,false);
  const save=d.nodes.fields.children[0].children.find(n=>n.tagName==="button");
  const select=d.nodes.fields.children[0].children.find(n=>n.tagName==="select");select.value="clear";
  save.click();await flush();assert.deepEqual(f.calls[1].body,{module_id:"game_link/core",expected_revision:1,updates:[{field:"default_region",mode:"clear"}]});
  f.calls[1].resolve({module_id:"game_link/core",revision:2});await flush();assert.equal(f.calls[2].endpoint,"admin/read");
  f.calls[2].resolve(snapshot(2));await flush();assert.equal(f.calls.filter(c=>c.endpoint==="admin/recover").length,0);
  d.nodes.rollback.click();await flush();f.calls[3].resolve({rolled_back:true});await flush();f.calls[4].resolve(snapshot(3));await flush();
  app.close();d.nodes.refresh.click();await flush();assert.equal(f.calls.length,5);
}
// Context arrives later and twice. A real context replacement invalidates the
// pending read and queues a new read; the late old snapshot is never shown.
{
  const f=fixture(null),d=doc(),app=createManagementPage(d,f.bridge,{addEventListener(){}});app.start();
  f.context(context);f.ready.resolve(context);await flush();assert.equal(f.calls.length,1);
  f.context({...context,locale:"en-US"});f.calls[0].resolve(snapshot(9));await flush();assert.equal(f.calls.length,2);assert.equal(d.nodes.fields.children.length,0);
  f.calls[1].resolve(snapshot(2));await flush();assert.equal(d.nodes.fields.children.length,2);
  d.nodes.refresh.click();await flush();f.calls[2].resolve({status:"ok",data:snapshot()});await flush();assert.match(d.nodes.status.textContent,/\u672a\u53d6\u5f97\u6210\u529f\u786e\u8ba4/);
  app.close();
}
assert.throws(()=>validateSnapshot({...snapshot(),"x/y":{}}));
assert.throws(()=>updateBody("x/y",1,{}));
console.log("management bridge/DOM contracts: 3 groups passed");
