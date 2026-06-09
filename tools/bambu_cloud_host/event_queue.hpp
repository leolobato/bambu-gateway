// SPDX-License-Identifier: MIT
// Thread-safe queue of plugin-callback events, drained by bridge.poll_events.
#pragma once

#include <deque>
#include <mutex>
#include <vector>

#include "third_party/nlohmann/json.hpp"

namespace bambu_host {

using json = nlohmann::json;

class EventQueue {
 public:
  // Bounds: a stalled Python poller must not grow this process without
  // limit (each subscribed printer pushes a multi-KB report every ~1s).
  // Newer status supersedes older, so dropping the OLDEST entry is safe.
  static constexpr std::size_t kMaxQueued = 1000;
  // Cap per drain so one bridge.poll_events response line stays well under
  // the parent's stream limit even with large push_all payloads queued.
  static constexpr std::size_t kMaxDrain = 200;

  void push(json event) {
    std::lock_guard<std::mutex> g(m_);
    if (q_.size() >= kMaxQueued) {
      q_.pop_front();
    }
    q_.push_back(std::move(event));
  }

  // Drain up to kMaxDrain buffered events, atomically (oldest first).
  // Call again to fetch the rest; the pump polls continuously.
  std::vector<json> drain() {
    std::lock_guard<std::mutex> g(m_);
    const std::size_t n = q_.size() < kMaxDrain ? q_.size() : kMaxDrain;
    std::vector<json> out(q_.begin(), q_.begin() + n);
    q_.erase(q_.begin(), q_.begin() + n);
    return out;
  }

  std::size_t size() const {
    std::lock_guard<std::mutex> g(m_);
    return q_.size();
  }

 private:
  mutable std::mutex m_;
  std::deque<json> q_;
};

// Process-global instance. The plugin callbacks push into this; the
// bridge.poll_events RPC method drains it.
EventQueue& global_event_queue();

}  // namespace bambu_host
