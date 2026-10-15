#pragma once

#include <cstdint>
#include <string>

// Cuts at a UTF-8 character boundary: an invalid sequence breaks protobuf decoding in Home Assistant.
inline std::string utf8_truncate(const std::string &s, size_t max_bytes) {
  if (s.size() <= max_bytes) {
    return s;
  }
  size_t n = max_bytes;
  while (n > 0 && (static_cast<uint8_t>(s[n]) & 0xC0) == 0x80) {
    n--;
  }
  return s.substr(0, n);
}
