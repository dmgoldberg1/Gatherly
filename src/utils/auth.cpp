#include "utils/auth.h"

#include <chrono>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace gatherly {

namespace {

const char kAlphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

std::string Base64UrlEncode(const std::string& input) {
    std::string output;
    int value = 0;
    int bits = -6;
    for (uint8_t ch : input) {
        value = (value << 8) + ch;
        bits += 8;
        while (bits >= 0) {
            output.push_back(kAlphabet[(value >> bits) & 0x3F]);
            bits -= 6;
        }
    }
    if (bits > -6) {
        output.push_back(kAlphabet[((value << 8) >> (bits + 8)) & 0x3F]);
    }
    return output;
}

int DecodeChar(char ch) {
    if (ch >= 'A' && ch <= 'Z') {
        return ch - 'A';
    }
    if (ch >= 'a' && ch <= 'z') {
        return ch - 'a' + 26;
    }
    if (ch >= '0' && ch <= '9') {
        return ch - '0' + 52;
    }
    if (ch == '-') {
        return 62;
    }
    if (ch == '_') {
        return 63;
    }
    return -1;
}

std::string Base64UrlDecode(const std::string& input) {
    std::string output;
    int value = 0;
    int bits = -8;
    for (char ch : input) {
        const int decoded = DecodeChar(ch);
        if (decoded < 0) {
            throw std::runtime_error("invalid base64url token part");
        }
        value = (value << 6) + decoded;
        bits += 6;
        if (bits >= 0) {
            output.push_back(static_cast<char>((value >> bits) & 0xFF));
            bits -= 8;
        }
    }
    return output;
}

int64_t UnixTime() {
    return std::chrono::duration_cast<std::chrono::seconds>(
        std::chrono::system_clock::now().time_since_epoch()
    ).count();
}

std::optional<int64_t> ExtractSubject(const std::string& payload) {
    const std::string key = "\"sub\":";
    const size_t pos = payload.find(key);
    if (pos == std::string::npos) {
        return std::nullopt;
    }
    size_t start = pos + key.size();
    size_t end = start;
    while (end < payload.size() && payload[end] >= '0' && payload[end] <= '9') {
        ++end;
    }
    if (start == end) {
        return std::nullopt;
    }
    return std::stoll(payload.substr(start, end - start));
}

} // namespace

TokenService::TokenService(std::string secret)
    : secret_(std::move(secret)) {}

std::string TokenService::IssueToken(int64_t user_id) const {
    const std::string header = Base64UrlEncode(R"({"alg":"DEV-HASH","typ":"JWT"})");
    const std::string payload = Base64UrlEncode(
        std::string(R"({"sub":)") + std::to_string(user_id) + R"(,"iat":)" + std::to_string(UnixTime()) + "}"
    );
    return header + "." + payload + "." + Sign(header, payload);
}

std::optional<int64_t> TokenService::VerifyToken(const std::string& token) const {
    const size_t first = token.find('.');
    if (first == std::string::npos) {
        return std::nullopt;
    }
    const size_t second = token.find('.', first + 1);
    if (second == std::string::npos) {
        return std::nullopt;
    }
    const std::string header = token.substr(0, first);
    const std::string payload = token.substr(first + 1, second - first - 1);
    const std::string signature = token.substr(second + 1);
    if (signature != Sign(header, payload)) {
        return std::nullopt;
    }
    try {
        return ExtractSubject(Base64UrlDecode(payload));
    } catch (...) {
        return std::nullopt;
    }
}

std::string TokenService::Sign(const std::string& header, const std::string& payload) const {
    const std::string data = header + "." + payload + "." + secret_;
    const size_t hash = std::hash<std::string>{}(data);
    std::ostringstream output;
    output << std::hex << hash;
    return Base64UrlEncode(output.str());
}

} // namespace gatherly
