import {createApp, h, shallowRef} from 'vue';
import {PageTemplate, Surface, type TemplateContent} from '@game-link/ui';
import type {MountedPage, PageContext, PageOptions} from '../../../../../pages/public-contract';

let instance = 0;

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
  const content: TemplateContent = {
    title: 'Shared UI example',
    eyebrow: 'EXAMPLE',
    intro: 'A module-owned view using Core presentation components.',
    sections: [{
      id: 'presentation', title: 'Public status',
      fields: [{
        id: 'heading', label: 'Local heading', control: 'text', maxLength: 80, width: 'medium',
        hint: 'Presentation only: this value is not saved or sent to a service.',
      }, {
        id: 'detail', label: 'View detail', control: 'select',
        choices: [{value: 'summary', label: 'Summary'}, {value: 'full', label: 'Full detail'}],
      }],
    }],
    actions: [{id: 'query', type: 'submit'}],
  };
  const detail = shallowRef('summary');
  // Unique to this mount, stable across context and draft updates.
  const idPrefix = 'shared-ui-example-' + ++instance;
  const app = createApp({setup: () => () => h(PageTemplate, {
    content, context: context.value, styleMountTarget: container, idPrefix, headingLevel: 1,
    appearance: {light: {accent: '#305e73', accentContrast: '#ffffff'}},
    state: {
      values: {heading: heading.value, detail: detail.value},
      errors: {heading: heading.value.trim() ? '' : 'Enter a heading.'},
      pending: pending.value,
      disabled: !current(sequence),
      notice: {key: 'custom', text: message.value},
    },
    onFieldChange: ({id, value}) => {
      if (id === 'heading' && typeof value === 'string') heading.value = value;
      if (id === 'detail' && typeof value === 'string') detail.value = value;
    },
    onAction: inspect,
  }, {
    aside: () => h(Surface, {title: 'Presentation only'}, {default: () => h('p', 'The UI facade grants no management permissions.')}),
    results: () => h('p', detail.value === 'full' ? `${heading.value || 'Public status'} · Business requests use the owner-bound page services.` : heading.value || 'Public status'),
  })});
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
