import type {GlobalThemeOverrides} from 'naive-ui';
import type {PageAppearance, PageColorSlot, ResolvedPageAppearance} from '../../../public-contract';

const variables = {
  background: '--ui-background', panel: '--ui-panel', text: '--ui-text', muted: '--ui-muted',
  border: '--ui-border', accent: '--ui-accent', accentContrast: '--ui-accent-contrast', logo: '--ui-logo',
} satisfies Record<PageColorSlot, string>;
const defaults = {
  light: {
    background: '#f7f4ed', panel: '#efebe4', text: '#302c30', muted: '#787078',
    border: '#d9d2cb', accent: '#655071', accentContrast: '#ffffff', logo: '#a99866',
  },
  dark: {
    background: '#211e26', panel: '#2a2630', text: '#eae6df', muted: '#aaa1ab',
    border: '#49414e', accent: '#c4ad78', accentContrast: '#211e26', logo: '#c4ad78',
  },
} satisfies Record<'light' | 'dark', Record<PageColorSlot, string>>;
const interaction = {
  light: {hover: '#796087', pressed: '#503d5c'},
  dark: {hover: '#d7c497', pressed: '#ae9460'},
};

function entries(value: unknown): [PropertyKey, unknown][] | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) return null;
  // Accept only own data properties. Accessors and hidden keys are not declarations.
  const keys = Reflect.ownKeys(value);
  const descriptors = Object.getOwnPropertyDescriptors(value);
  if (keys.some(key => !Object.hasOwn(descriptors[key as keyof typeof descriptors], 'value') ||
      !descriptors[key as keyof typeof descriptors].enumerable)) return null;
  return keys.map(key => [key, Reflect.get(value, key)]);
}

export function isPageAppearance(value: unknown): value is PageAppearance {
  try {
    return entries(value)?.every(([theme, palette]) =>
      (theme === 'light' || theme === 'dark') &&
      entries(palette)?.every(([slot, color]) =>
        Object.hasOwn(variables, slot) && typeof color === 'string' &&
        color.length === 7 && /^#[\da-f]{6}$/i.test(color),
      ),
    ) ?? false;
  } catch {
    return false;
  }
}

export function resolvePageAppearance(
  theme: 'light' | 'dark', appearance?: PageAppearance,
): ResolvedPageAppearance & {naiveOverrides: GlobalThemeOverrides} {
  const overrides = isPageAppearance(appearance) && Object.hasOwn(appearance, theme) ? appearance[theme] : undefined;
  const colors = Object.freeze({...defaults[theme], ...overrides});
  const {background, panel, text, muted, border, accent, accentContrast} = colors;
  const customAccent = overrides && Object.hasOwn(overrides, 'accent');
  const cssVars = Object.freeze(Object.fromEntries(
    (Object.keys(variables) as PageColorSlot[]).map(slot => [variables[slot], colors[slot]]),
  ));
  const naiveOverrides = Object.freeze({
    common: Object.freeze({
      heightMedium: '40px', borderRadius: '2px',
      bodyColor: background, cardColor: panel,
      textColorBase: text, textColor1: text, textColor2: text, textColor3: muted,
      placeholderColor: muted, borderColor: border, dividerColor: border,
      primaryColor: accent, primaryColorSuppl: accent,
      primaryColorHover: customAccent ? accent : interaction[theme].hover,
      primaryColorPressed: customAccent ? accent : interaction[theme].pressed,
    }),
    Button: Object.freeze({
      textColorPrimary: accentContrast, textColorHoverPrimary: accentContrast,
      textColorPressedPrimary: accentContrast, textColorFocusPrimary: accentContrast,
    }),
  } satisfies GlobalThemeOverrides);
  return Object.freeze({theme, colors, cssVars, naiveOverrides});
}
