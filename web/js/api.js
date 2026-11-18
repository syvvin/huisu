// Thin fetch wrapper around the local JSON API.

async function request(path, options = {}) {
  const init = { headers: {}, ...options };
  if (init.body && typeof init.body !== 'string') {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(init.body);
  }
  const res = await fetch(path, init);
  const type = res.headers.get('content-type') || '';
  const data = type.includes('application/json') ? await res.json() : await res.text();
  if (!res.ok) {
    const message = (data && data.error) || (typeof data === 'string' && data) || `请求失败 (${res.status})`;
    throw new Error(message);
  }
  return data;
}

const qs = (params) => {
  const search = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') search.set(k, v);
  });
  const s = search.toString();
  return s ? `?${s}` : '';
};

export const API = {
  library: () => request('/api/library'),
  part: (id) => request(`/api/part/${id}`),
  candidates: (id) => request(`/api/part/${id}/candidates`),
  dict: (lemma, partId, cue) => request(`/api/dict${qs({ lemma, part: partId, cue })}`),
  addWord: (payload) => request('/api/words', { method: 'POST', body: payload }),
  removeWord: (id, partId, forget = false) => request(`/api/words/${id}`, {
    method: 'DELETE', body: { part_id: partId, forget },
  }),
  updateWord: (id, payload) => request(`/api/words/${id}`, { method: 'PATCH', body: payload }),
  batchWords: (ids, action) => request('/api/words/batch', { method: 'POST', body: { ids, action } }),
  review: (id, payload) => request(`/api/words/${id}/review`, { method: 'POST', body: payload }),
  queue: (params) => request(`/api/review${qs(params)}`),
  wordbook: (params) => request(`/api/wordbook${qs(params)}`),
  stats: () => request('/api/stats'),
  settings: () => request('/api/settings'),
  saveSettings: (payload) => request('/api/settings', { method: 'POST', body: payload }),
  stateInfo: () => request('/api/state/info'),
  sync: () => request('/api/sync', { method: 'POST' }),
  progress: (payload) => request('/api/progress', { method: 'POST', body: payload }),
  search: (q) => request(`/api/search${qs({ q })}`),
  clearCache: () => request('/api/dict/cache/clear', { method: 'POST' }),
  extractorStart: () => request('/api/extractor/start', { method: 'POST' }),
  extractorCache: () => request('/api/extractor/cache'),
  extractorCacheClear: () => request('/api/extractor/cache/clear', { method: 'POST' }),
};
