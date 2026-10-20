FROM python:3.12-slim

ARG BUILD_VERSION=dev
ARG BUILD_ARCH=amd64
LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="${BUILD_ARCH}"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ADD https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/1/yamnet.tflite model/yamnet.tflite
ADD https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv model/yamnet_class_map.csv

COPY monitor.py live.py config.toml ./

ENV PYTHONUNBUFFERED=1 \
    CLIPS_DIR=/data/clips
EXPOSE 5050/udp

ENTRYPOINT ["python", "monitor.py"]
CMD ["serve"]
