import {uiThemeLabel, type TemplateContent, type TemplateField, type TemplateChoice} from '@game-link/ui';
import {ordinaryFields} from './settings-controller';
import {ff14Text} from './i18n';

export function homeContent(locale: string): TemplateContent {
  return {
    title: ff14Text(locale, 'home.title'),
    eyebrow: 'FINAL FANTASY XIV / MODULE',
    intro: ff14Text(locale, 'home.intro'),
    notes: [ff14Text(locale, 'home.manual'), ff14Text(locale, 'home.globalSettings')],
  };
}

const settingsSections = [
  {id: 'query-reply', titleKey: 'settings.queryReply', names: ['ff14_default_region', 'reply_theme']},
  {id: 'calendar-subscriptions', titleKey: 'settings.calendarSubscriptions', names: [
    'ff14_calendar_default_days', 'ff14_calendar_default_timezone', 'ff14_calendar_default_delivery_time',
    'ff14_calendar_sync_interval_seconds', 'ff14_subscriptions_enabled',
  ]},
] as const;

export function settingsSectionsFor(locale: string, themeAvailable: boolean, themeChoices: readonly TemplateChoice[], selected: string) {
  const controls = new Map(ordinaryFields.map((field): [string, TemplateField] => {
    const base = {id: field.name, label: ff14Text(locale, `settings.field.${field.name}`)};
    if (field.name === 'reply_theme') {
      const choices: TemplateChoice[] = [
        {value: '', label: ff14Text(locale, 'settings.followGlobal')},
        ...themeChoices.map(choice => ({...choice, label: uiThemeLabel(choice.value, choice.label, locale)})),
      ];
      if (selected && !themeChoices.some(choice => choice.value === selected)) {
        choices.push({value: selected, disabled: true, label: ff14Text(locale, 'settings.themeUnavailable')});
      }
      return [field.name, {...base, control: 'select', choices}];
    }
    if ('choices' in field) {
      return [field.name, {...base, control: 'select', choices: field.choices.map(([value, labelKey]) => ({
        value, label: ff14Text(locale, labelKey, labelKey === 'settings.hours' ? {count: Number(value) / 3600} : undefined),
      }))}];
    }
    if (field.name === 'ff14_calendar_default_days') return [field.name, {...base, control: 'number', min: 1, max: 30}];
    return [field.name, {...base, control: field.name === 'ff14_calendar_default_delivery_time' ? 'time' : 'text'}];
  }));
  return settingsSections.map(({id, titleKey, names}) => ({
    id, title: ff14Text(locale, titleKey),
    fields: names.filter(name => name !== 'reply_theme' || themeAvailable).map(name => controls.get(name)!),
  }));
}
