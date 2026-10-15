#pragma once

#ifdef USE_ESP32

#include "esphome/components/microphone/microphone_source.h"
#include "esphome/core/component.h"

#include <freertos/FreeRTOS.h>
#include <freertos/stream_buffer.h>
#include <lwip/sockets.h>

#include <atomic>
#include <string>

namespace esphome::audio_udp_stream {

/// Streams raw 16 kHz / 16-bit / mono PCM from a passive microphone source to a UDP endpoint.
class AudioUdpStream : public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::AFTER_WIFI; }

  void set_microphone_source(microphone::MicrophoneSource *source) { this->microphone_source_ = source; }
  void set_host(const std::string &host) { this->host_ = host; }
  void set_port(uint16_t port) { this->port_ = port; }

  void set_enabled(bool enabled) { this->enabled_ = enabled; }
  bool is_enabled() const { return this->enabled_; }
  bool is_streaming() const { return this->enabled_ && this->microphone_source_->is_running(); }

 protected:
  microphone::MicrophoneSource *microphone_source_{nullptr};
  std::string host_;
  uint16_t port_{5050};

  StaticStreamBuffer_t buffer_struct_{};
  StreamBufferHandle_t buffer_{nullptr};
  struct sockaddr_in destination_{};
  int socket_fd_{-1};

  // Written from the microphone task, read from the main loop.
  std::atomic<bool> enabled_{true};
  std::atomic<uint32_t> dropped_bytes_{0};
  uint32_t last_drop_report_ms_{0};
};

}  // namespace esphome::audio_udp_stream

#endif  // USE_ESP32
