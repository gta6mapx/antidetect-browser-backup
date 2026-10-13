import unittest

from fingerprint_toolkit import FingerprintKit


class FingerprintKitTests(unittest.TestCase):
    def test_generate_uses_available_font_pool(self):
        profile = FingerprintKit(seed=1234).get_profile()

        self.assertGreaterEqual(len(profile.fonts), 6)
        self.assertLessEqual(len(profile.fonts), len(FingerprintKit.COMMON_FONTS))
        self.assertEqual(len(profile.fonts), len(set(profile.fonts)))


if __name__ == "__main__":
    unittest.main()
