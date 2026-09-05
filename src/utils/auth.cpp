#include "utils/auth.h"

#include <jwt-cpp/jwt.h>
#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <openssl/rand.h>

#include <array>
#include <charconv>
#include <climits>
#include <stdexcept>
#include <string_view>

namespace gatherly {

namespace {

constexpr int kPasswordIterations = 600000;
constexpr std::string_view kPasswordPrefix = "pbkdf2-sha256$600000$";
constexpr auto kTokenLifetime = std::chrono::hours(24);
using Salt = std::array<unsigned char, 16>;
using PasswordDigest = std::array<unsigned char, 32>;

template<size_t Size>
std::string Hex(const std::array<unsigned char, Size>& bytes) {
    constexpr char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(Size * 2);
    for (auto byte : bytes) {
        result.push_back(digits[byte >> 4]);
        result.push_back(digits[byte & 15]);
    }
    return result;
}

template<size_t Size>
std::optional<std::array<unsigned char, Size>> ParseHex(std::string_view text) {
    if (text.size() != Size * 2) {
        return std::nullopt;
    }
    std::array<unsigned char, Size> result{};
    for (size_t i = 0; i < Size; ++i) {
        unsigned int value = 0;
        const char* start = text.data() + i * 2;
        const auto [end, error] = std::from_chars(start, start + 2, value, 16);
        if (error != std::errc{} || end != start + 2) {
            return std::nullopt;
        }
        result[i] = static_cast<unsigned char>(value);
    }
    return result;
}

PasswordDigest DerivePassword(const std::string& password, const Salt& salt) {
    if (password.size() > INT_MAX) {
        throw std::invalid_argument("password is too long");
    }
    PasswordDigest result{};
    if (PKCS5_PBKDF2_HMAC(password.data(), static_cast<int>(password.size()),
                         salt.data(), salt.size(), kPasswordIterations,
                         EVP_sha256(), result.size(), result.data()) != 1) {
        throw std::runtime_error("password hashing failed");
    }
    return result;
}

template<size_t Size>
std::array<unsigned char, Size> RandomBytes() {
    std::array<unsigned char, Size> result{};
    if (RAND_bytes(result.data(), result.size()) != 1) {
        throw std::runtime_error("secure random generation failed");
    }
    return result;
}

struct VerificationClock {
    std::chrono::system_clock::time_point time;
    std::chrono::system_clock::time_point now() const { return time; }
};

} // namespace

std::string GenerateAuthSecret() {
    return Hex(RandomBytes<32>());
}

std::string HashPassword(const std::string& password) {
    const Salt salt = RandomBytes<16>();
    return std::string(kPasswordPrefix) + Hex(salt) + "$" + Hex(DerivePassword(password, salt));
}

bool VerifyPassword(const std::string& password, const std::string& password_hash) {
    // Existing local accounts are upgraded by Login after successful verification.
    if (password_hash.starts_with("plain:")) {
        const std::string_view stored = std::string_view(password_hash).substr(6);
        return stored.size() == password.size() &&
               CRYPTO_memcmp(stored.data(), password.data(), stored.size()) == 0;
    }
    if (!password_hash.starts_with(kPasswordPrefix)) {
        return false;
    }
    const std::string_view encoded = std::string_view(password_hash).substr(kPasswordPrefix.size());
    if (encoded.size() != 32 + 1 + 64 || encoded[32] != '$') {
        return false;
    }
    const auto salt = ParseHex<16>(encoded.substr(0, 32));
    const auto expected = ParseHex<32>(encoded.substr(33));
    if (!salt || !expected) {
        return false;
    }
    const auto actual = DerivePassword(password, *salt);
    return CRYPTO_memcmp(expected->data(), actual.data(), actual.size()) == 0;
}

TokenService::TokenService(std::string secret) : secret_(std::move(secret)) {
    if (secret_.size() < 32) {
        throw std::invalid_argument("GATHERLY_AUTH_SECRET must contain at least 32 bytes");
    }
}

std::string TokenService::IssueToken(int64_t user_id, std::chrono::system_clock::time_point now) const {
    if (user_id <= 0) {
        throw std::invalid_argument("token subject must be a positive user id");
    }
    return jwt::create()
        .set_type("JWT")
        .set_issuer("gatherly")
        .set_subject(std::to_string(user_id))
        .set_issued_at(now)
        .set_expires_at(now + kTokenLifetime)
        .sign(jwt::algorithm::hs256{secret_});
}

std::optional<int64_t> TokenService::VerifyToken(
    const std::string& token, std::chrono::system_clock::time_point now
) const {
    if (token.empty() || token.size() > 4096) {
        return std::nullopt;
    }
    try {
        const auto decoded = jwt::decode(token);
        jwt::verify<VerificationClock, jwt::traits::kazuho_picojson>(VerificationClock{now})
            .allow_algorithm(jwt::algorithm::hs256{secret_})
            .with_issuer("gatherly")
            .with_type("JWT")
            .verify(decoded);
        if (!decoded.has_expires_at() || !decoded.has_issued_at() ||
            now >= decoded.get_expires_at() || decoded.get_expires_at() <= decoded.get_issued_at()) {
            return std::nullopt;
        }
        const auto subject = decoded.get_subject();
        int64_t user_id = 0;
        const auto [end, error] = std::from_chars(subject.data(), subject.data() + subject.size(), user_id);
        if (error != std::errc{} || end != subject.data() + subject.size() || user_id <= 0) {
            return std::nullopt;
        }
        return user_id;
    } catch (const std::exception&) {
        return std::nullopt;
    }
}

} // namespace gatherly
