import {createApp, defineComponent, h, ref} from 'vue';
import {ActionBar, PageTemplate, SettingsRow, StatusHint, Surface, uiText} from '@game-link/ui';
import type {PageTemplateSlots, TemplateActionKey, TemplateContent} from '@game-link/ui';
import type {MountedLogo, MountedPage, PageOptions, ResourceScope} from '../../../../pages/public-contract';
import {homeContent, settingsNote, settingsSectionsFor} from './page-content';
import {mountLogo, presentation} from './presentation-ff14';
import {createSettingsController, credentialFields} from './settings-controller';

let nextInstance = 0;

function createLogoScope(parent: ResourceScope) {
  const controller = new AbortController();
  const cleanups = new Set<() => void>();
  return {
    signal: controller.signal,
    isCurrent: () => !controller.signal.aborted && parent.isCurrent(),
    onDispose(cleanup: () => void) {
      cleanups.add(cleanup);
      return () => { cleanups.delete(cleanup); };
    },
    dispose() {
      controller.abort();
      for (const cleanup of cleanups) {
        // Keep a throwing callback registered for the next disposal attempt.
        cleanup();
        cleanups.delete(cleanup);
      }
    },
  };
}

export function mountSettings(container: HTMLElement, options: PageOptions): MountedPage {
  const idPrefix = `ff14-${++nextInstance}`;
  const isHome = options.routeId === 'home';
  const resourceUrl = isHome ? options.logoResourceUrl : undefined;
  const revision = ref(0);
  const activeRegion = ref<string>(credentialFields[0].name);
  const controller = createSettingsController(options, () => {
    revision.value++;
    options.onNavigationStateChange?.();
  });
  const logoScope = resourceUrl ? createLogoScope(options.scope) : undefined;
  let logo: MountedLogo | undefined;

  const app = createApp(defineComponent({setup() { return () => {
    revision.value;
    const s = controller.state;
    const ordinaryDisabled = !s.draftEditable || !s.context.available;
    const credentialDisabled = s.pending || !s.ready || !s.context.available;
    const copy = (key: TemplateActionKey) => uiText(key, s.context.locale);
    const button = (label: string, props: Record<string, unknown> = {}) =>
      h('button', {type: 'button', class: 'ui-button', ...props}, label);

    const slots: PageTemplateSlots = isHome ? {
      logo: ({presentation: {appearance}}) => h('span', {
        class: 'ff14-module-mark',
        'aria-hidden': 'true',
        onVnodeMounted: ({el}) => {
          if (!resourceUrl || !logoScope?.isCurrent()) return;
          const element = el as HTMLElement;
          try {
            logo = mountLogo(element, {resourceUrl, context: s.context, appearance, scope: logoScope});
          } catch {
            logoScope.dispose();
            const mark = element.ownerDocument.createElement('span');
            mark.className = 'ff14-logo-effect ff14-logo-static';
            mark.style.maskImage = `url(${JSON.stringify(resourceUrl)})`;
            element.append(mark);
          }
        },
        onVnodeUpdated: () => logo?.update(s.context, appearance),
      }, resourceUrl ? undefined : '✦'),
    } : {
      aside: () => h(Surface, {title: 'FFLogs 来源凭据', eyebrow: 'SOURCE ACCESS'}, {default: () => [
        h(StatusHint, {}, () => '国服与国际服独立配置。保存与上游验证相互独立。'),
        h('div', {class: 'ui-segmented', 'aria-label': '凭据区域'}, credentialFields.map(field =>
          button(field.label, {'aria-pressed': activeRegion.value === field.name, onClick: () => { activeRegion.value = field.name; }}))),
        ...credentialFields.map(field => {
          const status = s.credentials?.fields[field.name];
          return h('form', {
            key: field.name, class: 'ff14-credential', hidden: activeRegion.value !== field.name,
            'aria-label': `${field.label} FFLogs 凭据`,
            onSubmit: (event: Event) => { event.preventDefault(); void controller.replace(field.name); },
          }, [
            h('p', {class: 'ui-badge'}, !status ? '状态未读取' : !status.configured ? '未配置' :
              status.decryptable ? '已配置 · 可解密 · 上游未验证' : '已配置 · 当前不可解密'),
            ...(['client_id', 'client_secret'] as const).map(name => {
              const id = `${field.name}-${name}`;
              return h(SettingsRow, {key: name}, {default: () => [
                h('label', {for: id}, name === 'client_id' ? 'Client ID' : 'Client Secret'),
                h('input', {
                  class: 'ui-control', value: s.pairs[field.name][name], type: 'password', disabled: credentialDisabled,
                  maxlength: 512, id, autocomplete: 'off',
                  onInput: (event: Event) => controller.editPair(field.name, name, (event.target as HTMLInputElement).value),
                }),
              ]});
            }),
            h(ActionBar, {}, () => [
              button(copy('saveCredential'), {type: 'submit', 'data-variant': 'primary', disabled: credentialDisabled}),
              button(copy('discardCredential'), {disabled: s.pending, onClick: () => controller.discardPair(field.name)}),
              button(copy('clearCredential'), {disabled: credentialDisabled || !status?.configured, onClick: () => void controller.clear(field.name)}),
            ]),
          ]);
        }),
        h(StatusHint, {}, () => '凭据不回显。加密存储由全局设置统一管理。'),
      ]}),
      footer: () => h(ActionBar, {}, () => [
        button(copy('refresh'), {disabled: !s.context.available, 'aria-disabled': s.pending || undefined,
          'aria-busy': s.pending || undefined, onClick: () => { if (!s.pending) void controller.load(); }}),
        s.pending ? button(copy('cancelWait'), {onClick: controller.cancel}) : null,
      ]),
    };
    const content: TemplateContent = isHome ? homeContent : {
      title: '业务配置',
      sections: settingsSectionsFor(s.themeAvailable, s.themeChoices, s.draft.reply_theme),
      notes: [settingsNote],
      actions: [
        {id: 'save', type: 'submit', disabled: !s.pending && (!s.ready || !s.dirty)},
        {id: 'discard', disabled: !s.pending && !s.dirty},
      ],
    };

    return h(PageTemplate, {
      class: 'ff14-page ff14-settings',
      context: s.context, idPrefix, styleMountTarget: container, appearance: presentation.appearance, content,
      state: isHome ? undefined : {
        values: s.draft, disabled: ordinaryDisabled, pending: s.pending,
        notice: {key: 'custom', text: s.message, tone: s.error ? 'error' : 'neutral', role: s.error ? 'alert' : 'status'},
      },
      onFieldChange: ({id, value}: {id: string; value: string | boolean}) => {
        if (typeof value === 'string') controller.edit(id, value);
      },
      onAction: (action: TemplateActionKey) => {
        if (action === 'save') void controller.save();
        if (action === 'discard') controller.discard();
      },
    }, slots);
  }; }}));

  let disposed = false;
  let mounted = false;
  const dispose = () => {
    if (disposed) return;
    controller.dispose();
    logoScope?.dispose();
    if (mounted) {
      app.unmount();
      mounted = false;
    }
    container.replaceChildren();
    disposed = true;
  };
  options.scope.onDispose(dispose);
  if (options.scope.isCurrent()) {
    mounted = true;
    app.mount(container);
    if (!isHome) void controller.load();
  }
  return {update: controller.update, dispose, navigationImpact: controller.navigationImpact};
}
