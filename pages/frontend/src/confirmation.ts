import {h, shallowRef, nextTick} from 'vue';
import {NCard, NButton} from 'naive-ui';
export type ConfirmationRequest={kind:'navigation'|'module-draft'|'unload';title:string;message:string;confirmLabel:string;isCurrent():boolean;canRestoreFocus?():boolean;owner?:string;trigger?:HTMLElement|null};
type Pending=ConfirmationRequest&{id:number;resolve(value:boolean):void};
// One owned, non-modal confirmation for the existing shell guards.
export function createInlineConfirmation(document:Document) {
  const pending=shallowRef<Pending|null>(null);let serial=0,closed=false,owned:HTMLElement|null=null;
  function settle(choice:Pending,value:boolean,restoreFocus=false) {
    if(pending.value!==choice)return;
    const ownedFocus=!!owned?.contains(document.activeElement);pending.value=null;choice.resolve(value&&choice.isCurrent());
    // Wait for the caller's awaiting-state render, then restore only this still
    // current choice and only if the user has not focused elsewhere meanwhile.
    if(restoreFocus&&ownedFocus)void nextTick().then(()=>nextTick()).then(()=>{
      if(!closed&&serial===choice.id&&!pending.value&&(choice.canRestoreFocus??choice.isCurrent)()&&document.activeElement===document.body&&choice.trigger?.isConnected)choice.trigger.focus({preventScroll:true});
    });
  }
  function cancel(restoreFocus=false){if(pending.value)settle(pending.value,false,restoreFocus);}
  return {
    ask(request:ConfirmationRequest):Promise<boolean>{
      cancel();if(closed||!request.isCurrent())return Promise.resolve(false);
      return new Promise(resolve=>{const choice={...request,id:++serial,trigger:request.trigger??document.activeElement as HTMLElement|null,resolve};pending.value=choice;
        void nextTick().then(()=>{if(pending.value===choice&&choice.isCurrent())owned?.querySelector<HTMLElement>('[data-confirm-cancel]')?.focus({preventScroll:true});});});
    },cancel,invalidate(){if(pending.value&&!pending.value.isCurrent())cancel();},dispose(){cancel();closed=true;},
    render(){const choice=pending.value;if(!choice)return null;
      return h('section',{key:choice.id,role:'group','aria-label':choice.title,'data-unload-confirm':choice.kind==='unload'?choice.owner:undefined,'data-confirmation':choice.kind,ref:(node:unknown)=>{owned=node as HTMLElement|null;},onKeydown:(event:KeyboardEvent)=>{if(event.key==='Escape'){event.preventDefault();settle(choice,false,true);}}},[
        h(NCard,{title:choice.title},{default:()=>[h('p',choice.message),
          h(NButton,{'data-action':'confirm-'+choice.kind,onClick:()=>settle(choice,true)},()=>choice.confirmLabel),
          h(NButton,{'data-action':'cancel-'+choice.kind,'data-confirm-cancel':'',onClick:()=>settle(choice,false,true)},()=> '取消'),
        ]}),
      ]);
    },
  };
}
export type InlineConfirmation=ReturnType<typeof createInlineConfirmation>;
