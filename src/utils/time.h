#pragma once
#include <cstdint>
#include <string>
namespace gatherly {
int64_t TimestampMicros(const std::string& value);
std::string NormalizeTimestamp(const std::string& value);
}
