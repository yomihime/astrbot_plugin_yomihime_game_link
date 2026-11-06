import zhCN from './zh-CN.json';
import zhTW from './zh-TW.json';
import jaJP from './ja-JP.json';
import enUS from './en-US.json';
import {translateUi, type UiParams} from './locale';

export type CoreTextKey = keyof typeof zhCN;
const coreMessages = {'zh-CN': zhCN, 'zh-TW': zhTW, 'ja-JP': jaJP, 'en-US': enUS};

export function coreText(locale: unknown, key: string, params?: UiParams): string {
  return translateUi(coreMessages, locale, key, params);
}
