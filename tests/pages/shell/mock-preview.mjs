// Browser-only fixture control. No storage, real credentials, Host or upstream calls.
export function installMock(window,catalog,response) {
  const contexts=new Set(), posts=[], gets=[];
  let context={pluginName:'astrbot_plugin_yomihime_game_link',pageName:'shell',sessionGeneration:1,isDark:false,locale:'zh-CN'}, nextCatalog=catalog, nextResponse=response, failNext=false, holdCatalog=false, pendingCatalog=null;
  window.AstrBotPluginPage={ready:()=>Promise.resolve(context),onContext(fn){contexts.add(fn);return ()=>contexts.delete(fn);},apiGet(path,body){gets.push({path,body});if(path!=='catalog')return Promise.reject(Error('fixture_unknown_endpoint'));if(failNext){failNext=false;return Promise.reject(Error('fixture_failure'));}if(holdCatalog)return new Promise((resolve,reject)=>{pendingCatalog={resolve,reject};});return Promise.resolve(nextCatalog);},apiPost(path,body){posts.push({path,body});return Promise.resolve(nextResponse);}};
  window.YGLPreview=Object.freeze({posts,gets,setCatalog(value){nextCatalog=value;},setResponse(value){nextResponse=value;},failCatalog(){failNext=true;},holdCatalog(){holdCatalog=true;},releaseCatalog(){holdCatalog=false;pendingCatalog?.resolve(nextCatalog);pendingCatalog=null;},theme(isDark){context={...context,isDark};for(const fn of contexts)fn(context);},locale(locale){context={...context,locale};for(const fn of contexts)fn(context);},boundary(){context={...context,sessionGeneration:context.sessionGeneration+1};for(const fn of contexts)fn(context);}});
  return window.YGLPreview;
}
