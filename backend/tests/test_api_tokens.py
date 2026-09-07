import unittest

from app.services import api_tokens


class ApiTokenTests(unittest.TestCase):
    def test_generate_shape(self) -> None:
        token = api_tokens.generate_token()
        self.assertTrue(token.startswith(api_tokens.TOKEN_PREFIX))
        self.assertGreater(len(token), 40)
        self.assertTrue(api_tokens.looks_like_token(token))

    def test_hash_is_stable_and_hidden(self) -> None:
        token = api_tokens.generate_token()
        h1, h2 = api_tokens.hash_token(token), api_tokens.hash_token(token)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)
        self.assertNotIn(token[4:], h1)

    def test_prefix_is_short(self) -> None:
        token = api_tokens.generate_token()
        self.assertEqual(len(api_tokens.token_prefix(token)), api_tokens.VISIBLE_CHARS)

    def test_rejects_foreign_formats(self) -> None:
        for bad in ("", "abc", "Bearer x", "rga_", "rga_short", "eyJhbGciOi"):
            self.assertFalse(api_tokens.looks_like_token(bad))


if __name__ == "__main__":
    unittest.main()
