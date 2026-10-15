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
import os
import socket
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
from ai_edge_litert.interpreter import Interpreter

SAMPLE_RATE = 16000
WINDOW_SAMPLES = 15600  # entrada fixa do YAMNet: 0,975 s
PACKET_SAMPLES = 512  # mesmo tamanho de datagrama que o firmware envia

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


@dataclass
class Settings:
    hop_seconds: float
    clip_seconds: float
    min_rms_dbfs: float
    categories: list[Category]


def load_settings(path: Path, class_names: list[str]) -> Settings:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    index = {name: i for i, name in enumerate(class_names)}
    categories = []
    for name, conf in raw["categories"].items():
        missing = [label for label in conf["labels"] if label not in index]
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
            )
        )
    return Settings(
        hop_seconds=raw["hop_seconds"],
        clip_seconds=raw["clip_seconds"],
        min_rms_dbfs=raw["min_rms_dbfs"],
        categories=categories,
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

    def push(self, pcm: np.ndarray) -> bool:
        """Acrescenta amostras; devolve True quando é hora de rodar a inferência."""
        n = len(pcm)
        self.buffer = np.roll(self.buffer, -n)
        self.buffer[-n:] = pcm
        self.samples_since_inference += n
        self.received_samples += n
        hop = int(self.settings.hop_seconds * SAMPLE_RATE)
        if self.samples_since_inference >= hop:
            self.samples_since_inference = 0
            return True
        return False

    def window(self) -> np.ndarray:
        return self.buffer[-WINDOW_SAMPLES:]

    def evaluate(self, scores: np.ndarray, now: float) -> list[dict]:
        """Atualiza a votação por categoria e devolve os eventos confirmados."""
        events = []
        for category in self.settings.categories:
            category_scores = scores[category.label_indices]
            best = int(np.argmax(category_scores))
            score = float(category_scores[best])
            self.history[category.name].append(score >= category.threshold)
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
            log.info("%s: %.1f s de áudio recebidos em %.0f s", self.address, audio_seconds, elapsed)
            self.received_samples = 0
            self.stats_since = now


class Notifier:
    def __init__(self, ha_url: str | None, webhook_id: str | None, clips_dir: Path | None):
        self.webhook_url = f"{ha_url.rstrip('/')}/api/webhook/{webhook_id}" if ha_url and webhook_id else None
        self.clips_dir = clips_dir
        if clips_dir:
            clips_dir.mkdir(parents=True, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=2)
        if not self.webhook_url:
            log.warning("HA_URL/HA_WEBHOOK_ID não definidos: eventos só serão registrados no log")

    def submit(self, event: dict, clip: np.ndarray) -> None:
        self.executor.submit(self._handle, event, clip.copy())

    def _handle(self, event: dict, clip: np.ndarray) -> None:
        if self.clips_dir:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = self.clips_dir / f"{stamp}_{event['category']}_{event['score']:.2f}.wav"
            write_wav(path, clip)
            event["clip"] = path.name
        if not self.webhook_url:
            return
        request = urllib.request.Request(
            self.webhook_url,
            data=json.dumps(event).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                log.debug("Webhook respondeu %s", response.status)
        except (urllib.error.URLError, TimeoutError) as err:
            log.error("Falha ao chamar o webhook do Home Assistant: %s", err)


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
    notifier = Notifier(
        os.environ.get("HA_URL"),
        os.environ.get("HA_WEBHOOK_ID"),
        Path(os.environ["CLIPS_DIR"]) if os.environ.get("CLIPS_DIR") else None,
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    sock.bind(("0.0.0.0", args.port))
    log.info("Escutando PCM 16 kHz/16 bit/mono em UDP %d", args.port)
    log.info("Categorias: %s", ", ".join(c.name for c in settings.categories))

    streams: dict[str, DeviceStream] = {}
    while True:
        payload, (address, _) = sock.recvfrom(4096)
        if len(payload) % 2:
            continue
        stream = streams.get(address)
        if stream is None:
            log.info("Novo dispositivo transmitindo: %s", address)
            stream = streams[address] = DeviceStream(address, settings)

        now = time.monotonic()
        stream.report_stats(now)
        if not stream.push(np.frombuffer(payload, dtype="<i2")):
            continue

        window = stream.window()
        level = rms_dbfs(window)
        if level < settings.min_rms_dbfs:
            continue
        scores = model.scores(window)
        if log.isEnabledFor(logging.DEBUG):
            top = np.argsort(scores)[::-1][:3]
            log.debug(
                "%s %.0f dBFS | %s",
                address,
                level,
                ", ".join(f"{model.class_names[i]}={scores[i]:.2f}" for i in top),
            )
        for event in stream.evaluate(scores, now):
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
        for event in stream.evaluate(scores, start / SAMPLE_RATE):
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=os.environ.get("MODEL_PATH", "model/yamnet.tflite"))
    parser.add_argument("--class-map", default=os.environ.get("CLASS_MAP_PATH", "model/yamnet_class_map.csv"))
    parser.add_argument("--config", default=os.environ.get("CONFIG_PATH", "config.toml"))
    sub = parser.add_subparsers(dest="mode", required=True)
    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--port", type=int, default=int(os.environ.get("UDP_PORT", "5050")))
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
    model = YamNet(Path(args.model), Path(args.class_map))
    settings = load_settings(Path(args.config), model.class_names)
    if args.mode == "serve":
        serve(args, model, settings)
    else:
        classify(args, model, settings)


if __name__ == "__main__":
    main()
