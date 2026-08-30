import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import httpx
from openai import RateLimitError

from app.config import Settings
from app.extractor import OpenAIRecipeExtractor, RecipeDraft, RecipeExtractionPayload
from app.repository import LibraryRepository
from app.url_recipe_import import (
    FetchedRecipePage,
    UrlRecipeImporter,
    UrlRecipeImportError,
    collection_slug_for_hostname,
    fetch_recipe_page,
    normalize_recipe_url,
    prepare_recipe_page,
    validate_public_url,
)


def public_resolver(host: str, port: int, **_kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def recipe_payload() -> RecipeExtractionPayload:
    return RecipeExtractionPayload.model_validate(
        {
            "is_recipe": True,
            "title": "Lemon Pasta",
            "confidence": 0.96,
            "ingredients": [
                {
                    "raw": "200g spaghetti",
                    "normalized_name": "spaghetti",
                    "quantity": "200",
                    "unit": "g",
                    "item": "spaghetti",
                },
                {
                    "raw": "1 lemon",
                    "normalized_name": "lemon",
                    "quantity": "1",
                    "item": "lemon",
                },
            ],
            "method_steps": ["Cook the pasta.", "Add the lemon."],
            "serves": "2",
            "prep_time": "10 minutes",
            "notes": ["complete"],
        }
    )


class FakeExtractor:
    def __init__(self, payload: RecipeExtractionPayload | None = None) -> None:
        self.payload = payload or recipe_payload()
        self.extract_calls = 0
        self.web_search_calls = 0
        self.embedding_calls = 0

    def extract_url_payload(self, **_kwargs) -> RecipeExtractionPayload:
        self.extract_calls += 1
        return self.payload

    def extract_url_with_web_search(self, *, source_url: str, hostname: str):
        self.web_search_calls += 1
        return self.payload, [source_url]

    def build_embeddings(self, texts: list[str]) -> list[list[float]]:
        self.embedding_calls += 1
        return [[0.1, 0.2] for _text in texts]


class FakeRepository:
    def __init__(self) -> None:
        self.existing = None
        self.preview_payloads: dict[str, str] = {}
        self.preview_ttl = 0
        self.saved_draft = None
        self.deleted_preview = ""

    def get_recipe_for_source_url(self, _url: str):
        return self.existing

    def get_cookbook(self, _cookbook_id: str):
        return SimpleNamespace(collection_slug="other")

    def save_recipe_import_preview(self, import_id: str, payload: str, *, ttl_seconds: int) -> None:
        self.preview_payloads[import_id] = payload
        self.preview_ttl = ttl_seconds

    def load_recipe_import_preview(self, import_id: str):
        return self.preview_payloads.get(import_id)

    def delete_recipe_import_preview(self, import_id: str) -> None:
        self.deleted_preview = import_id
        self.preview_payloads.pop(import_id, None)

    def store_url_recipe(self, **kwargs):
        self.saved_draft = kwargs["draft"]
        return SimpleNamespace(
            id="recipe-1",
            cookbook_id="web-source",
            cookbook_title=kwargs["site_name"],
            title=kwargs["draft"].title,
        )


class MemoryRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}
        self.strings: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    def hset(self, key: str, mapping: dict[str, object]) -> None:
        current = self.hashes.setdefault(key, {})
        current.update({name: str(value) for name, value in mapping.items()})

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.sorted_sets.setdefault(key, {}).update(mapping)

    def zrange(self, key: str, start: int, end: int) -> list[str]:
        values = [item[0] for item in sorted(self.sorted_sets.get(key, {}).items(), key=lambda item: item[1])]
        return values[start:] if end == -1 else values[start : end + 1]

    def zrevrange(self, key: str, start: int, end: int) -> list[str]:
        values = [
            item[0]
            for item in sorted(self.sorted_sets.get(key, {}).items(), key=lambda item: item[1], reverse=True)
        ]
        return values[start:] if end == -1 else values[start : end + 1]

    def zscore(self, key: str, member: str):
        return self.sorted_sets.get(key, {}).get(member)

    def zmscore(self, key: str, members: list[str]) -> list[float | None]:
        return [self.zscore(key, member) for member in members]

    def set(self, key: str, value: object, *, nx: bool = False, **_kwargs):
        if nx and key in self.strings:
            return False
        self.strings[key] = str(value)
        return True

    def get(self, key: str):
        return self.strings.get(key)

    def delete(self, *keys: str) -> None:
        for key in keys:
            self.strings.pop(key, None)
            self.hashes.pop(key, None)
            self.sorted_sets.pop(key, None)
            self.sets.pop(key, None)

    def mget(self, keys: list[str]) -> list[str | None]:
        return [self.get(key) for key in keys]

    def sadd(self, key: str, member: str) -> None:
        self.sets.setdefault(key, set()).add(member)


