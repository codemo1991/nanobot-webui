import { useEffect, useRef, useCallback, useState } from 'react';

// 扩展 Window 类型
declare global {
  interface Window {
    __wsUnloaded?: boolean;
  }
}

export interface WsEvent {
  type: string;
  event?: {
    type: string;
    content?: string;
    [key: string]: any;
  };
  error?: string;
}

export interface UseWebSocketOptions {
  url: string;
  onMessage: (event: WsEvent) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
  onError?: (error: Event) => void;
  reconnect?: boolean;
  reconnectInterval?: number;
  heartbeatInterval?: number;  // 心跳间隔(ms)，默认 30s
}

export function useWebSocket(options: UseWebSocketOptions) {
  const {
    url,
    onMessage,
    onConnect,
    onDisconnect,
    onError,
    reconnect = true,
    reconnectInterval = 3000,
    heartbeatInterval = 30000,  // 30秒心跳
  } = options;

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const heartbeatIntervalRef = useRef<number | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  // Buffer for messages sent while WebSocket is reconnecting
  const pendingMessagesRef = useRef<object[]>([]);

  // Use refs for callbacks to avoid reconnects when callback references change
  const onMessageRef = useRef(onMessage);
  const onConnectRef = useRef(onConnect);
  const onDisconnectRef = useRef(onDisconnect);
  const onErrorRef = useRef(onError);
  const urlRef = useRef(url);

  // Keep refs updated
  onMessageRef.current = onMessage;
  onConnectRef.current = onConnect;
  onDisconnectRef.current = onDisconnect;
  onErrorRef.current = onError;
  urlRef.current = url;

  // Flush pending messages when connected
  const flushPendingMessages = useCallback(() => {
    const pending = pendingMessagesRef.current
    if (pending.length === 0) return
    pendingMessagesRef.current = []
    for (const msg of pending) {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify(msg))
        console.log('[WebSocket] Flushed pending message:', msg)
      }
    }
  }, [])

  const clearHeartbeat = useCallback(() => {
    if (heartbeatIntervalRef.current) {
      clearInterval(heartbeatIntervalRef.current);
      heartbeatIntervalRef.current = null;
    }
  }, []);

  const clearReconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
  }, []);

  const disconnect = useCallback(() => {
    clearHeartbeat();
    clearReconnect();
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    // Clear pending messages when disconnecting
    pendingMessagesRef.current = [];
  }, [clearHeartbeat, clearReconnect]);

  const startHeartbeat = useCallback(() => {
    clearHeartbeat();
    heartbeatIntervalRef.current = window.setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'ping' }));
      }
    }, heartbeatInterval);
  }, [clearHeartbeat, heartbeatInterval]);

  const connect = useCallback(() => {
    const currentUrl = urlRef.current;
    console.log('[WebSocket] connect called, URL:', currentUrl);

    if (!currentUrl) {
      console.log('[WebSocket] No URL provided, skipping connect');
      return;
    }

    // 如果已有连接且正在打开或已打开，不再重复连接
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      console.log('[WebSocket] Already connected');
      return;
    }
    if (wsRef.current?.readyState === WebSocket.CONNECTING) {
      console.log('[WebSocket] Already connecting');
      return;
    }

    const ws = new WebSocket(currentUrl);
    console.log('[WebSocket] WebSocket created for URL:', currentUrl);

    ws.onopen = () => {
      console.log('[WebSocket] Connected!');
      setIsConnected(true);
      startHeartbeat();
      onConnectRef.current?.();
      // Flush any messages that were queued during reconnection
      flushPendingMessages();
    };

    ws.onmessage = (event) => {
      console.log('[WebSocket] message received:', event.data);
      try {
        // 忽略 pong 响应（心跳回执）
        if (event.data === JSON.stringify({ type: 'pong' })) {
          return;
        }
        const data = JSON.parse(event.data) as WsEvent;
        onMessageRef.current(data);
      } catch (e) {
        console.error('Failed to parse WebSocket message:', e);
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      clearHeartbeat();
      onDisconnectRef.current?.();

      // 自动重连（只在页面未卸载时）
      if (reconnect && !window.__wsUnloaded) {
        clearReconnect();
        reconnectTimeoutRef.current = window.setTimeout(() => {
          // 检查 URL 是否变化，如果变化则不重连旧 URL
          if (urlRef.current === currentUrl) {
            connect();
          }
        }, reconnectInterval);
      }
    };

    ws.onerror = (error) => {
      // 不打印 error 事件，onclose 会处理重连
      onErrorRef.current?.(error);
    };

    wsRef.current = ws;
  }, [reconnect, reconnectInterval, startHeartbeat, clearHeartbeat, clearReconnect]);

  useEffect(() => {
    console.log('[WebSocket] Hook mounted, URL:', url);
    connect();

    // 标记页面未卸载
    window.__wsUnloaded = false;

    // 页面卸载时断开连接（只断开，不重连）
    const handleBeforeUnload = () => {
      window.__wsUnloaded = true;
      disconnect();
    };

    window.addEventListener('beforeunload', handleBeforeUnload);

    return () => {
      console.log('[WebSocket] Hook unmounting');
      window.removeEventListener('beforeunload', handleBeforeUnload);
      window.__wsUnloaded = true;
      disconnect();
    };
  }, [connect, disconnect]);  // 依赖 connect（内部已通过 ref 访问最新状态）

  // URL 变化时断开旧连接并连接新会话
  useEffect(() => {
    console.log('[WebSocket] URL changed:', url);
    const pendingConnect = { current: false };

    if (!url) {
      if (wsRef.current) {
        disconnect();
      }
      return;
    }

    if (wsRef.current?.readyState === WebSocket.OPEN) {
      disconnect();
      pendingConnect.current = true;
      const t = setTimeout(() => {
        if (pendingConnect.current) {
          connect();
        }
      }, 100);
      return () => {
        pendingConnect.current = false;
        clearTimeout(t);
      };
    }

    if (!wsRef.current || wsRef.current.readyState === WebSocket.CLOSED) {
      connect();
    }
    // CONNECTING 状态时不需要操作，连接建立后会触发 onopen
  }, [url, connect, disconnect]);

  const send = useCallback((data: object) => {
    console.log('[WebSocket] send called:', data);
    console.log('[WebSocket] readyState:', wsRef.current?.readyState);
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
      console.log('[WebSocket] message sent successfully');
      return true;
    }
    // Buffer message for sending when reconnected
    pendingMessagesRef.current.push(data);
    console.log('[WebSocket] buffering message, pending count:', pendingMessagesRef.current.length);
    return false;
  }, []);

  return {
    isConnected,
    send,
    disconnect,
    reconnect: connect,
  };
}
