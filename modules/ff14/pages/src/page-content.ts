import type {TemplateContent, TemplateField, TemplateChoice} from '@game-link/ui';
import {ordinaryFields} from './settings-controller';

export const homeContent: TemplateContent = {
  title: '最终幻想 XIV',
  eyebrow: 'FINAL FANTASY XIV / MODULE',
  intro: '欢迎来到最终幻想 XIV。可以在这里查询物品、市场、角色 Logs 与活动日历；需要调整默认值或来源凭据时，请前往业务配置。',
  notes: [
    '查询只在手动提交后执行；缺失、失败与覆盖限制以本次来源结果为准。',
    '主密钥、存储恢复、全局主题和字体由全局设置管理。',
  ],
};

export const settingsNote = '默认值用于后续订阅，已有订阅保留原设置；提醒按调度周期检查，实际送达可能延迟。';

const settingsSections = [
  {id: 'query-reply', title: '查询与回复', names: ['ff14_default_region', 'reply_theme']},
  {id: 'calendar-subscriptions', title: '日历与订阅', names: [
    'ff14_calendar_default_days', 'ff14_calendar_default_timezone', 'ff14_calendar_default_delivery_time',
    'ff14_calendar_sync_interval_seconds', 'ff14_subscriptions_enabled',
  ]},
] as const;

export function settingsSectionsFor(themeAvailable: boolean, themeChoices: readonly TemplateChoice[], selected: string) {
  const controls = new Map(ordinaryFields.map((field): [string, TemplateField] => {
    const base = {id: field.name, label: field.label};
    if (field.name === 'reply_theme') {
      const choices: TemplateChoice[] = [{value: '', label: '跟随全局设置'}, ...themeChoices];
      if (selected && !themeChoices.some(choice => choice.value === selected)) {
        choices.push({value: selected, disabled: true, label: '当前主题不可用，请重新选择'});
      }
      return [field.name, {...base, control: 'select', choices}];
    }
    if ('choices' in field) {
      return [field.name, {...base, control: 'select', choices: field.choices.map(([value, label]) => ({value, label}))}];
    }
    if (field.name === 'ff14_calendar_default_days') return [field.name, {...base, control: 'number', min: 1, max: 30}];
    return [field.name, {...base, control: field.name === 'ff14_calendar_default_delivery_time' ? 'time' : 'text'}];
  }));
  return settingsSections.map(({id, title, names}) => ({
    id, title,
    fields: names.filter(name => name !== 'reply_theme' || themeAvailable).map(name => controls.get(name)!),
  }));
}
