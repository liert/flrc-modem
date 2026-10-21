import type { SpectrumFrame, SpectrumStatusResponse } from '../types';

// Connection lifetime depends on the session, never on chart state or controls.
export function connectSpectrumStream(options: {
  sessionId: string; url: string;
  onFrame: (frame: SpectrumFrame) => void;
  onStatus: (status: SpectrumStatusResponse) => void;
  onConnectionError: (message: string) => void;
  createSocket?: (url: string) => WebSocket;
}) {
  let disposed = false;
  let terminal = false;
  let socket: WebSocket | null = null;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let delay = 500;
  let lastSequence = -1;
  const open = () => {
    if (disposed || terminal) return;
    const current = (options.createSocket || (url => new WebSocket(url)))(options.url);
    socket = current;
    current.onopen = () => { if (!disposed && current === socket) delay = 500; };
    current.onmessage = event => {
      if (disposed || current !== socket) return;
      let data;
      try { data = JSON.parse(event.data); }
      catch { return; }
      try {
        if (data.session_id !== options.sessionId) return;
        if (data.type === 'spectrum_status') {
          terminal = ['error', 'stopped'].includes(data.state);
          options.onStatus(data);
        } else if (Array.isArray(data.powers)) {
          if (Number.isFinite(data.sequence)) {
            if (data.sequence <= lastSequence) return;
            lastSequence = data.sequence;
          }
          options.onFrame(data);
        }
      } catch (error) {
        options.onConnectionError(`频谱显示更新失败：${error instanceof Error ? error.message : String(error)}`);
      }
    };
    current.onclose = event => {
      if (disposed || terminal || current !== socket) return;
      if (event.code === 1008) {
        terminal = true;
        options.onConnectionError('频谱会话已失效，请重新开始采集。');
        return;
      }
      options.onConnectionError('频谱连接已断开，正在重新连接…');
      retry = setTimeout(open, delay);
      delay = Math.min(delay * 2, 5000);
    };
    current.onerror = () => { if (!disposed && current === socket) current.close(); };
  };
  const heartbeat = setInterval(() => {
    if (socket?.readyState === 1) socket.send('ping');
  }, 5000);
  open();
  return () => {
    disposed = true;
    clearTimeout(retry);
    clearInterval(heartbeat);
    socket?.close();
  };
}
