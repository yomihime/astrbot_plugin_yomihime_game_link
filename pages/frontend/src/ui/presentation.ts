/// <reference types="vite/client" />
import {coreText} from '../i18n/messages';
type FieldPresentation = {label?: string; width?: 'short'|'medium'|'long'; input?: 'time'; order?: number; advanced?: boolean; hint?:string; reset?:'replace-default'; booleanLabels?:{'true':string;'false':string}; optionLabels?:Record<string,string>};
// Core-only decoration. Module pages own their business labels and controls.
export function fieldPresentation(field: {module_id:string;name:string}, locale?: string): FieldPresentation {
  if (field.module_id === 'game_link/core' && field.name === 'page_locale') {
    return {label: coreText(locale, 'settings.language'), hint: coreText(locale, 'settings.languageHint'),
      optionLabels: {auto: coreText(locale, 'settings.languageAuto'), 'zh-CN': '简体中文',
        'zh-TW': '繁體中文', 'ja-JP': '日本語', 'en-US': 'English'}};
  }
  if (field.module_id === 'game_link/core' && field.name === 'image_theme') {
    return {label: coreText(locale, 'settings.imageTheme')};
  }
  return {};
}
export function groupTitle(group: string, locale: string): string {
  if (group === 'reply_style') return coreText(locale, 'settings.replyStyle');
  if (group === 'page_appearance') return coreText(locale, 'settings.pageAppearance');
  return group || coreText(locale, 'settings.general');
}
export const credentialLabel = (field:{module_id:string;name:string;description:string}) => field.description.split('；')[0] || field.name;
