export type SettingChoice = {value: string | null; label: string};
export type ChoiceField = {module_id: string; name: string; group?: string | null; default?: unknown; choices?: SettingChoice[]; value_schema: null | {type: string; enum?: unknown[]}};

// These reserved declarations belong to Core even when scoped to another module.
// Choice metadata decorates the ordinary enum contract; it never grants access.
export const isCoreThemeField = (field: ChoiceField) => field.group === 'reply_style' && (field.module_id === 'game_link/core' ? field.name === 'image_theme' : field.name === 'reply_theme');
export const followsCoreTheme = (field: ChoiceField) => isCoreThemeField(field) && field.module_id !== 'game_link/core' && field.default === null && field.choices?.[0]?.value === null;
export const settingValues = (field: ChoiceField): unknown[] | null => field.choices?.map(choice => choice.value) ?? field.value_schema?.enum ?? (field.value_schema?.type === 'boolean' ? [false, true] : null);
