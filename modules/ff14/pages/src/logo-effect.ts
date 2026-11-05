import type {LogoMountOptions, MountedLogo} from '../../../../pages/public-contract';

/** Both FF14 logo surfaces use this effect with their own scoped resource URL. */
export function mountFf14Logo(container: HTMLElement, options: LogoMountOptions): MountedLogo {
  const {resourceUrl, scope} = options;
  let mark: HTMLElement | undefined;
  let animation: Animation | undefined;
  let media: MediaQueryList | undefined;
  let listening = false;

  const stop = () => {
    animation?.cancel();
    animation = undefined;
  };
  const onPreference = (event: MediaQueryListEvent) => {
    if (event.matches) stop();
  };
  const dispose = () => {
    stop();
    if (listening) {
      media?.removeEventListener('change', onPreference);
      listening = false;
    }
    mark?.remove();
    mark = undefined;
  };
  const handle: MountedLogo = {
    update(_context, appearance) {
      mark?.style.setProperty('--ff14-logo-color', appearance.colors.logo);
    },
    dispose,
  };
  scope.onDispose(dispose);
  if (!scope.isCurrent()) return handle;

  const document = container.ownerDocument;
  mark = document.createElement('span');
  mark.className = 'ff14-logo-effect';
  mark.setAttribute('aria-hidden', 'true');
  mark.style.maskImage = `url(${JSON.stringify(resourceUrl)})`;
  handle.update(options.context, options.appearance);
  const sweep = document.createElement('span');
  sweep.className = 'ff14-logo-sweep';
  mark.append(sweep);
  container.append(mark);

  media = document.defaultView?.matchMedia?.('(prefers-reduced-motion: reduce)');
  if (typeof media?.addEventListener === 'function' && typeof media.removeEventListener === 'function') {
    listening = true;
    media.addEventListener('change', onPreference);
  }
  if (!media || media.matches || typeof sweep.animate !== 'function') return handle;

  try {
    animation = sweep.animate([
      {transform: 'translateY(-100%)', opacity: 0, offset: 0},
      {transform: 'translateY(-100%)', opacity: 1, offset: 0.01},
      {transform: 'translateY(100%)', opacity: 1, offset: 0.63},
      {transform: 'translateY(-100%)', opacity: 0, offset: 1},
    ], {duration: 870, delay: 1300, iterations: 1, fill: 'forwards', easing: 'linear'});
  } catch {
    // The masked base stays visible if Web Animations is unavailable.
  }
  return handle;
}
