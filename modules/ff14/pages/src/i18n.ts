import {translateUi} from '@game-link/ui';
import zhCN from './i18n/zh-CN.json';
import zhTW from './i18n/zh-TW.json';
import jaJP from './i18n/ja-JP.json';
import enUS from './i18n/en-US.json';

const catalogs = {'zh-CN': zhCN, 'zh-TW': zhTW, 'ja-JP': jaJP, 'en-US': enUS};

export function ff14Text(locale: string, key: string, params?: Readonly<Record<string, string | number>>): string {
  return translateUi(catalogs, locale, key, params);
}
