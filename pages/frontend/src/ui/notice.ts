// Local status lifetime: cancelled callbacks must never erase newer feedback.
export function createNotice(window:Window,write:(message:string,tone:string)=>void){
 let serial=0,timer:number|null=null;
 const cancel=()=>{serial++;if(timer!==null)window.clearTimeout(timer);timer=null;};
 return {show(message:string,tone='error'){cancel();write(message,tone);if(tone==='success'){const token=serial;timer=window.setTimeout(()=>{if(token!==serial)return;timer=null;write('','neutral');},3500);}},clear(){cancel();write('','neutral');},cancel};
}
