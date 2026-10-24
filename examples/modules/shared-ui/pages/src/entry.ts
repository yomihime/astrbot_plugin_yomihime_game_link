import {createApp, h, shallowRef} from 'vue';
import {UiProvider, PageHeader, SettingsLayout, Surface, ActionBar, StatusHint, FormGrid, FormField} from '@game-link/ui';
import type {FormFieldSlot} from '@game-link/ui';
import type {MountedPage, PageContext, PageOptions} from '../../../../../pages/public-contract';

/** Source-only, non-FF14 consumer of the Core public presentation facade. */
export function mount(container: HTMLElement, options: PageOptions): MountedPage {
  const context = shallowRef(options.context);
  const message = shallowRef('No request has been sent.');
  const pending = shallowRef(false);
  const heading = shallowRef('Public status');
  const identity = (value: Readonly<PageContext>) => JSON.stringify([value.owner, value.runtimeId, value.epoch, value.boundary]);
  const binding = identity(options.context);
  let disposed = false, invalidated = false, sequence = 0;
  const current = (version: number) => !disposed && !invalidated && options.scope.isCurrent() && context.value.available && identity(context.value) === binding && version === sequence;
  async function inspect() {
    if (pending.value || !current(sequence)) return;
    const version = ++sequence;
    pending.value = true;
    message.value = 'Reading public status…';
    try {
      await options.services.invoke('status', {});
      if (current(version)) message.value = 'Public status request completed.';
    } catch {
      if (current(version)) message.value = 'Request failed. Reopen the page to check authorization.';
    } finally {
      if (current(version)) pending.value = false;
    }
  }
  const app = createApp({setup: () => () => h(UiProvider, {
    theme: context.value.theme, locale: context.value.locale, styleMountTarget: container,
  }, {default: () => [
    h(PageHeader, {title: 'Shared UI example', eyebrow: 'EXAMPLE', subtitle: 'A module-owned view using Core presentation components.'}),
    h(SettingsLayout, {}, {
      default: () => h(Surface, {title: heading.value || 'Public status'}, {default: () => [
        h(FormGrid, {columns: 1}, {default: () => h(FormField, {
          controlId: 'example-view-heading', label: 'Local heading',
          hint: 'Presentation only: this value is not saved or sent to a service.',
          error: heading.value.trim() ? '' : 'Enter a heading.',
        }, {default: ({controlAttrs}: FormFieldSlot) => h('input', {
          ...controlAttrs, class: 'ui-control', value: heading.value, maxlength: 80,
          onInput: (event: Event) => {heading.value = (event.target as HTMLInputElement).value;},
        })})}),
        h('p', 'Business requests still use the owner-bound page services.'),
        h(ActionBar, {}, {default: () => h('button', {type: 'button', class: 'ui-button', 'data-variant': 'primary', disabled: pending.value || !current(sequence), onClick: inspect}, 'Read public status')}),
        h(StatusHint, {}, {default: () => message.value}),
      ]}),
      aside: () => h(Surface, {title: 'Presentation only'}, {default: () => h('p', 'The UI facade grants no management permissions.')}),
    }),
  ]})});
  app.mount(container);
  function dispose() {
    if (disposed) return;
    disposed = true;
    sequence++;
    app.unmount();
  }
  options.scope.onDispose(dispose);
  return {
    update(next) {
      if (disposed) return;
      const wasAvailable = context.value.available;
      context.value = next;
      if (identity(next) !== binding) {
        invalidated = true;
        sequence++;
        pending.value = false;
        message.value = 'Module binding expired. Reopen this page.';
      } else if (!invalidated && !next.available) {
        // Refresh temporarily pauses the same binding; fence old results, not the view.
        sequence++;
        pending.value = false;
        message.value = 'Page temporarily unavailable. Waiting for the current context.';
      } else if (!invalidated && !wasAvailable) {
        message.value = 'Page available. Read public status to send a new request.';
      }
    },
    dispose,
  };
}
