"""Escuta ao vivo: repassa o PCM já recebido por UDP como stream HTTP (via ingress do HA).

Sem codificação: o PCM 16 kHz/16 bit/mono (32 kB/s) sai como chegou. Sem ouvinte, o
único custo no laço UDP é testar `live.active` (um booleano).

Rotas (servidas só para o proxy do ingress, 172.30.32.2):
  /            página com botão "Ouvir" (Web Audio, funciona no app do HA e no iOS)
  /stream.wav  WAV contínuo (VLC/navegadores que tocam WAV progressivo)
  /stream.pcm  PCM cru s16le (usado pela página)
  /status      JSON com ouvintes e fonte

Opcional: com MQTT disponível, publica no dispositivo do StickS3 (pela MAC) um sensor
"Ouvintes ao vivo" e o link do dispositivo (configuration_url) para esta página.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import struct
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("sound-monitor.live")

INGRESS_PROXY = "172.30.32.2"
SAMPLE_RATE = 16000
MAX_QUEUE = 64  # ~2 s de pacotes de 512 amostras; ouvinte lento perde áudio, não trava o detector


class _Client:
    def __init__(self) -> None:
        self.queue: collections.deque[bytes] = collections.deque(maxlen=MAX_QUEUE)
        self.event = threading.Event()


class LiveRelay:
    def __init__(self) -> None:
        self._clients: set[_Client] = set()
        self._lock = threading.Lock()
        self.active = False  # lido sem lock no laço UDP
        self.source = ""
        self.on_change = None  # callback(n_ouvintes)

    def feed(self, address: str, payload: bytes) -> None:
        self.source = address
        with self._lock:
            for c in self._clients:
                c.queue.append(payload)
                c.event.set()

    def _add(self) -> _Client:
        c = _Client()
        with self._lock:
            self._clients.add(c)
            self.active = True
            n = len(self._clients)
        log.info("Ouvinte ao vivo conectado (%d)", n)
        if self.on_change:
            self.on_change(n)
        return c

    def _remove(self, c: _Client) -> None:
        with self._lock:
            self._clients.discard(c)
            n = len(self._clients)
            self.active = n > 0
        log.info("Ouvinte ao vivo saiu (%d)", n)
        if self.on_change:
            self.on_change(n)

    @property
    def listeners(self) -> int:
        return len(self._clients)


PAGE = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>StickS3 ao vivo</title>
<style>body{font-family:system-ui,sans-serif;background:#111;color:#eee;display:flex;flex-direction:column;
align-items:center;gap:16px;padding:24px}button{font-size:22px;padding:14px 28px;border-radius:12px;border:0;
background:#03a9f4;color:#fff}#m{width:80%;max-width:420px;height:14px;background:#333;border-radius:7px;overflow:hidden}
#b{height:100%;width:0;background:#4caf50}small{color:#999}a{color:#8cf}</style></head><body>
<h2>&#127908; StickS3 ao vivo</h2><button id="go">Ouvir</button><div id="m"><div id="b"></div></div>
<div id="s">parado</div><small>16 kHz mono, sem compressão (~32 kB/s). Atraso ~0,5 s.
<a href="stream.wav">stream.wav</a></small>
<script>
let ctx,ctl,t0=0;const s=document.getElementById('s'),bar=document.getElementById('b'),go=document.getElementById('go');
async function start(){ctx=new (window.AudioContext||window.webkitAudioContext)({sampleRate:16000});
ctl=new AbortController();go.textContent='Parar';s.textContent='conectando…';
try{const r=await fetch('stream.pcm',{signal:ctl.signal,cache:'no-store'});const rd=r.body.getReader();let rest=null;
s.textContent='ao vivo';t0=ctx.currentTime+0.3;
for(;;){const {value,done}=await rd.read();if(done)break;let u=value;
if(rest){const j=new Uint8Array(rest.length+u.length);j.set(rest);j.set(u,rest.length);u=j;rest=null}
if(u.length%2){rest=u.slice(-1);u=u.slice(0,-1)}if(!u.length)continue;
const v=new DataView(u.buffer,u.byteOffset,u.byteLength),n=u.length/2,buf=ctx.createBuffer(1,n,16000),d=buf.getChannelData(0);let e=0;
for(let i=0;i<n;i++){const x=v.getInt16(2*i,true)/32768;d[i]=x;e+=x*x}
const db=10*Math.log10(e/n+1e-12);bar.style.width=Math.max(0,Math.min(100,(db+90)*100/60))+'%';
const now=ctx.currentTime;if(t0<now||t0>now+1.5)t0=now+0.15;const src=ctx.createBufferSource();src.buffer=buf;
src.connect(ctx.destination);src.start(t0);t0+=buf.duration}}catch(e){if(e.name!=='AbortError')s.textContent='erro: '+e}
stop()}
function stop(){if(ctl)ctl.abort();ctl=null;if(ctx)ctx.close();ctx=null;go.textContent='Ouvir';bar.style.width='0';
if(s.textContent==='ao vivo'||s.textContent==='conectando…')s.textContent='parado'}
go.onclick=()=>ctl?stop():start();
</script></body></html>"""


