import time
import unittest

import jwt

from gatherly.auth import hash_password, issue_token, verify_password, verify_token


class AuthCompatibility(unittest.TestCase):
    secret = '0123456789abcdef' * 4

    def test_existing_password_fixture(self):
        digest = 'pbkdf2-sha256$600000$00112233445566778899aabbccddeeff$7c0123695eb46911838d4c16fa259d7280c59060c6031130b8269b624faacd02'
        self.assertTrue(verify_password('correct horse battery staple', digest))
        self.assertFalse(verify_password('wrong password', digest))
        self.assertTrue(verify_password('password', 'plain:password'))
        self.assertFalse(verify_password('wrong', 'plain:password'))

    def test_salted_passwords_and_malformed_hashes(self):
        first, second = hash_password('password'), hash_password('password')
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password('password', first))
        for value in ('', 'unknown', 'pbkdf2-sha256$999999999$invalid', first[:-1] + 'z'):
            self.assertFalse(verify_password('password', value))

    def test_compatible_jwt_and_invalid_claims(self):
        self.assertEqual(verify_token(self.secret, issue_token(self.secret, 42)), 42)
        self.assertIsNone(verify_token('another secret' * 4, issue_token(self.secret, 42)))
        now = int(time.time())
        base = {'iss': 'gatherly', 'sub': '42', 'iat': now, 'exp': now + 86400}
        for changed in ({'sub': '0'}, {'sub': '42extra'}, {'sub': str(2**64)}, {'iat': now + 1}, {'exp': now}, {'iss': 'other'}):
            token = jwt.encode(base | changed, self.secret, algorithm='HS256')
            self.assertIsNone(verify_token(self.secret, token), changed)
        for algorithm in ('HS512', 'HS384'):
            self.assertIsNone(verify_token(self.secret, jwt.encode(base, self.secret, algorithm=algorithm)))
        del base['exp']
        self.assertIsNone(verify_token(self.secret, jwt.encode(base, self.secret, algorithm='HS256')))


if __name__ == '__main__':
    unittest.main()
