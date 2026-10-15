#include "audio_udp_stream.h"

#ifdef USE_ESP32

#include "esphome/components/network/util.h"
#include "esphome/core/hal.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"

#include <unistd.h>

#include <cerrno>

namespace esphome::audio_udp_stream {

static const char *const TAG = "audio_udp_stream";

// 512 samples (32 ms) per datagram keeps every packet well under the 1472-byte UDP MTU payload.
static const size_t PACKET_BYTES = 1024;
static const size_t BUFFER_BYTES = 16 * 1024;
static const uint8_t MAX_PACKETS_PER_LOOP = 8;
static const uint32_t DROP_REPORT_INTERVAL_MS = 10000;

void AudioUdpStream::setup() {
  RAMAllocator<uint8_t> allocator;
  // FreeRTOS stream buffers need one byte more than their usable capacity.
  uint8_t *storage = allocator.allocate(BUFFER_BYTES + 1);
  if (storage == nullptr) {
    ESP_LOGE(TAG, "Failed to allocate audio buffer");
    this->mark_failed();
    return;
  }
  this->buffer_ = xStreamBufferCreateStatic(BUFFER_BYTES, 1, storage, &this->buffer_struct_);

  this->destination_.sin_family = AF_INET;
  this->destination_.sin_port = htons(this->port_);
  if (inet_pton(AF_INET, this->host_.c_str(), &this->destination_.sin_addr) != 1) {
    ESP_LOGE(TAG, "Invalid host address: %s", this->host_.c_str());
    this->mark_failed();
    return;
  }

  this->microphone_source_->add_data_callback([this](const std::vector<uint8_t> &data) {
    if (!this->enabled_ || data.empty()) {
      return;
    }
    size_t written = xStreamBufferSend(this->buffer_, data.data(), data.size(), 0);
    if (written < data.size()) {
      this->dropped_bytes_ += data.size() - written;
    }
  });
}

void AudioUdpStream::loop() {
  if (!this->enabled_ || !network::is_connected()) {
    xStreamBufferReset(this->buffer_);
    return;
  }

  if (this->socket_fd_ < 0) {
    this->socket_fd_ = ::socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (this->socket_fd_ < 0) {
      ESP_LOGW(TAG, "Could not create UDP socket (errno %d)", errno);
      return;
    }
  }

  uint8_t packet[PACKET_BYTES];
  for (uint8_t i = 0; i < MAX_PACKETS_PER_LOOP; i++) {
    if (xStreamBufferBytesAvailable(this->buffer_) < PACKET_BYTES) {
      break;
    }
    size_t length = xStreamBufferReceive(this->buffer_, packet, PACKET_BYTES, 0);
    ssize_t sent = ::sendto(this->socket_fd_, packet, length, 0,
                            reinterpret_cast<const struct sockaddr *>(&this->destination_), sizeof(this->destination_));
    if (sent < 0) {
      // ENOMEM means the Wi-Fi TX queue is full; drop this packet and retry on the next loop.
      if (errno != ENOMEM) {
        ESP_LOGW(TAG, "sendto failed (errno %d); recreating socket", errno);
        ::close(this->socket_fd_);
        this->socket_fd_ = -1;
      }
      break;
    }
  }

  const uint32_t now = millis();
  if (now - this->last_drop_report_ms_ > DROP_REPORT_INTERVAL_MS) {
    this->last_drop_report_ms_ = now;
    uint32_t dropped = this->dropped_bytes_.exchange(0);
    if (dropped > 0) {
      ESP_LOGW(TAG, "Dropped %u bytes of audio in the last %u s", (unsigned) dropped,
               (unsigned) (DROP_REPORT_INTERVAL_MS / 1000));
    }
  }
}

void AudioUdpStream::dump_config() {
  ESP_LOGCONFIG(TAG,
                "Audio UDP stream:\n"
                "  Destination: %s:%u\n"
                "  Format: 16 kHz, 16-bit, mono PCM (little endian)",
                this->host_.c_str(), this->port_);
}

}  // namespace esphome::audio_udp_stream

#endif  // USE_ESP32
