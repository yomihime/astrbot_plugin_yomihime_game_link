/// <reference types="vite/client" />
type FieldPresentation = {label?: string; width?: 'short'|'medium'|'long'; input?: 'time'; order?: number; advanced?: boolean; hint?:string; reset?:'replace-default'; booleanLabels?:{'true':string;'false':string}; optionLabels?:Record<string,string>};
type GroupPresentation = {title?: string};
type CredentialPresentation = {label?: string};
type Presentation = {owner: string; saveLabel?: string; groups?: Record<string, GroupPresentation>; fields?: Record<string, FieldPresentation>; credentials?: Record<string, CredentialPresentation>};

// Core-only decoration. Module pages own their business labels and controls.
const registry = new Map<string,Presentation>();
registry.set('game_link/core', {owner: 'game_link/core', saveLabel: '保存全局设置', groups: {'': {title: '普通设置'}}});
export const ownerPresentation = (owner: string) => registry.get(owner);
export const fieldPresentation = (field: {module_id:string;name:string}) => registry.get(field.module_id)?.fields?.[field.name] || {};
export const groupTitle = (owner:string, group:string) => (group==='reply_style'?'回复样式':registry.get(owner)?.groups?.[group]?.title || group || '普通设置');
export const credentialLabel = (field:{module_id:string;name:string;description:string}) => registry.get(field.module_id)?.credentials?.[field.name]?.label || field.description.split('；')[0] || field.name;
