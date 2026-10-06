// Only explicit skip navigation moves to the beginning of the main region.
export function skipToMain(main: HTMLElement | null): void {
  if (!main) return;
  main.focus({preventScroll: true});
  main.scrollIntoView({block: 'start', inline: 'nearest', behavior: 'instant'});
}