class UrlRecipeImportTests(unittest.TestCase):
    def test_normalize_url_removes_fragment_default_port_and_tracking(self) -> None:
        normalized = normalize_recipe_url(
            "HTTPS://Example.com:443/recipe?utm_source=newsletter&id=4&fbclid=x#method"
        )

        self.assertEqual(normalized, "https://example.com/recipe?id=4")

    def test_collection_mapping_accepts_subdomains_and_falls_back_to_other(self) -> None:
        self.assertEqual(collection_slug_for_hostname("www.jamieoliver.com"), "jamie-oliver")
        self.assertEqual(collection_slug_for_hostname("cooking.nytimes.com"), "nytimes")
        self.assertEqual(collection_slug_for_hostname("recipes.waitrose.com"), "waitrose-recipes")
        self.assertEqual(collection_slug_for_hostname("example.com"), "other")

    def test_private_addresses_are_rejected(self) -> None:
        def private_resolver(host: str, port: int, **_kwargs):
            return [(2, 1, 6, "", ("127.0.0.1", port))]

        with self.assertRaises(UrlRecipeImportError) as context:
            validate_public_url("https://example.com/recipe", resolver=private_resolver)

        self.assertEqual(context.exception.code, "unsafe_url")

    def test_prepare_page_prefers_same_host_canonical_and_includes_recipe_json_ld(self) -> None:
        html = """
        <html><head>
          <title>Lemon Pasta</title>
          <meta property="og:site_name" content="Example Kitchen">
          <link rel="canonical" href="/lemon-pasta?utm_source=test#method">
          <script type="application/ld+json">
            {"@type":"Recipe","name":"Lemon Pasta","recipeIngredient":["1 lemon"]}
          </script>
        </head><body><article><h1>Lemon Pasta</h1><p>Cook the pasta.</p></article></body></html>
        """

        page = prepare_recipe_page(
            html,
            submitted_url="https://example.com/lemon-pasta?utm_source=test",
            final_url="https://example.com/lemon-pasta?utm_source=test",
        )

        self.assertEqual(page.canonical_url, "https://example.com/lemon-pasta")
        self.assertEqual(page.site_name, "Example Kitchen")
        self.assertIn("recipeIngredient", page.model_text)
        self.assertIn("Cook the pasta", page.model_text)

    def test_fetch_revalidates_redirect_and_accepts_html(self) -> None:
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if request.url.host == "example.com":
                return httpx.Response(302, headers={"location": "https://www.jamieoliver.com/recipes/pasta/"})
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<html><body><article><h1>Pasta</h1><p>Boil water and cook pasta.</p></article></body></html>",
            )

        page = fetch_recipe_page(
            "https://example.com/recipe",
            resolver=public_resolver,
            transport=httpx.MockTransport(handler),
        )

        self.assertEqual(len(requests), 2)
        self.assertEqual(page.hostname, "www.jamieoliver.com")

    def test_fetch_rejects_redirect_to_private_address(self) -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(302, headers={"location": "http://127.0.0.1/recipe"})
        )

        with self.assertRaises(UrlRecipeImportError) as context:
            fetch_recipe_page(
                "https://example.com/recipe",
                resolver=lambda host, port, **kwargs: public_resolver(host, port, **kwargs)
                if host == "example.com"
                else [(2, 1, 6, "", ("127.0.0.1", port))],
                transport=transport,
            )

        self.assertEqual(context.exception.code, "unsafe_url")

    def test_non_html_response_is_rejected(self) -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"pdf")
        )

        with self.assertRaises(UrlRecipeImportError) as context:
            fetch_recipe_page(
                "https://example.com/recipe.pdf",
                resolver=public_resolver,
                transport=transport,
            )

        self.assertEqual(context.exception.status_code, 415)

    def test_oversized_page_is_rejected(self) -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"x" * ((2 * 1024 * 1024) + 1),
            )
        )

        with self.assertRaises(UrlRecipeImportError) as context:
            fetch_recipe_page(
                "https://example.com/recipe",
                resolver=public_resolver,
                transport=transport,
            )

        self.assertEqual(context.exception.status_code, 413)

    def test_preview_saves_temporary_draft_and_metadata(self) -> None:
        repository = FakeRepository()
        extractor = FakeExtractor()
        page = FetchedRecipePage(
            submitted_url="https://example.com/recipe",
            final_url="https://example.com/recipe",
            canonical_url="https://example.com/recipe",
            hostname="example.com",
            site_name="Example Kitchen",
            page_title="Lemon Pasta",
            model_text="Recipe source",
            excerpt="Lemon pasta source excerpt",
        )
        importer = UrlRecipeImporter(
            Settings(openai_api_key="test-key"),
            fetcher=lambda _url: page,
            extractor_factory=lambda _settings: extractor,
        )

        result = importer.preview(repository, page.submitted_url)

        self.assertEqual(result["status"], "preview")
        self.assertEqual(result["collection_slug"], "other")
        self.assertEqual(result["ingredient_lines"], ["200g spaghetti", "1 lemon"])
        self.assertEqual(result["metadata"]["serves"], "2")
        self.assertGreater(repository.preview_ttl, 0)
        self.assertIn(result["import_id"], repository.preview_payloads)

    def test_unreachable_page_falls_back_to_domain_limited_web_search(self) -> None:
        repository = FakeRepository()
        extractor = FakeExtractor()

        def unreachable(_url: str):
            raise UrlRecipeImportError(
                "The recipe website could not be reached.",
                status_code=422,
                code="source_unreachable",
            )

        importer = UrlRecipeImporter(
            Settings(openai_api_key="test-key"),
            fetcher=unreachable,
            extractor_factory=lambda _settings: extractor,
        )

        result = importer.preview(repository, "https://www.waitrose.com/ecom/recipe/lemon-pasta")
        stored = next(iter(repository.preview_payloads.values()))

        self.assertEqual(result["status"], "preview")
        self.assertEqual(result["collection_slug"], "waitrose-recipes")
        self.assertEqual(result["metadata"]["retrieval_method"], "openai_web_search")
        self.assertEqual(
            result["metadata"]["web_search_sources"],
            ["https://www.waitrose.com/ecom/recipe/lemon-pasta"],
        )
        self.assertIn('"retrieval_method":"openai_web_search"', stored)
        self.assertEqual(extractor.web_search_calls, 1)

    def test_exhausted_openai_credits_return_a_clear_error(self) -> None:
        repository = FakeRepository()
        page = FetchedRecipePage(
            submitted_url="https://example.com/recipe",
            final_url="https://example.com/recipe",
            canonical_url="https://example.com/recipe",
            hostname="example.com",
            site_name="Example Kitchen",
            page_title="Lemon Pasta",
            model_text="Recipe source",
            excerpt="Lemon pasta source excerpt",
        )

        class QuotaExtractor(FakeExtractor):
            def extract_url_payload(self, **_kwargs):
                request = httpx.Request("POST", "https://api.openai.com/v1/responses")
                response = httpx.Response(429, request=request)
                raise RateLimitError(
                    "You have no credits remaining.",
                    response=response,
                    body={"code": "credit_balance_exhausted"},
                )

        importer = UrlRecipeImporter(
            Settings(openai_api_key="test-key"),
            fetcher=lambda _url: page,
            extractor_factory=lambda _settings: QuotaExtractor(),
        )

        with self.assertRaises(UrlRecipeImportError) as context:
            importer.preview(repository, page.submitted_url)

        self.assertEqual(context.exception.code, "openai_quota_exhausted")
        self.assertIn("credits", str(context.exception).casefold())

    def test_duplicate_preview_skips_fetch_and_openai(self) -> None:
        repository = FakeRepository()
        repository.existing = SimpleNamespace(
            id="recipe-existing",
            cookbook_id="book-1",
            cookbook_title="Example Kitchen",
            title="Existing Recipe",
        )
        fetcher = Mock()
        importer = UrlRecipeImporter(
            Settings(openai_api_key=""),
            fetcher=fetcher,
        )

        result = importer.preview(repository, "https://example.com/recipe")

        self.assertEqual(result["status"], "existing")
        fetcher.assert_not_called()

    def test_commit_uses_edited_core_fields_and_deletes_preview(self) -> None:
        repository = FakeRepository()
        extractor = FakeExtractor()
        page = FetchedRecipePage(
            submitted_url="https://example.com/recipe",
            final_url="https://example.com/recipe",
            canonical_url="https://example.com/recipe",
            hostname="example.com",
            site_name="Example Kitchen",
            page_title="Lemon Pasta",
            model_text="Recipe source",
            excerpt="Lemon pasta source excerpt",
        )
        importer = UrlRecipeImporter(
            Settings(openai_api_key="test-key"),
            fetcher=lambda _url: page,
            extractor_factory=lambda _settings: extractor,
        )
        preview = importer.preview(repository, page.submitted_url)

        result = importer.commit(
            repository,
            preview["import_id"],
            title="Creamy Lemon Pasta",
            ingredient_lines=["250g spaghetti", "1 lemon"],
            method_steps=["Cook the spaghetti.", "Stir through the lemon."],
        )

        self.assertEqual(result["status"], "created")
        self.assertEqual(repository.saved_draft.title, "Creamy Lemon Pasta")
        self.assertEqual(repository.saved_draft.ingredients[0]["canonical_name"], "spaghetti")
        self.assertEqual(repository.saved_draft.source["metadata"]["canonical_url"], page.canonical_url)
        self.assertEqual(repository.saved_draft.review_status, "verified")
        self.assertEqual(repository.deleted_preview, preview["import_id"])

    def test_expired_preview_returns_gone_error(self) -> None:
        importer = UrlRecipeImporter(Settings(openai_api_key="test-key"))

        with self.assertRaises(UrlRecipeImportError) as context:
            importer.commit(
                FakeRepository(),
                "missing",
                title="Recipe",
                ingredient_lines=["1 lemon"],
                method_steps=["Cook it."],
            )

        self.assertEqual(context.exception.status_code, 410)

    def test_web_source_is_marked_and_hidden_from_books_shelf(self) -> None:
        repository = object.__new__(LibraryRepository)
        repository.settings = Settings()
        repository.redis = MemoryRedis()

        source = repository.ensure_web_source(
            hostname="example.com",
            site_name="Example Kitchen",
            collection_slug="other",
        )

        self.assertEqual(source.source_kind, "web")
        self.assertEqual(source.collection_slug, "other")
        self.assertEqual(source.object_key, "")
        self.assertEqual(repository._filter_cookbooks_for_library([source], False), [])

    def test_store_url_recipe_indexes_once_and_places_recipe_in_other_collection(self) -> None:
        repository = object.__new__(LibraryRepository)
        repository.settings = Settings()
        repository.redis = MemoryRedis()
        repository.qdrant = SimpleNamespace(upsert=Mock())
        repository.minio = SimpleNamespace()
        source_url = "https://example.com/lemon-pasta"
        draft = RecipeDraft(
            title="Lemon Pasta",
            ingredients=[
                {
                    "raw": "1 lemon",
                    "normalized_name": "lemon",
                    "canonical_name": "lemon",
                }
            ],
            method_steps=["Cook it."],
            source={
                "object_key": "",
                "format": "url",
                "anchor": source_url,
                "excerpt": "Lemon pasta",
                "metadata": {"canonical_url": source_url, "source_url": source_url},
            },
            images=[],
            confidence=0.95,
            notes=["url_import"],
            review_status="verified",
            review_reasons=[],
        )

        first = repository.store_url_recipe(
            hostname="example.com",
            site_name="Example Kitchen",
            collection_slug="other",
            normalized_url=source_url,
            draft=draft,
            embedding=None,
        )
        second = repository.store_url_recipe(
            hostname="example.com",
            site_name="Example Kitchen",
            collection_slug="other",
            normalized_url=source_url,
            draft=draft,
            embedding=None,
        )

        source_key = repository.settings.recipe_source_url_key(source_url)
        lock_key = repository.settings.recipe_source_url_lock_key(source_url)
        repository.redis.delete(source_key)
        repository.redis.set(lock_key, "concurrent-import", ex=60)

        def finish_concurrent_import() -> None:
            time.sleep(0.075)
            repository.redis.set(source_key, first.id)
            repository.redis.delete(lock_key)

        concurrent_import = threading.Thread(target=finish_concurrent_import)
        concurrent_import.start()
        concurrent_result = repository.store_url_recipe(
            hostname="example.com",
            site_name="Example Kitchen",
            collection_slug="other",
            normalized_url=source_url,
            draft=draft,
            embedding=None,
        )
        concurrent_import.join()

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.id, concurrent_result.id)
        self.assertEqual(repository.get_recipe_for_source_url(source_url).id, first.id)
        self.assertEqual([recipe.id for recipe in repository.list_recipes_for_collection("other")], [first.id])


