import { useState, useEffect } from 'react';

// REST remains authoritative: the socket only invalidates the bounded page in view.
export function useWebSocket() {
  const [isConnected, setIsConnected] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | undefined;
    let reconnect: ReturnType<typeof setTimeout> | undefined;
    let heartbeat: ReturnType<typeof setInterval> | undefined;
    let attempts = 0;
    const connect = () => {
      if (disposed) return;
      socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/alerts`);
      socket.onopen = () => {
        if (disposed) { socket?.close(); return; }
        attempts = 0;
        setIsConnected(true);
        setRevision(value => value + 1);
        heartbeat = setInterval(() => { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type: 'PING'})); }, 20000);
      };
      socket.onmessage = event => {
        if (disposed) return;
        try { if (JSON.parse(event.data).type === 'ALERT') setRevision(value => value + 1); } catch { /* Ignore malformed transport messages. */ }
      };
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        if (heartbeat) clearInterval(heartbeat);
        if (disposed) return;
        setIsConnected(false);
        reconnect = setTimeout(connect, Math.min(1000 * 2 ** attempts++, 15000));
      };
    };
    connect();
    return () => { disposed = true; clearTimeout(reconnect); clearInterval(heartbeat); socket?.close(); };
  }, []);
  return { isConnected, revision };
}
