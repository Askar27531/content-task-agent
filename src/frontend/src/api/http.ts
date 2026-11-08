import type { StudioApi, StudioEvent, Connection } from '../types';

const base = import.meta.env.VITE_API_BASE || '/api';
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(base + path, { ...options, headers: { 'Content-Type': 'application/json', ...options?.headers } });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(detail?.error?.message || detail?.detail || (response.status >= 500 ? '后端暂时不可用，请检查服务是否已启动。' : `请求未完成（${response.status}）`));
  }
  return response.json() as Promise<T>;
}
export const httpApi: StudioApi = {
  mode: 'http',
  listSessions: () => request('/sessions'),
  createSession: () => request('/sessions', { method: 'POST', body: '{}' }),
  getWorkspace: id => request(`/sessions/${id}`),
  getMessages: (id, cursor) => request(`/sessions/${id}/messages${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`),
  getToolCall: id => request(`/tool-calls/${id}`),
  getProposal: id => request(`/proposals/${id}`),
  sendMessage: (id, content, clientId, context) => request(`/sessions/${id}/messages`, { method: 'POST', body: JSON.stringify({ content, client_message_id: clientId, view_context: context }) }),
  subscribe(id, after, onEvent, onState) {
    let source: EventSource | null = null;
    let closed = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    const connect = (seq: number) => {
      if (closed) return;
      source = new EventSource(`${base}/sessions/${id}/events?after=${seq}`);
      source.onopen = () => { if(!closed) onState('connected'); };
      source.onerror = () => { if(!closed) onState(navigator.onLine ? 'reconnecting' : 'offline'); };
      source.onmessage = event => {
        let parsed: StudioEvent;
        try { parsed = JSON.parse(event.data) as StudioEvent; } catch { return; }
        if (parsed.type === 'resync_required') {
          source?.close();
          void httpApi.getWorkspace(id).then(snapshot => { if(!closed) { onEvent(parsed); connect(snapshot.last_event_seq); } }).catch(() => {
            if(!closed) { onState('offline'); reconnectTimer=setTimeout(()=>connect(seq),2000); }
          });
        } else onEvent(parsed);
      };
    };
    onState('connecting'); connect(after);
    return () => { closed = true; source?.close(); if(reconnectTimer) clearTimeout(reconnectTimer); };
  },
};
