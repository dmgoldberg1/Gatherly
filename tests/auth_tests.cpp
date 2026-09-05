#include "test_support.h"

#include <jwt-cpp/jwt.h>

using namespace gatherly;
using namespace gatherly::test;
using namespace std::chrono_literals;

TEST(PasswordTest, VerifiesDigestProducedByPythonHashlib) {
    // hashlib.pbkdf2_hmac('sha256', password, bytes.fromhex(salt), 600000).
    const std::string hash = "pbkdf2-sha256$600000$00112233445566778899aabbccddeeff$"
                             "7c0123695eb46911838d4c16fa259d7280c59060c6031130b8269b624faacd02";
    EXPECT_TRUE(VerifyPassword("correct horse battery staple", hash));
}

TEST(PasswordTest, SaltedHashesVerifyOnlyTheCorrectPassword) {
    const auto first = HashPassword("correct password");
    const auto second = HashPassword("correct password");
    EXPECT_NE(first, second);
    EXPECT_TRUE(first.starts_with("pbkdf2-sha256$"));
    EXPECT_TRUE(VerifyPassword("correct password", first));
    EXPECT_TRUE(VerifyPassword("correct password", second));
    EXPECT_FALSE(VerifyPassword("wrong password", first));
    EXPECT_FALSE(VerifyPassword("", first));
}

TEST(PasswordTest, MalformedHashesAreRejected) {
    for (const auto* hash : {"", "unknown", "pbkdf2-sha256$600000$short",
                             "pbkdf2-sha256$999999999$invalid"}) {
        SCOPED_TRACE(hash);
        EXPECT_FALSE(VerifyPassword("password", hash));
    }
    auto hash = HashPassword("password");
    hash.back() = 'z';
    EXPECT_FALSE(VerifyPassword("password", hash));
}

TEST(AuthTest, RegistrationStoresHashedPassword) {
    auto store = std::make_shared<FakePostgresStore>();
    GatherlyService service(store);
    const User alice = service.RegisterUser(UserCommand("alice"));
    const auto stored = store->GetUser(alice.id);
    ASSERT_TRUE(stored.has_value());
    EXPECT_TRUE(stored->password_hash.starts_with("pbkdf2-sha256$"));
    EXPECT_TRUE(VerifyPassword("password", stored->password_hash));
}

TEST(AuthTest, SuccessfulLoginUpgradesLegacyPasswordWithoutChangingProfile) {
    auto store = std::make_shared<FakePostgresStore>();
    User legacy;
    legacy.id = 1;
    legacy.username = "alice";
    legacy.email = "alice@gatherly.local";
    legacy.bio = "Existing profile";
    legacy.password_hash = "plain:password";
    legacy.created_at = "2026-07-04T18:00:00Z";
    store->UpsertUser(legacy);
    GatherlyService service(store);

    ExpectThrowsStatus(401, [&] { service.Login("alice", "wrong password"); });
    EXPECT_EQ(store->GetUser(legacy.id)->password_hash, "plain:password");
    const auto login = service.Login("alice", "password");
    EXPECT_EQ(service.Authenticate(login.token), legacy.id);
    const auto upgraded = store->GetUser(legacy.id);
    ASSERT_TRUE(upgraded.has_value());
    EXPECT_TRUE(upgraded->password_hash.starts_with("pbkdf2-sha256$"));
    EXPECT_EQ(upgraded->username, legacy.username);
    EXPECT_EQ(upgraded->email, legacy.email);
    EXPECT_EQ(upgraded->bio, legacy.bio);
    EXPECT_EQ(upgraded->created_at, legacy.created_at);
    EXPECT_EQ(service.Login("alice", "password").user.id, legacy.id);
}

TEST(TokenTest, TokensExpireAfterOneDayAndRejectFutureIssueTime) {
    TokenService service(GenerateAuthSecret());
    const auto issued_at = std::chrono::system_clock::from_time_t(1760000000);
    const auto token = service.IssueToken(42, issued_at);
    EXPECT_EQ(service.VerifyToken(token, issued_at), 42);
    EXPECT_EQ(service.VerifyToken(token, issued_at + 24h - 1s), 42);
    EXPECT_FALSE(service.VerifyToken(token, issued_at + 24h).has_value());
    EXPECT_FALSE(service.VerifyToken(token, issued_at - 1s).has_value());
}

TEST(TokenTest, SignatureMustMatchConfiguredSecret) {
    const auto secret = GenerateAuthSecret();
    TokenService issuer(secret);
    const auto token = issuer.IssueToken(42);
    EXPECT_EQ(TokenService(secret).VerifyToken(token), 42);
    EXPECT_FALSE(TokenService(GenerateAuthSecret()).VerifyToken(token).has_value());
    EXPECT_FALSE(issuer.VerifyToken(token + "invalid").has_value());
    EXPECT_THROW(TokenService("short secret"), std::invalid_argument);
    EXPECT_THROW(issuer.IssueToken(0), std::invalid_argument);
}

TEST(TokenTest, RejectsMissingExpiryWrongIssuerAndDisallowedAlgorithm) {
    const auto secret = GenerateAuthSecret();
    TokenService service(secret);
    const auto now = std::chrono::system_clock::now();
    auto builder = jwt::create().set_type("JWT").set_issuer("gatherly")
        .set_subject("42").set_issued_at(now);
    EXPECT_FALSE(service.VerifyToken(builder.sign(jwt::algorithm::hs256{secret})).has_value());

    builder.set_expires_at(now + 24h);
    EXPECT_FALSE(service.VerifyToken(builder.sign(jwt::algorithm::hs512{secret})).has_value());
    EXPECT_FALSE(service.VerifyToken(builder.sign(jwt::algorithm::none{})).has_value());
    builder.set_issuer("another-service");
    EXPECT_FALSE(service.VerifyToken(builder.sign(jwt::algorithm::hs256{secret})).has_value());
}

TEST(TokenTest, SubjectMustBeAnEntirePositiveInteger) {
    const auto secret = GenerateAuthSecret();
    TokenService service(secret);
    const auto now = std::chrono::system_clock::now();
    for (const auto* subject : {"", "0", "-1", "42extra", "999999999999999999999999"}) {
        SCOPED_TRACE(subject);
        const auto token = jwt::create().set_type("JWT").set_issuer("gatherly")
            .set_subject(subject).set_issued_at(now).set_expires_at(now + 24h)
            .sign(jwt::algorithm::hs256{secret});
        EXPECT_FALSE(service.VerifyToken(token).has_value());
    }
}
