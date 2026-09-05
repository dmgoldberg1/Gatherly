#include "utils/json.h"
#include <nlohmann/json.hpp>
#include <limits>
#include <stdexcept>

namespace gatherly {
namespace {
const Json kNull;
Json FromNative(const nlohmann::json& value) {
    if (value.is_null()) return nullptr;
    if (value.is_boolean()) return value.get<bool>();
    if (value.is_string()) return value.get<std::string>();
    if (value.is_number_unsigned()) {
        auto number = value.get<uint64_t>();
        if (number > uint64_t(std::numeric_limits<int64_t>::max())) throw std::invalid_argument("integer out of range");
        return int64_t(number);
    }
    if (value.is_number_integer()) return value.get<int64_t>();
    if (value.is_number_float()) return value.get<double>();
    if (value.is_array()) {
        Json::Array output;
        for (const auto& item : value) output.push_back(FromNative(item));
        return output;
    }
    Json::Object output;
    for (const auto& [key, item] : value.items()) output.emplace(key, FromNative(item));
    return output;
}
}

Json::Json() : value_(nullptr) {}
Json::Json(std::nullptr_t) : value_(nullptr) {}
Json::Json(bool value) : value_(value) {}
Json::Json(int64_t value) : value_(value) {}
Json::Json(double value) : value_(value) {}
Json::Json(const char* value) : value_(std::string(value)) {}
Json::Json(std::string value) : value_(std::move(value)) {}
Json::Json(Array value) : value_(std::move(value)) {}
Json::Json(Object value) : value_(std::move(value)) {}

bool Json::IsNull() const {
    return std::holds_alternative<std::nullptr_t>(value_);
}

bool Json::IsObject() const {
    return std::holds_alternative<Object>(value_);
}

bool Json::IsArray() const {
    return std::holds_alternative<Array>(value_);
}

const Json::Object& Json::AsObject() const {
    return std::get<Object>(value_);
}

const Json::Array& Json::AsArray() const {
    return std::get<Array>(value_);
}

std::string Json::AsString(const std::string& fallback) const {
    if (const auto* value = std::get_if<std::string>(&value_)) {
        return *value;
    }
    return fallback;
}

int64_t Json::AsInt(int64_t fallback) const {
    if (const auto* value = std::get_if<int64_t>(&value_)) return *value;
    if (std::holds_alternative<double>(value_)) throw std::invalid_argument("expected an integer");
    return fallback;
}

bool Json::AsBool(bool fallback) const {
    if (const auto* value = std::get_if<bool>(&value_)) {
        return *value;
    }
    return fallback;
}

const Json& Json::At(const std::string& key) const {
    if (!IsObject()) {
        return kNull;
    }
    const auto& object = AsObject();
    auto it = object.find(key);
    if (it == object.end()) {
        return kNull;
    }
    return it->second;
}

bool Json::Contains(const std::string& key) const {
    return IsObject() && AsObject().contains(key);
}

std::string Json::Dump() const {
    return std::visit([](const auto& value) -> std::string {
        using T = std::decay_t<decltype(value)>;
        if constexpr (std::is_same_v<T, Array>) {
            auto output = nlohmann::json::array();
            for (const auto& item : value) output.push_back(nlohmann::json::parse(item.Dump()));
            return output.dump();
        } else if constexpr (std::is_same_v<T, Object>) {
            auto output = nlohmann::json::object();
            for (const auto& [key, item] : value) output[key] = nlohmann::json::parse(item.Dump());
            return output.dump();
        } else { return nlohmann::json(value).dump(); }
    }, value_);
}

Json Json::Parse(const std::string& text) {
    if (text.empty()) return Object{};
    try { return FromNative(nlohmann::json::parse(text)); }
    catch (const nlohmann::json::exception&) { throw std::invalid_argument("invalid JSON"); }
}

Json ErrorJson(const std::string& message) {
    return Json::Object{{"error", message}};
}

Json OkJson(const std::string& message) {
    return Json::Object{{"message", message}};
}

} // namespace gatherly
