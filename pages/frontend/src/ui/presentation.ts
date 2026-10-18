/// <reference types="vite/client" />
type FieldPresentation = {label?: string; width?: 'short'|'medium'|'long'; input?: 'time'; order?: number; advanced?: boolean; hint?:string; reset?:'replace-default'; booleanLabels?:{'true':string;'false':string}};
type GroupPresentation = {title?: string};
type CredentialPresentation = {label?: string};
type Presentation = {owner: string; saveLabel?: string; groups?: Record<string, GroupPresentation>; fields?: Record<string, FieldPresentation>; credentials?: Record<string, CredentialPresentation>};

// Build-time decoration only. This never registers a module, field, route or grant.
const discovered = import.meta.glob<Presentation>('../../../../modules/*/pages/src/presentation.json', {eager: true, import: 'default'});
const registry = new Map(Object.values(discovered).map(value => [value.owner, value]));
registry.set('game_link/core', {owner: 'game_link/core', saveLabel: '保存全局设置', groups: {'': {title: '查询默认值'}}, fields: {default_region: {label: '默认区域', width: 'short'}}});
export const ownerPresentation = (owner: string) => registry.get(owner);
export const fieldPresentation = (field: {module_id:string;name:string}) => registry.get(field.module_id)?.fields?.[field.name] || {};
export const groupTitle = (owner:string, group:string) => (group==='reply_style'?'回复样式':registry.get(owner)?.groups?.[group]?.title || group || '普通设置');
export const credentialLabel = (field:{module_id:string;name:string;description:string}) => registry.get(field.module_id)?.credentials?.[field.name]?.label || field.description.split('；')[0] || field.name;
