#pragma once
#include <condition_variable>
#include <functional>
#include <mutex>
#include <queue>
#include <thread>
#include <vector>

namespace gatherly {
// Keep blocking PostgreSQL/PBKDF2 work off Drogon's event loops. Bound queued work.
class WorkerPool {
public:
    explicit WorkerPool(size_t count) {
        for (size_t i = 0; i < count; ++i) threads_.emplace_back([this] {
            for (;;) {
                std::function<void()> task;
                {
                    std::unique_lock lock(mutex_);
                    ready_.wait(lock, [&] { return stopping_ || !tasks_.empty(); });
                    if (stopping_ && tasks_.empty()) return;
                    task = std::move(tasks_.front()); tasks_.pop();
                }
                task();
            }
        });
    }
    ~WorkerPool() {
        { std::lock_guard lock(mutex_); stopping_ = true; }
        ready_.notify_all();
        for (auto& thread : threads_) thread.join();
    }
    bool Submit(std::function<void()> task) {
        { std::lock_guard lock(mutex_);
          if (stopping_ || tasks_.size() >= 32) return false;
          tasks_.push(std::move(task)); }
        ready_.notify_one(); return true;
    }
private:
    std::mutex mutex_;
    std::condition_variable ready_;
    bool stopping_ = false;
    std::queue<std::function<void()>> tasks_;
    std::vector<std::thread> threads_;
};
}
