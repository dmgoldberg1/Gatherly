#include "utils/json.h"

#include <cctype>
#include <cmath>
#include <sstream>
#include <stdexcept>

namespace gatherly {

namespace {

const Json kNull;

std::string EscapeJson(const std::string& value) {
    std::string result;
    for (char ch : value) {
        switch (ch) {
            case '"':
                result += "\\\"";
                break;
            case '\\':
                result += "\\\\";
                break;
            case '\n':
                result += "\\n";
                break;
            case '\r':
                result += "\\r";
                break;
            case '\t':
                result += "\\t";
                break;
            default:
                result += ch;
        }
    }
    return result;
}

class JsonParser {
public:
    explicit JsonParser(const std::string& text) : text_(text) {}

    Json Parse() {
        SkipSpaces();
        Json value = ParseValue();
        SkipSpaces();
        if (pos_ != text_.size()) {
            throw std::runtime_error("Unexpected JSON tail.");
        }
        return value;
    }

protected:
    Json ParseValue() {
        SkipSpaces();
        if (pos_ >= text_.size()) {
            throw std::runtime_error("Unexpected JSON end.");
        }
        const char ch = text_[pos_];
        if (ch == '{') {
            return ParseObject();
        }
        if (ch == '[') {
            return ParseArray();
        }
        if (ch == '"') {
            return ParseString();
        }
        if (ch == 't') {
            Expect("true");
            return Json(true);
        }
        if (ch == 'f') {
            Expect("false");
            return Json(false);
        }
        if (ch == 'n') {
            Expect("null");
            return Json(nullptr);
        }
        return ParseNumber();
    }

    Json ParseObject() {
        Expect("{");
        Json::Object object;
        SkipSpaces();
        if (TryConsume('}')) {
            return object;
        }
        while (true) {
            std::string key = ParseString().AsString();
            SkipSpaces();
            Expect(":");
            object[key] = ParseValue();
            SkipSpaces();
            if (TryConsume('}')) {
                break;
            }
            Expect(",");
        }
        return object;
    }

    Json ParseArray() {
        Expect("[");
        Json::Array array;
        SkipSpaces();
        if (TryConsume(']')) {
            return array;
        }
        while (true) {
            array.push_back(ParseValue());
            SkipSpaces();
            if (TryConsume(']')) {
                break;
            }
            Expect(",");
        }
        return array;
    }

    Json ParseString() {
        Expect("\"");
        std::string result;
        while (pos_ < text_.size()) {
            char ch = text_[pos_++];
            if (ch == '"') {
                return result;
            }
            if (ch == '\\') {
                if (pos_ >= text_.size()) {
                    throw std::runtime_error("Invalid JSON string escape.");
                }
                const char escaped = text_[pos_++];
                switch (escaped) {
                    case '"':
                    case '\\':
                    case '/':
                        result += escaped;
                        break;
                    case 'n':
                        result += '\n';
                        break;
                    case 'r':
                        result += '\r';
                        break;
                    case 't':
                        result += '\t';
                        break;
                    default:
                        result += escaped;
                }
                continue;
            }
            result += ch;
        }
        throw std::runtime_error("Unterminated JSON string.");
    }

    Json ParseNumber() {
        const size_t start = pos_;
        if (text_[pos_] == '-') {
            ++pos_;
        }
        while (pos_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[pos_]))) {
            ++pos_;
        }
        if (pos_ < text_.size() && text_[pos_] == '.') {
            ++pos_;
            while (pos_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[pos_]))) {
                ++pos_;
            }
        }
        return std::stod(text_.substr(start, pos_ - start));
    }

    void SkipSpaces() {
        while (pos_ < text_.size() && std::isspace(static_cast<unsigned char>(text_[pos_]))) {
            ++pos_;
        }
    }

    bool TryConsume(char ch) {
        SkipSpaces();
        if (pos_ < text_.size() && text_[pos_] == ch) {
            ++pos_;
            return true;
        }
        return false;
    }

    void Expect(const std::string& token) {
        SkipSpaces();
        if (text_.substr(pos_, token.size()) != token) {
            throw std::runtime_error("Expected JSON token: " + token);
        }
        pos_ += token.size();
    }

    const std::string& text_;
    size_t pos_ = 0;
};

} // namespace

Json::Json() : value_(nullptr) {}
Json::Json(std::nullptr_t) : value_(nullptr) {}
Json::Json(bool value) : value_(value) {}
Json::Json(int64_t value) : value_(static_cast<double>(value)) {}
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
    if (const auto* value = std::get_if<double>(&value_)) {
        return static_cast<int64_t>(*value);
    }
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
    if (std::holds_alternative<std::nullptr_t>(value_)) {
        return "null";
    }
    if (const auto* value = std::get_if<bool>(&value_)) {
        return *value ? "true" : "false";
    }
    if (const auto* value = std::get_if<double>(&value_)) {
        if (std::floor(*value) == *value) {
            return std::to_string(static_cast<int64_t>(*value));
        }
        std::ostringstream output;
        output << *value;
        return output.str();
    }
    if (const auto* value = std::get_if<std::string>(&value_)) {
        return "\"" + EscapeJson(*value) + "\"";
    }
    if (const auto* array = std::get_if<Array>(&value_)) {
        std::string result = "[";
        for (size_t i = 0; i < array->size(); ++i) {
            if (i > 0) {
                result += ",";
            }
            result += (*array)[i].Dump();
        }
        result += "]";
        return result;
    }
    const auto& object = std::get<Object>(value_);
    std::string result = "{";
    bool first = true;
    for (const auto& [key, value] : object) {
        if (!first) {
            result += ",";
        }
        first = false;
        result += "\"" + EscapeJson(key) + "\":" + value.Dump();
    }
    result += "}";
    return result;
}

Json Json::Parse(const std::string& text) {
    if (text.empty()) {
        return Json::Object{};
    }
    return JsonParser(text).Parse();
}

Json ErrorJson(const std::string& message) {
    return Json::Object{{"error", message}};
}

Json OkJson(const std::string& message) {
    return Json::Object{{"message", message}};
}

} // namespace gatherly
