import type {ObjectDirective} from 'vue';

type SelectionAttrs = {id:string;labelId:string;menuId:string;expanded:boolean;invalid:boolean;errorId:string};
// Naive 2.45.3 has no selectionProps for its nonfilterable focusable div.
// Vue's directive lifecycle adapts that locked node without changing its keyboard handler.
export function createSelectA11y(container:HTMLElement): ObjectDirective<HTMLElement,SelectionAttrs> {
  const owners = new WeakMap<HTMLElement,{sync:(attrs:SelectionAttrs)=>void;observer:MutationObserver}>();
  return {
    mounted(element,binding) {
      let attrs=binding.value;
      const apply=()=>{
        const selection=element.querySelector<HTMLElement>('.n-base-selection-label[tabindex]');
        if (!selection) return;
        selection.id=attrs.id;
        selection.setAttribute('role','combobox');
        selection.setAttribute('aria-labelledby',attrs.labelId);
        selection.setAttribute('aria-haspopup','listbox');
        selection.setAttribute('aria-expanded',String(attrs.expanded));
        selection.setAttribute('aria-invalid',String(attrs.invalid));
        selection.setAttribute('aria-describedby',attrs.errorId);
        const menu=container.querySelector<HTMLElement>(`#${attrs.menuId}`);
        if (menu) selection.setAttribute('aria-controls',attrs.menuId);
        else selection.removeAttribute('aria-controls');
        const pending=attrs.expanded ? menu?.querySelector<HTMLElement>('.n-base-select-option--pending[role=option][id]') : null;
        if (pending) selection.setAttribute('aria-activedescendant',pending.id);
        else selection.removeAttribute('aria-activedescendant');
      };
      const observer=new container.ownerDocument.defaultView!.MutationObserver(apply);
      observer.observe(container,{subtree:true,childList:true,attributes:true,attributeFilter:['class']});
      owners.set(element,{sync:next=>{attrs=next;apply();},observer});
      apply();
    },
    updated(element,binding) {owners.get(element)?.sync(binding.value);},
    beforeUnmount(element) {owners.get(element)?.observer.disconnect();owners.delete(element);},
  };
}
