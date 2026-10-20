"""Classifica áudio PCM recebido por UDP com YAMNet e avisa o Home Assistant sobre sons perigosos.

Modos:
  serve               escuta o stream UDP do CoreS3 (padrão no container)
  classify ARQ.wav    mostra, janela a janela, as classes e as pontuações por categoria
  send ARQ.wav        transmite um WAV por UDP em tempo real, simulando o CoreS3
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import logging
import logging.handlers
import os
import shutil
import socket
import threading
import time
import tomllib
import urllib.error
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

import live
from ai_edge_litert.interpreter import Interpreter

SAMPLE_RATE = 16000
WINDOW_SAMPLES = 15600  # entrada fixa do YAMNet: 0,975 s
PACKET_SAMPLES = 512  # mesmo tamanho de datagrama que o firmware envia
EVENT_TYPE = "monitor_perigo"
APP_OPTIONS_PATH = Path("/data/options.json")
BUNDLED_CONFIG_PATH = Path(__file__).with_name("config.toml")

log = logging.getLogger("sound-monitor")


@dataclass
class Category:
    name: str
    labels: list[str]
    threshold: float
    min_hits: int
    window: int
    cooldown_seconds: float
    label_indices: list[int] = field(default_factory=list)
    # Filtro de pássaros (periquitos): a janela não conta como acerto quando as classes
    # de veto dominam (veto >= veto_min e veto >= score * veto_ratio), a menos que algum
    # rótulo "forte" (toque tonal real) passe de strong_threshold.
    veto_labels: list[str] = field(default_factory=list)
    veto_indices: list[int] = field(default_factory=list)
    veto_min: float = 0.15
    veto_ratio: float = 1.0
    strong_labels: list[str] = field(default_factory=list)
    strong_indices: list[int] = field(default_factory=list)
    strong_threshold: float = 1.1
    # Rejeita bipes curtos: com min_active_ms > 0, a janela do acerto precisa ter pelo menos
    # esse tempo (em blocos de 50 ms) a até active_db dB do pico da janela.
    min_active_ms: int = 0
    active_db: float = 10.0


@dataclass
class Settings:
    hop_seconds: float
    clip_seconds: float
    min_rms_dbfs: float
    categories: list[Category]
    # Só aceita áudio destes IPs (vazio = todos). Evita fontes espúrias (ex.: roteador).
    allowed_sources: list[str] = field(default_factory=list)
    # Exige áudio contínuo: fração mínima da janela recebida nos últimos ~1,5 s.
    min_fresh_fraction: float = 0.9
    # Loga as classes principais quando uma categoria chega a esta fração do limiar.
    near_miss_fraction: float = 0.0
    # Arquivo de log persistente (rotativo, 5 MB x 3), ex.: /media/sound-monitor/monitor.log
    log_file: str = ""
    # Categorias cujos "quase" também salvam um WAV (prefixo quase_), no máx. 1 a cada N s.
    near_miss_clips: list[str] = field(default_factory=list)
    near_miss_clip_cooldown: float = 10.0


def load_settings(path: Path, class_names: list[str]) -> Settings:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    index = {name: i for i, name in enumerate(class_names)}
    categories = []
    for name, conf in raw["categories"].items():
        extra = conf.get("veto_labels", []) + conf.get("strong_labels", [])
        missing = [label for label in conf["labels"] + extra if label not in index]
        if missing:
            raise ValueError(f"Categoria '{name}': rótulos inexistentes no YAMNet: {missing}")
        if conf["min_hits"] > conf["window"]:
            raise ValueError(f"Categoria '{name}': min_hits não pode ser maior que window")
        categories.append(
            Category(
                name=name,
                labels=conf["labels"],
                threshold=conf["threshold"],
                min_hits=conf["min_hits"],
                window=conf["window"],
                cooldown_seconds=conf["cooldown_seconds"],
                label_indices=[index[label] for label in conf["labels"]],
                veto_labels=conf.get("veto_labels", []),
                veto_indices=[index[label] for label in conf.get("veto_labels", [])],
                veto_min=conf.get("veto_min", 0.15),
                veto_ratio=conf.get("veto_ratio", 1.0),
                strong_labels=conf.get("strong_labels", []),
                strong_indices=[index[label] for label in conf.get("strong_labels", [])],
                strong_threshold=conf.get("strong_threshold", 1.1),
                min_active_ms=int(conf.get("min_active_ms", 0)),
                active_db=float(conf.get("active_db", 10.0)),
            )
        )
    return Settings(
        hop_seconds=raw["hop_seconds"],
        clip_seconds=raw["clip_seconds"],
        min_rms_dbfs=raw["min_rms_dbfs"],
        categories=categories,
        allowed_sources=raw.get("allowed_sources", []),
        min_fresh_fraction=raw.get("min_fresh_fraction", 0.9),
        near_miss_fraction=raw.get("near_miss_fraction", 0.0),
        log_file=raw.get("log_file", ""),
        near_miss_clips=raw.get("near_miss_clips", []),
        near_miss_clip_cooldown=raw.get("near_miss_clip_cooldown", 10.0),
    )


class YamNet:
    def __init__(self, model_path: Path, class_map_path: Path):
        self.interpreter = Interpreter(model_path=str(model_path))
        input_detail = self.interpreter.get_input_details()[0]
        self.input_index = input_detail["index"]
        self.input_shape = tuple(input_detail["shape"])
        self.output_index = self.interpreter.get_output_details()[0]["index"]
        self.interpreter.allocate_tensors()
        with class_map_path.open(encoding="utf-8") as f:
            self.class_names = [row["display_name"] for row in csv.DictReader(f)]

    def scores(self, samples: np.ndarray) -> np.ndarray:
        waveform = (samples.astype(np.float32) / 32768.0).reshape(self.input_shape)
        self.interpreter.set_tensor(self.input_index, waveform)
        self.interpreter.invoke()
        return self.interpreter.get_tensor(self.output_index).reshape(-1)


def active_ms(samples: np.ndarray, below_peak_db: float) -> int:
    """Tempo (ms) da janela em blocos de 50 ms com energia a até below_peak_db dB do pico."""
    block = SAMPLE_RATE // 20
    n = len(samples) // block
    if n == 0:
        return 0
    frames = samples[: n * block].astype(np.float64).reshape(n, block)
    db = 10 * np.log10(np.mean(frames**2, axis=1) + 1e-9)
    return int(np.sum(db >= db.max() - below_peak_db) * 50)


def rms_dbfs(samples: np.ndarray) -> float:
    rms = np.sqrt(np.mean(np.square(samples.astype(np.float32) / 32768.0)))
    return 20 * np.log10(max(rms, 1e-9))


class DeviceStream:
    """Estado por dispositivo (identificado pelo IP de origem)."""

    def __init__(self, address: str, settings: Settings):
        self.address = address
        self.settings = settings
        capacity = max(WINDOW_SAMPLES, int(settings.clip_seconds * SAMPLE_RATE))
        self.buffer = np.zeros(capacity, dtype=np.int16)
        self.samples_since_inference = 0
        self.history = {c.name: collections.deque(maxlen=c.window) for c in settings.categories}
        self.best = {c.name: collections.deque(maxlen=c.window) for c in settings.categories}
        self.last_fired = {c.name: float("-inf") for c in settings.categories}
        self.received_samples = 0
        self.stats_since = time.monotonic()
        self.recent = collections.deque()  # (instante, amostras) dos últimos pacotes
        self.vetoed = {c.name: 0 for c in settings.categories}

    def push(self, pcm: np.ndarray) -> bool:
        """Acrescenta amostras; devolve True quando é hora de rodar a inferência."""
        n = len(pcm)
        now = time.monotonic()
        self.recent.append((now, n))
        while self.recent and now - self.recent[0][0] > 1.5:
            self.recent.popleft()
        self.buffer = np.roll(self.buffer, -n)
        self.buffer[-n:] = pcm
        self.samples_since_inference += n
        self.received_samples += n
        hop = int(self.settings.hop_seconds * SAMPLE_RATE)
        if self.samples_since_inference >= hop:
            self.samples_since_inference = 0
            return True
        return False

    def fresh(self) -> bool:
        """True se a janela inteira veio de áudio contínuo (não de pacotes soltos)."""
        got = sum(n for _, n in self.recent)
        return got >= WINDOW_SAMPLES * self.settings.min_fresh_fraction

    def window(self) -> np.ndarray:
        return self.buffer[-WINDOW_SAMPLES:]

    def evaluate(self, scores: np.ndarray, now: float, window: np.ndarray | None = None) -> list[dict]:
        """Atualiza a votação por categoria e devolve os eventos confirmados."""
        events = []
        for category in self.settings.categories:
            category_scores = scores[category.label_indices]
            best = int(np.argmax(category_scores))
            score = float(category_scores[best])
            hit = score >= category.threshold
            if hit and category.veto_indices:
                veto = float(scores[category.veto_indices].max())
                strong = float(scores[category.strong_indices].max()) if category.strong_indices else 0.0
                if veto >= category.veto_min and veto >= score * category.veto_ratio and strong < category.strong_threshold:
                    hit = False
                    self.vetoed[category.name] += 1
                    log.info("%s: %s vetado (%s=%.2f, pássaros=%.2f)", self.address, category.name,
                             category.labels[best], score, veto)
            if hit and category.min_active_ms > 0 and window is not None:
                strong = float(scores[category.strong_indices].max()) if category.strong_indices else 0.0
                dur = active_ms(window, category.active_db)
                if dur < category.min_active_ms and strong < category.strong_threshold:
                    hit = False
                    log.info("%s: %s ignorado, som curto (%s=%.2f, %d ms < %d ms)", self.address, category.name,
                             category.labels[best], score, dur, category.min_active_ms)
            self.history[category.name].append(hit)
            self.best[category.name].append((score, category.labels[best]))

            hits = sum(self.history[category.name])
            cooling_down = now - self.last_fired[category.name] < category.cooldown_seconds
            if hits >= category.min_hits and not cooling_down:
                self.last_fired[category.name] = now
                peak_score, peak_label = max(self.best[category.name])
                self.history[category.name].clear()
                events.append(
                    {
                        "category": category.name,
                        "label": peak_label,
                        "score": round(peak_score, 3),
                        "device": self.address,
                        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                    }
                )
        return events

    def report_stats(self, now: float) -> None:
        elapsed = now - self.stats_since
        if elapsed >= 60:
            audio_seconds = self.received_samples / SAMPLE_RATE
            vetos = ", ".join(f"{k}={v}" for k, v in self.vetoed.items() if v)
            log.info("%s: %.1f s de áudio recebidos em %.0f s%s", self.address, audio_seconds, elapsed,
                     f" | vetos: {vetos}" if vetos else "")
            self.vetoed = {k: 0 for k in self.vetoed}
            self.received_samples = 0
            self.stats_since = now


class Notifier:
    """Entrega eventos ao Home Assistant e guarda o clipe de áudio de cada um.

    Rodando como app do HA OS, dispara o evento `monitor_perigo` pela API interna do Supervisor.
    Fora dele, chama o webhook configurado em HA_URL/HA_WEBHOOK_ID.
    """

    def __init__(self, clips_dir: Path | None, max_clips: int):
        self.clips_dir = clips_dir
        self.max_clips = max_clips
        if clips_dir:
            clips_dir.mkdir(parents=True, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=2)

        self.headers = {"Content-Type": "application/json"}
        if token := os.environ.get("SUPERVISOR_TOKEN"):
            self.url = f"http://supervisor/core/api/events/{EVENT_TYPE}"
            self.headers["Authorization"] = f"Bearer {token}"
            log.info("Eventos serão disparados como '%s' pela API do Supervisor", EVENT_TYPE)
        elif (ha_url := os.environ.get("HA_URL")) and (webhook_id := os.environ.get("HA_WEBHOOK_ID")):
            self.url = f"{ha_url.rstrip('/')}/api/webhook/{webhook_id}"
            log.info("Eventos serão enviados ao webhook do Home Assistant em %s", ha_url)
        else:
            self.url = None
            log.warning("Sem SUPERVISOR_TOKEN nem HA_URL/HA_WEBHOOK_ID: eventos só serão registrados no log")

    def submit(self, event: dict, clip: np.ndarray) -> None:
        self.executor.submit(self._handle, event, clip.copy())

    def _handle(self, event: dict, clip: np.ndarray) -> None:
        if self.clips_dir:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = self.clips_dir / f"{stamp}_{event['category']}_{event['score']:.2f}.wav"
            write_wav(path, clip)
            event["clip"] = path.name
            self._prune_clips()
        if not self.url:
            return
        request = urllib.request.Request(self.url, data=json.dumps(event).encode(), headers=self.headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                log.debug("Home Assistant respondeu %s", response.status)
        except (urllib.error.URLError, TimeoutError) as err:
            log.error("Falha ao avisar o Home Assistant: %s", err)

    def save_clip(self, suffix: str, clip: np.ndarray) -> None:
        if self.clips_dir:
            self.executor.submit(self._save, suffix, clip.copy())

    def _save(self, suffix: str, clip: np.ndarray) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        write_wav(self.clips_dir / f"{stamp}_{suffix}.wav", clip)
        self._prune_clips()

    def _prune_clips(self) -> None:
        # Nomes começam com data/hora, então a ordem alfabética é a cronológica.
        clips = sorted(self.clips_dir.glob("*.wav"))
        for old in clips[: max(0, len(clips) - self.max_clips)]:
            old.unlink(missing_ok=True)


def write_wav(path: Path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(samples.astype("<i2").tobytes())


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as f:
        if f.getsampwidth() != 2:
            raise ValueError("Use WAV PCM de 16 bits")
        channels, rate = f.getnchannels(), f.getframerate()
        data = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2")
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        positions = np.arange(0, len(data), rate / SAMPLE_RATE)
        data = np.interp(positions, np.arange(len(data)), data)
    return data.astype(np.int16)


def serve(args, model: YamNet, settings: Settings) -> None:
    if settings.log_file:
        Path(settings.log_file).parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(settings.log_file, maxBytes=5_000_000, backupCount=3,
                                                       encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
        log.info("Log persistente em %s", settings.log_file)
    notifier = Notifier(
        Path(os.environ["CLIPS_DIR"]) if os.environ.get("CLIPS_DIR") else None,
        int(os.environ.get("MAX_CLIPS", "200")),
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    sock.bind(("0.0.0.0", args.port))
    log.info("Escutando PCM 16 kHz/16 bit/mono em UDP %d", args.port)
    log.info("Categorias: %s", ", ".join(c.name for c in settings.categories))

    relay = live.LiveRelay()
    link = None
    if args.live_port:
        live.start_http(relay, args.live_port)
        link = live.DeviceLink(relay, os.environ.get("LIVE_DEVICE_MAC", ""), os.environ.get("LIVE_ADDON_SLUG", ""))
        threading.Thread(target=link.start, name="live-mqtt", daemon=True).start()
    last_packet: dict[str, float] = {}

    streams: dict[str, DeviceStream] = {}
    ignored: set[str] = set()
    last_near_clip = float("-inf")
    while True:
        payload, (address, _) = sock.recvfrom(4096)
        if len(payload) % 2:
            continue
        if settings.allowed_sources and address not in settings.allowed_sources:
            if address not in ignored:
                ignored.add(address)
                log.info("Ignorando áudio de %s (fora de allowed_sources)", address)
            continue
        if relay.active:
            relay.feed(address, payload)
        now = time.monotonic()
        if link and now - last_packet.get(address, now - 31) > 30:
            # stream voltou (StickS3 reiniciou): a ESPHome regrava o dispositivo; refaz o link depois
            threading.Timer(25, link.announce, kwargs={"reset": True}).start()
        last_packet[address] = now
        stream = streams.get(address)
        if stream is None:
            log.info("Novo dispositivo transmitindo: %s", address)
            stream = streams[address] = DeviceStream(address, settings)

        now = time.monotonic()
        stream.report_stats(now)
        if not stream.push(np.frombuffer(payload, dtype="<i2")):
            continue

        if not stream.fresh():
            continue  # pacotes soltos: o buffer é velho e daria falso positivo
        window = stream.window()
        level = rms_dbfs(window)
        if level < settings.min_rms_dbfs:
            continue
        scores = model.scores(window)
        if settings.near_miss_fraction > 0 and not log.isEnabledFor(logging.DEBUG):
            near = [(c, float(scores[c.label_indices].max())) for c in settings.categories]
            near = [(c, sc) for c, sc in near if sc >= c.threshold * settings.near_miss_fraction]
            if near:
                top = np.argsort(scores)[::-1][:5]
                log.info("%s %.0f dBFS quase %s | %d ms ativos | %s", address, level,
                         ", ".join(f"{c.name}={sc:.2f}" for c, sc in near), active_ms(window, 10.0),
                         ", ".join(f"{model.class_names[i]}={scores[i]:.2f}" for i in top))
                names = [c.name for c, _ in near if c.name in settings.near_miss_clips]
                if names and now - last_near_clip >= settings.near_miss_clip_cooldown:
                    last_near_clip = now
                    notifier.save_clip(f"quase_{names[0]}_{max(sc for c, sc in near if c.name == names[0]):.2f}", stream.buffer)
        if log.isEnabledFor(logging.DEBUG):
            top = np.argsort(scores)[::-1][:3]
            log.debug(
                "%s %.0f dBFS | %s",
                address,
                level,
                ", ".join(f"{model.class_names[i]}={scores[i]:.2f}" for i in top),
            )
        for event in stream.evaluate(scores, now, window):
            log.warning("PERIGO %s (%s, %.2f) em %s", event["category"], event["label"], event["score"], address)
            notifier.submit(event, stream.buffer)


def classify(args, model: YamNet, settings: Settings) -> None:
    samples = read_wav(Path(args.file))
    if len(samples) < WINDOW_SAMPLES:
        samples = np.pad(samples, (0, WINDOW_SAMPLES - len(samples)))
    hop = int(settings.hop_seconds * SAMPLE_RATE)
    stream = DeviceStream("arquivo", settings)
    for start in range(0, len(samples) - WINDOW_SAMPLES + 1, hop):
        window = samples[start : start + WINDOW_SAMPLES]
        scores = model.scores(window)
        top = np.argsort(scores)[::-1][:5]
        per_category = {
            c.name: float(scores[c.label_indices].max()) for c in settings.categories
        }
        print(f"t={start / SAMPLE_RATE:6.2f}s {rms_dbfs(window):5.0f} dBFS")
        print("   top: " + ", ".join(f"{model.class_names[i]}={scores[i]:.2f}" for i in top))
        print("   categorias: " + ", ".join(f"{k}={v:.2f}" for k, v in per_category.items()))
        for event in stream.evaluate(scores, start / SAMPLE_RATE, window):
            print(f"   >>> EVENTO {event['category']} ({event['label']}, {event['score']:.2f})")


def send(args) -> None:
    samples = read_wav(Path(args.file))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = PACKET_SAMPLES / SAMPLE_RATE
    next_time = time.monotonic()
    for start in range(0, len(samples), PACKET_SAMPLES):
        sock.sendto(samples[start : start + PACKET_SAMPLES].astype("<i2").tobytes(), (args.host, args.port))
        next_time += interval
        time.sleep(max(0.0, next_time - time.monotonic()))
    print(f"Enviados {len(samples) / SAMPLE_RATE:.1f} s de áudio para {args.host}:{args.port}")


def apply_app_options() -> None:
    """No HA OS, as opções da aba Configuração do app chegam em /data/options.json."""
    if not APP_OPTIONS_PATH.exists():
        return
    options = json.loads(APP_OPTIONS_PATH.read_text(encoding="utf-8"))
    os.environ["LOG_LEVEL"] = options.get("log_level", "INFO")
    os.environ["MAX_CLIPS"] = str(options.get("max_clips", 200))
    os.environ.setdefault("LIVE_DEVICE_MAC", options.get("live_device_mac", ""))


def ensure_config(path: Path) -> None:
    """Na primeira execução, copia os parâmetros padrão para onde o usuário pode editá-los."""
    if not path.exists() and path.resolve() != BUNDLED_CONFIG_PATH.resolve():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BUNDLED_CONFIG_PATH, path)
        log.info("Parâmetros padrão copiados para %s", path)


def main() -> None:
    apply_app_options()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=os.environ.get("MODEL_PATH", "model/yamnet.tflite"))
    parser.add_argument("--class-map", default=os.environ.get("CLASS_MAP_PATH", "model/yamnet_class_map.csv"))
    parser.add_argument("--config", default=os.environ.get("CONFIG_PATH", "config.toml"))
    sub = parser.add_subparsers(dest="mode", required=True)
    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--port", type=int, default=int(os.environ.get("UDP_PORT", "5050")))
    serve_parser.add_argument("--live-port", type=int, default=int(os.environ.get("LIVE_PORT", "0")),
                              help="porta HTTP da escuta ao vivo (0 = desligada)")
    classify_parser = sub.add_parser("classify")
    classify_parser.add_argument("file")
    send_parser = sub.add_parser("send")
    send_parser.add_argument("file")
    send_parser.add_argument("--host", required=True)
    send_parser.add_argument("--port", type=int, default=5050)
    args = parser.parse_args()

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.mode == "send":
        send(args)
        return
    ensure_config(Path(args.config))
    model = YamNet(Path(args.model), Path(args.class_map))
    settings = load_settings(Path(args.config), model.class_names)
    if args.mode == "serve":
        serve(args, model, settings)
    else:
        classify(args, model, settings)


if __name__ == "__main__":
    main()
