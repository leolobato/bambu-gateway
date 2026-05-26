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
  void push(json event) {
    std::lock_guard<std::mutex> g(m_);
    q_.push_back(std::move(event));
  }

  // Drain everything currently buffered, atomically.
  std::vector<json> drain() {
    std::lock_guard<std::mutex> g(m_);
    std::vector<json> out(q_.begin(), q_.end());
    q_.clear();
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
