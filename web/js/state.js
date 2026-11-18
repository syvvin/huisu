// Shared application state.
import { store } from './util.js';

export const S = {
  lib: null,
  settings: {},
  part: null,
  words: [],
  added: new Set(),
  panel: null,          // null | 'deck' | 'dict'
  dict: null,
  dictQuery: null,
  dictLoading: false,
  dictError: '',
  dictMore: false,
  pendingSeek: null,
  cleanup: null,
  routeSeq: 0,
  ui: {
    openChapters: new Set(store.get('openChapters', [])),
    libQuery: '',
    wb: null,
  },
};

export function applySettings(settings) {
  S.settings = { ...S.settings, ...settings };
  const theme = S.settings.theme === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme;
  const btn = document.querySelector('#btn-theme');
  if (btn) {
    btn.textContent = theme === 'light' ? '☾' : '☼';
    btn.title = theme === 'light' ? '切换到暗色主题' : '切换到亮色主题';
  }
}
