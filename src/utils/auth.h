#pragma once

#include <chrono>
#include <cstdint>
#include <optional>
#include <string>

namespace gatherly {

std::string GenerateAuthSecret();
std::string HashPassword(const std::string& password);
bool VerifyPassword(const std::string& password, const std::string& password_hash);

class TokenService {
public:
    explicit TokenService(std::string secret);

    std::string IssueToken(
        int64_t user_id,
        std::chrono::system_clock::time_point now = std::chrono::system_clock::now()
    ) const;
    std::optional<int64_t> VerifyToken(
        const std::string& token,
        std::chrono::system_clock::time_point now = std::chrono::system_clock::now()
    ) const;

private:
    std::string secret_;
};

} // namespace gatherly
