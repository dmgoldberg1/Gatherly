#pragma once

#include <cstdint>
#include <optional>
#include <string>

namespace gatherly {

class TokenService {
public:
    explicit TokenService(std::string secret);

    std::string IssueToken(int64_t user_id) const;
    std::optional<int64_t> VerifyToken(const std::string& token) const;

protected:
    std::string Sign(const std::string& header, const std::string& payload) const;
    std::string secret_;
};

} // namespace gatherly