def _wav_header() -> bytes:
    size = 0xFFFFFFFF - 36
    return (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE,
            SAMPLE_RATE * 2, 2, 16) + b"data" + struct.pack("<I", size))


def _handler(relay: LiveRelay):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # silencioso
            pass

        def _allowed(self) -> bool:
            if self.client_address[0] == INGRESS_PROXY or os.environ.get("LIVE_ALLOW_ANY") == "1":
                return True
            self.send_error(403)
            return False

        def _send(self, code: int, ctype: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._allowed():
                return
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/":
                self._send(200, "text/html; charset=utf-8", PAGE.encode())
            elif path == "/status":
                self._send(200, "application/json",
                           json.dumps({"listeners": relay.listeners, "source": relay.source}).encode())
            elif path in ("/stream.wav", "/stream.pcm"):
                self._stream(path.endswith(".wav"))
            else:
                self.send_error(404)

        def _stream(self, wav: bool) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav" if wav else "application/octet-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            client = relay._add()
            try:
                if wav:
                    self._chunk(_wav_header())
                idle = time.monotonic()
                while True:
                    client.event.wait(5)
                    client.event.clear()
                    data = b"".join(client.queue.popleft() for _ in range(len(client.queue)))
                    if data:
                        idle = time.monotonic()
                        self._chunk(data)
                    elif time.monotonic() - idle > 5:
                        self._chunk(b"\x00\x00" * 1600)  # 0,1 s de silêncio mantém a conexão viva
                        idle = time.monotonic()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                relay._remove(client)

        def _chunk(self, data: bytes) -> None:
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()

    return Handler


def start_http(relay: LiveRelay, port: int) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), _handler(relay))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="live-http", daemon=True).start()
    log.info("Escuta ao vivo em HTTP %d (ingress)", port)


class DeviceLink:
    """Sensor 'Ouvintes ao vivo' + link no dispositivo do StickS3, por MQTT discovery."""

    def __init__(self, relay: LiveRelay, mac: str, addon_slug: str) -> None:
        self.relay, self.mac, self.slug = relay, mac.lower(), addon_slug
        self.client = None
        self.base = f"sound_monitor/live/{self.mac.replace(':', '')}"
        self.disc = f"homeassistant/sensor/{self.mac.replace(':', '')}_ouvintes/config"

    def start(self) -> None:
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not (token and self.mac):
            return
        if not self.slug:
            try:
                req = urllib.request.Request("http://supervisor/addons/self/info",
                                             headers={"Authorization": f"Bearer {token}"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    self.slug = json.load(r)["data"]["slug"]
            except Exception as e:  # noqa: BLE001
                log.warning("Sem slug do app (%s)", e)
        try:
            req = urllib.request.Request("http://supervisor/services/mqtt",
                                         headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=10) as r:
                cfg = json.load(r)["data"]
        except Exception as e:  # noqa: BLE001
            log.warning("MQTT indisponível; sem entidade no dispositivo (%s)", e)
            return
        import paho.mqtt.client as mqtt

        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sound_monitor_live")
        c.username_pw_set(cfg.get("username"), cfg.get("password"))
        c.will_set(f"{self.base}/availability", "offline", retain=True)
        c.on_connect = lambda *a: self._on_connect()
        c.on_message = lambda _c, _u, m: self._on_ha_status(m)
        c.connect_async(cfg["host"], int(cfg.get("port", 1883)))
        c.loop_start()
        self.client = c
        self.relay.on_change = self.publish_state

    def _payload(self) -> dict:
        return {
            "name": "Ouvintes ao vivo",
            "unique_id": f"{self.mac.replace(':', '')}_ouvintes_ao_vivo",
            "object_id": "sticks3_ouvintes_ao_vivo",
            "icon": "mdi:ear-hearing",
            "state_topic": f"{self.base}/listeners",
            "availability_topic": f"{self.base}/availability",
            "state_class": "measurement",
            "device": {
                "connections": [["mac", self.mac]],
                "configuration_url": f"homeassistant://app/{self.slug}",
            },
        }

    def _on_connect(self) -> None:
        self.client.subscribe("homeassistant/status")
        self.announce()

    def _on_ha_status(self, msg) -> None:
        if msg.payload == b"online":
            threading.Timer(20, self.announce, kwargs={"reset": True}).start()

    def announce(self, reset: bool = False) -> None:
        """(Re)publica o discovery. reset=True apaga e recria, para regravar o link do
        dispositivo depois que a integração ESPHome o sobrescreve (ela grava None ao reconectar)."""
        if not self.client:
            return
        if reset:
            self.client.publish(self.disc, "", retain=True)
            time.sleep(2)
        self.client.publish(self.disc, json.dumps(self._payload()), retain=True)
        self.client.publish(f"{self.base}/availability", "online", retain=True)
        self.publish_state(self.relay.listeners)

    def publish_state(self, n: int) -> None:
        if self.client:
            self.client.publish(f"{self.base}/listeners", str(n), retain=True)
