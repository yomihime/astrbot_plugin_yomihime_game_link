/** Public presentation API v1. No services, authorization, persistence or Host access. */
export {UiProvider, SettingsLayout, Surface, LogoMark} from './frontend/src/ui/shared-components';
export {PageHeader, SettingsGroup, SettingsRow, ActionBar, StatusHint, FormField, FormGrid} from './frontend/src/ui/components';
export type {FormFieldSlot} from './frontend/src/ui/components';
export {PageTemplate} from './frontend/src/ui/page-template';
export {uiText} from './frontend/src/ui/page-copy';
export type {UiTextKey} from './frontend/src/ui/page-copy';
export type {UiPresentationContext, UiProviderProps, UiProviderSlots} from './frontend/src/ui/shared-components';
export type {
  PageTemplateProps, PageTemplateSlots, PageTemplateEvents, TemplateContent, TemplateState, TemplateChoice,
  TemplateField, TemplateSection, TemplateValues, TemplateAction, TemplateActionKey, TemplateNotice,
} from './frontend/src/ui/page-template';
export type {
  PageAppearance, PageColorSlot, ResolvedPageAppearance, ModulePresentationExports, LogoMountOptions, MountedLogo,
} from './public-contract';
