const copy = {
  save: ['保存配置', 'Save settings'],
  discard: ['放弃修改', 'Discard changes'],
  refresh: ['重新读取状态', 'Refresh status'],
  query: ['查询', 'Query'],
  cancelWait: ['停止等待', 'Stop waiting'],
  resetDefault: ['恢复默认', 'Restore default'],
  saveCredential: ['保存凭据', 'Save credential'],
  discardCredential: ['放弃输入', 'Discard input'],
  clearCredential: ['清除凭据', 'Clear credential'],
  saved: ['已保存', 'Saved'],
  refreshed: ['已刷新', 'Refreshed'],
  loading: ['正在读取…', 'Reading…'],
  dirty: ['有未保存修改', 'Unsaved changes'],
  conflict: ['配置已变化，请重新读取后保存', 'Settings changed. Refresh before saving.'],
  pendingUnverified: ['已提交，状态暂未核实，请刷新确认', 'Submitted. Refresh to verify the current status.'],
  unavailable: ['当前入口暂不可用，请刷新确认', 'This page is temporarily unavailable. Refresh to check.'],
  custom: ['', ''],
} as const;

export type UiTextKey = keyof typeof copy;

/** The initial catalog uses fixed text and has no interpolation parameters. */
export function uiText(key: UiTextKey, locale: string, _params?: Readonly<Record<string, string | number>>): string {
  return copy[key][/^en(?:-|$)/i.test(locale) ? 1 : 0];
}
