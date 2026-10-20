/** @typedef {import('../../../../pages/public-contract').PageOptions} PageOptions */
/** @param {HTMLElement} container @param {PageOptions} options */
export function mount(container, options) {
  const document = container.ownerDocument;
  const section = document.createElement('section');
  const title = document.createElement('h2'); title.textContent = 'Minimal module';
  const button = document.createElement('button'); button.textContent = 'Read public status';
  const status = document.createElement('p'); status.setAttribute('role', 'status');
  status.textContent = 'No request has been sent.';
  section.append(title, button, status); container.append(section);
  let context = options.context, sequence = 0, disposed = false, pending = false;
  const identity = value => JSON.stringify([value.owner, value.runtimeId, value.epoch, value.boundary]);
  const binding = identity(context);
  const current = version => !disposed && options.scope.isCurrent() && context.available && identity(context) === binding && sequence === version;
  button.disabled = !context.available;
  button.onclick = async () => {
    if (pending || !current(sequence)) return;
    const version = ++sequence; pending = true; button.disabled = true;
    status.textContent = 'Reading public status...';
    try {
      const result = await options.services.invoke('status', {});
      if (!current(version)) return;
      status.textContent = result?.status === 'success' && result?.model_facts?.ready === true
        ? 'Minimal module is ready.' : 'No verified public result.';
    } catch {
      if (current(version)) status.textContent = 'Request failed. Reopen the page to check authorization.';
    } finally {
      if (current(version)) { pending = false; button.disabled = false; }
    }
  };
  const dispose = () => { if (disposed) return; disposed = true; sequence++; button.onclick = null; section.remove(); };
  options.scope.onDispose(dispose);
  return {
    update(next) {
      if (disposed) return;
      context = next;
      if (!next.available || identity(next) !== binding) {
        sequence++; pending = false; button.disabled = true; status.textContent = 'Module binding expired. Reopen this page.';
      }
    },
    dispose,
  };
}
