import unittest
from pathlib import Path

from app.config import Settings
from app.main import BRAND_IMAGE_MAP


class BrandingTests(unittest.TestCase):
    def test_default_app_name_matches_family_branding(self) -> None:
        settings = Settings()

        self.assertEqual(settings.app_name, "Heley Family Cookbook")

    def test_brand_image_map_points_to_existing_files(self) -> None:
        self.assertEqual(set(BRAND_IMAGE_MAP), {"cottage", "george-chef"})
        for path in BRAND_IMAGE_MAP.values():
            self.assertTrue(path.exists(), f"Expected brand asset to exist: {path}")

    def test_dockerfile_copies_brand_asset_directory(self) -> None:
        dockerfile = Path(__file__).resolve().parent.parent / "Dockerfile"

        self.assertIn("COPY data-raw ./data-raw", dockerfile.read_text())


if __name__ == "__main__":
    unittest.main()