class OpenAIUrlExtractionTests(unittest.TestCase):
    def test_url_extraction_uses_strict_responses_json_schema(self) -> None:
        extractor = OpenAIRecipeExtractor(Settings(openai_api_key="test-key"))
        response_payload = recipe_payload().model_dump()
        response_payload.update(
            {
                "intro": "",
                "makes": "",
                "yield_value": "",
                "cook_time": "",
                "total_time": "",
                "preparation_notes": [],
                "supplemental_sections": [],
            }
        )
        create = Mock(return_value=SimpleNamespace(output_text=json.dumps(response_payload)))
        extractor.client = SimpleNamespace(responses=SimpleNamespace(create=create))

        result = extractor.extract_url_payload(
            source_name="Example Kitchen",
            source_url="https://example.com/recipe",
            page_text="Lemon Pasta\nIngredients\n1 lemon\nMethod\nCook it.",
            excerpt="Lemon Pasta",
        )

        self.assertEqual(result.title, "Lemon Pasta")
        request = create.call_args.kwargs
        self.assertEqual(request["text"]["format"]["type"], "json_schema")
        self.assertTrue(request["text"]["format"]["strict"])

    def test_web_search_fallback_requires_exact_domain_source_and_strict_schema(self) -> None:
        extractor = OpenAIRecipeExtractor(Settings(openai_api_key="test-key"))
        source_url = "https://www.waitrose.com/ecom/recipe/lemon-pasta"
        response_payload = recipe_payload().model_dump()
        response_payload.update(
            {
                "intro": "",
                "makes": "",
                "yield_value": "",
                "cook_time": "",
                "total_time": "",
                "preparation_notes": [],
                "supplemental_sections": [],
            }
        )
        response = SimpleNamespace(
            output_text=json.dumps(response_payload),
            model_dump=lambda: {
                "output": [
                    {
                        "type": "web_search_call",
                        "action": {"type": "open_page", "url": source_url},
                    }
                ]
            },
        )
        create = Mock(return_value=response)
        extractor.client = SimpleNamespace(responses=SimpleNamespace(create=create))

        result, sources = extractor.extract_url_with_web_search(
            source_url=source_url,
            hostname="www.waitrose.com",
        )

        self.assertEqual(result.title, "Lemon Pasta")
        self.assertEqual(sources, [source_url])
        request = create.call_args.kwargs
        self.assertEqual(request["tools"][0]["type"], "web_search")
        self.assertEqual(request["tools"][0]["filters"]["allowed_domains"], ["www.waitrose.com"])
        self.assertEqual(request["tool_choice"], "required")
        self.assertEqual(request["text"]["format"]["type"], "json_schema")
        self.assertTrue(request["text"]["format"]["strict"])


if __name__ == "__main__":
    unittest.main()
