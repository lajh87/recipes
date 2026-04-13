import unittest

from app.ingredients import canonicalize_ingredient_name, ingredient_index_name


class IngredientCanonicalizationTests(unittest.TestCase):
    def test_collapses_safe_duplicate_aliases(self) -> None:
        self.assertEqual(canonicalize_ingredient_name("feta cheese"), "feta")
        self.assertEqual(canonicalize_ingredient_name("orzo pasta"), "orzo")
        self.assertEqual(canonicalize_ingredient_name("mozzarella cheese"), "mozzarella")

    def test_preserves_non_merge_cheeses(self) -> None:
        self.assertEqual(canonicalize_ingredient_name("cream cheese"), "cream cheese")
        self.assertEqual(canonicalize_ingredient_name("goat cheese"), "goat cheese")
        self.assertEqual(canonicalize_ingredient_name("blue cheese"), "blue cheese")

    def test_index_name_prefers_canonical_name(self) -> None:
        ingredient = {
            "raw": "200g feta cheese",
            "normalized_name": "feta cheese",
            "canonical_name": "feta",
        }
        self.assertEqual(ingredient_index_name(ingredient), "feta")


if __name__ == "__main__":
    unittest.main()
