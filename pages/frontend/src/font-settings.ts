import type {Bridge} from './contracts';

export interface FontStatus {
  revision:number; state:'checking'|'downloading'|'ready'|'unavailable'|'stopped';
  source:'canonical'|'system'|'none'; error:string|null;
  filename:string; size:number; sha256:string; download_url:string;
}
export interface FontPanel {
  visible:boolean; busy:boolean; enabled:boolean; message:string;
  status:FontStatus|null; progress:string; uploadAvailable:boolean;
  refresh():void; retry():void; upload(file:File|null):void;
}
export function validateFontStatus(value:unknown):FontStatus {
  const v=value as FontStatus;
  if(!v||typeof v!=='object'||!Number.isSafeInteger(v.revision)||v.revision<0||
    !['checking','downloading','ready','unavailable','stopped'].includes(v.state)||
    !['canonical','system','none'].includes(v.source)||
    !(v.error===null||typeof v.error==='string'&&/^[a-z_]{1,80}$/.test(v.error))||
    typeof v.filename!=='string'||!/^[A-Za-z0-9._-]{1,128}\.otf$/.test(v.filename)||
    !Number.isSafeInteger(v.size)||v.size<1||v.size>20*1024*1024||
    typeof v.sha256!=='string'||!/^[a-f0-9]{64}$/.test(v.sha256)||typeof v.download_url!=='string')throw Error('invalid_font_status');
  return {...v};
}
/** Only the official pinned-commit font source may become a clickable link. */
export function fontDownloadUrl(status:FontStatus|null):string|null {
  return status&&/^https:\/\/raw\.githubusercontent\.com\/notofonts\/noto-cjk\/[a-f0-9]{40}\/Sans\/OTF\/SimplifiedChinese\/NotoSansCJKsc-Regular\.otf$/.test(status.download_url)?status.download_url:null;
}
export function fontStatusText(status:FontStatus|null):string {
  if(!status)return '字体状态暂不可用';
  const fallback=status.source==='system'?' · 正在使用系统字体':status.source==='none'?' · 暂无可用图片字体':'';
  if(status.state==='ready'&&status.source==='canonical')return '标准字体已就绪';
  if(status.state==='checking')return '正在检查字体'+fallback;
  if(status.state==='downloading')return '正在下载标准字体'+fallback;
  if(status.error)return '标准字体获取失败'+fallback;
  return (status.source==='system'?'正在使用系统字体':'标准字体暂不可用')+(status.source==='none'?' · 暂无可用图片字体':'');
}

/** Font actions use the existing authenticated bridge, separately from config drafts. */
export function createFontSettings(bridge:Bridge,window:Window,changed:(panel:FontPanel)=>void,accessLost:()=>void){
  let epoch=0,closed=false,timer:number|undefined;
  const panel:FontPanel={visible:false,busy:false,enabled:false,message:'',status:null,progress:'',uploadAvailable:typeof bridge?.upload==='function',refresh:()=>void refresh(),retry:()=>void retry(),upload:file=>void upload(file)};
  const publish=()=>changed({...panel});
  const current=(version:number)=>!closed&&panel.enabled&&epoch===version;
  const stopTimer=()=>{if(timer!==undefined)window.clearTimeout(timer);timer=undefined;};
  const denied=(error:unknown)=>{const e=error as {message?:string;code?:string;status?:number};return ['admin_authorization_denied','stale_context','page_revoked','asset_revoked'].some(code=>e?.message===code||e?.code===code)||e?.status===401||e?.status===403;};
  function failure(error:unknown){panel.progress='';panel.message=denied(error)?'管理授权已失效，请重新打开页面':(error as Error)?.message==='revision_conflict'?'字体状态已变化，请刷新后重试':'字体操作未确认，请刷新状态后重试；可继续使用现有字体';panel.status=null;if(denied(error)){panel.enabled=false;accessLost();}}
  function schedule(){stopTimer();if(panel.enabled&&['checking','downloading'].includes(panel.status?.state||''))timer=window.setTimeout(()=>void refresh(),2000);}
  async function perform(action:()=>Promise<unknown>,progress:string){
    if(closed||!panel.enabled||panel.busy)return;const version=epoch;panel.busy=true;panel.message='';panel.progress=progress;stopTimer();publish();
    try{const result=validateFontStatus(await action());if(!current(version))return;panel.status=result;panel.progress='';}
    catch(error){if(current(version))failure(error);}
    finally{if(current(version)){panel.busy=false;publish();schedule();}else if(epoch===version){panel.busy=false;publish();}}
  }
  async function refresh(){await perform(()=>bridge.apiPost('admin/font-status',{}),'正在读取字体状态…');}
  async function retry(){if(!panel.status||['checking','downloading'].includes(panel.status.state))return;await perform(()=>bridge.apiPost('admin/font-retry',{}),'正在请求重新下载…');}
  async function upload(file:File|null){
    if(!file||!panel.enabled||panel.busy||!panel.status||!bridge.upload||['checking','downloading'].includes(panel.status.state))return;
    if(file.name!==panel.status.filename||file.size!==panel.status.size){panel.message='请选择指定版本的 '+panel.status.filename+'（文件大小必须匹配）';publish();return;}
    await perform(()=>bridge.upload!('admin/font-upload',file),'正在上传并校验标准字体…');
  }
  return {
    sync(visible:boolean,enabled:boolean){const active=visible&&enabled;if(panel.visible===visible&&panel.enabled===active)return;epoch++;stopTimer();panel.visible=visible;panel.enabled=active;panel.busy=false;panel.progress='';panel.message='';panel.status=null;publish();if(active)void refresh();},
    invalidate(){epoch++;stopTimer();panel.enabled=false;panel.busy=false;panel.status=null;panel.progress='';panel.message='';publish();},
    refresh,
    dispose(){closed=true;epoch++;stopTimer();},
  };
}
