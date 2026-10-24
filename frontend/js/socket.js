// WebSocket client: live events from the backend (which receives them over MQTT).
//
// Clock synchronisation (NTP-style): the browser may be a phone with a different
// clock. We send t0, the server answers with its time, we receive at t3:
//     offset = server_ts - (t0 + t3) / 2
// and report every event's arrival time in the SERVER clock, so the backend can
// compute end-to-end latency = dashboard_receive - detection correctly.

const handlers = new Set();
let ws = null;
let offset = 0;               // server clock - browser clock (ms)
let bestRtt = Infinity;
let syncTimer = null;
export const socketState = { connected: false, rtt: null };

export const serverNow = () => Date.now() + offset;
export const onMessage = (fn) => handlers.add(fn);

function emit(msg, receivedAt) { handlers.forEach((fn) => fn(msg, receivedAt)); }

function sendSync() {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "sync", t0: Date.now() }));
}

export function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

export function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    socketState.connected = true;
    bestRtt = Infinity;
    sendSync();
    clearInterval(syncTimer);
    syncTimer = setInterval(sendSync, 10000);
    emit({ type: "_socket", connected: true });
  };

  ws.onmessage = (e) => {
    const receivedAt = Date.now();          // browser clock, taken first
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.type === "sync") {
      const rtt = receivedAt - msg.t0;
      socketState.rtt = rtt;
      if (rtt <= bestRtt * 1.5 || bestRtt === Infinity) {   // prefer low-RTT samples
        bestRtt = Math.min(bestRtt, rtt);
        offset = msg.server_ts - (msg.t0 + receivedAt) / 2;
      }
      return;
    }
    if (msg.type === "event" && msg.action === "created") {
      // Acknowledge with the arrival time converted to the server's clock.
      send({ type: "ack", event_id: msg.event.id, received_at: receivedAt + offset });
    }
    emit(msg, receivedAt + offset);
  };

  ws.onclose = () => {
    socketState.connected = false;
    clearInterval(syncTimer);
    emit({ type: "_socket", connected: false });
    setTimeout(connect, 2000);  // auto-reconnect (e.g. backend restarted)
  };
  ws.onerror = () => ws.close();
}
