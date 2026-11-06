import type {Bridge} from './contracts';
import {coreText} from './i18n/messages';

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
export function fontStatusText(status:FontStatus|null, locale='zh-CN'):string {
  const text = (key: string) => coreText(locale, key);
  if(!status)return text('font.unavailable');
  const fallback=status.source==='system'?text('font.fallbackSystem'):status.source==='none'?text('font.fallbackNone'):'';
  if(status.state==='ready'&&status.source==='canonical')return text('font.ready');
  if(status.state==='checking')return text('font.checking')+fallback;
  if(status.state==='downloading')return text('font.downloading')+fallback;
  if(status.error)return text('font.sourceFailed')+fallback;
  return (status.source==='system'?text('font.system'):text('font.standardUnavailable'))+(status.source==='none'?text('font.fallbackNone'):'');
}

/** Font actions use the existing authenticated bridge, separately from config drafts. */
export function createFontSettings(bridge:Bridge,window:Window,changed:(panel:FontPanel)=>void,accessLost:()=>void,readFailed:()=>void=()=>{},locale:()=>string=()=> 'zh-CN'){
  let epoch=0,closed=false,timer:number|undefined;
  let messageKey='', progressKey='', filename='';
  const panel:Omit<FontPanel,'message'|'progress'>={visible:false,busy:false,enabled:false,status:null,uploadAvailable:typeof bridge?.upload==='function',refresh:()=>void refresh(),retry:()=>void retry(),upload:file=>void upload(file)};
  function publish(){
    const message=messageKey, progress=progressKey, selectedFile=filename;
    changed({
      ...panel,
      get message(){return message ? coreText(locale(), message, {filename:selectedFile}) : '';},
      get progress(){return progress ? coreText(locale(), progress) : '';},
    });
  }
  const current=(version:number)=>!closed&&panel.enabled&&epoch===version;
  const stopTimer=()=>{if(timer!==undefined)window.clearTimeout(timer);timer=undefined;};
  const denied=(error:unknown)=>{const e=error as {message?:string;code?:string;status?:number};return ['admin_authorization_denied','stale_context','page_revoked','asset_revoked'].some(code=>e?.message===code||e?.code===code)||e?.status===401||e?.status===403;};
  function failure(error:unknown){progressKey='';messageKey=denied(error)?'font.authorizationLost':(error as Error)?.message==='revision_conflict'?'font.conflict':'font.operationUnknown';panel.status=null;if(denied(error)){panel.enabled=false;accessLost();}}
  function schedule(){stopTimer();if(panel.enabled&&['checking','downloading'].includes(panel.status?.state||''))timer=window.setTimeout(()=>void refresh(),2000);}
  async function perform(action:()=>Promise<unknown>,progress:string,reading=false){
    if(closed||!panel.enabled||panel.busy)return;const version=epoch;panel.busy=true;messageKey='';progressKey=progress;stopTimer();publish();
    try{const result=validateFontStatus(await action());if(!current(version))return;panel.status=result;progressKey='';}
    catch(error){if(current(version)){if(reading)readFailed();failure(error);}}
    finally{if(current(version)){panel.busy=false;publish();schedule();}else if(epoch===version){panel.busy=false;publish();}}
  }
  async function refresh(){await perform(()=>bridge.apiPost('admin/font-status',{}),'font.reading',true);}
  async function retry(){if(!panel.status||['checking','downloading'].includes(panel.status.state))return;await perform(()=>bridge.apiPost('admin/font-retry',{}),'font.retrying');}
  async function upload(file:File|null){
    if(!file||!panel.enabled||panel.busy||!panel.status||!bridge.upload||['checking','downloading'].includes(panel.status.state))return;
    if(file.name!==panel.status.filename||file.size!==panel.status.size){filename=panel.status.filename;messageKey='font.invalidFile';publish();return;}
    await perform(()=>bridge.upload!('admin/font-upload',file),'font.uploading');
  }
  return {
    sync(visible:boolean,enabled:boolean){const active=visible&&enabled;if(panel.visible===visible&&panel.enabled===active)return;epoch++;stopTimer();panel.visible=visible;panel.enabled=active;panel.busy=false;progressKey='';messageKey='';panel.status=null;publish();if(active)void refresh();},
    invalidate(){epoch++;stopTimer();panel.enabled=false;panel.busy=false;panel.status=null;progressKey='';messageKey='';publish();},
    refresh,
    dispose(){closed=true;epoch++;stopTimer();},
  };
}
