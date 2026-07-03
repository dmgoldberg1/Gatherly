#pragma once

#include <map>
#include <optional>
#include <string>
#include <variant>
#include <vector>

namespace gatherly {

class Json {
public:
    using Array = std::vector<Json>;
    using Object = std::map<std::string, Json>;
    using Value = std::variant<std::nullptr_t, bool, double, std::string, Array, Object>;

    Json();
    Json(std::nullptr_t);
    Json(bool value);
    Json(int64_t value);
    Json(double value);
    Json(const char* value);
    Json(std::string value);
    Json(Array value);
    Json(Object value);

    bool IsNull() const;
    bool IsObject() const;
    bool IsArray() const;
    const Object& AsObject() const;
    const Array& AsArray() const;
    std::string AsString(const std::string& fallback = "") const;
    int64_t AsInt(int64_t fallback = 0) const;
    bool AsBool(bool fallback = false) const;

    const Json& At(const std::string& key) const;
    bool Contains(const std::string& key) const;
    std::string Dump() const;

    static Json Parse(const std::string& text);

protected:
    Value value_;
};

Json ErrorJson(const std::string& message);
Json OkJson(const std::string& message);

} // namespace gatherly
