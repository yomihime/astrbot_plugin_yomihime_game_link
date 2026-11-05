import type {ModulePresentationExports} from '../../../../pages/public-contract';
import './logo-effect.css';

export const presentation = {
  schemaVersion: 1,
  appearance: {
    light: {logo: '#a99866'},
    dark: {logo: '#c4ad78'},
  },
} satisfies NonNullable<ModulePresentationExports['presentation']>;

export {mountFf14Logo as mountLogo} from './logo-effect';
