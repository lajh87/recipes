from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import ipaddress
import json
import logging
import socket
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from uuid import uuid4

from bs4 import BeautifulSoup
import httpx
from openai import AuthenticationError, RateLimitError
from pydantic import BaseModel, Field

from app.config import Settings
from app.extractor import (
    OpenAIRecipeExtractor,
    RecipeDraft,
    RecipeExtractionPayload,
    parse_ingredient_line,
)
from app.ingredients import prepare_ingredient_mapping

logger = logging.getLogger(__name__)

URL_IMPORT_PREVIEW_TTL_SECONDS = 30 * 60
URL_FETCH_TIMEOUT_SECONDS = 12
URL_FETCH_MAX_BYTES = 2 * 1024 * 1024
URL_FETCH_MAX_REDIRECTS = 5
URL_MODEL_TEXT_LIMIT = 50_000
URL_EXCERPT_LIMIT = 2_000
TRACKING_QUERY_KEYS = frozenset({"fbclid", "gclid", "igshid", "mc_cid", "mc_eid"})
PUBLISHER_COLLECTION_DOMAINS = (
    ("nytimes.com", "nytimes"),
    ("jamieoliver.com", "jamie-oliver"),
    ("bbcgoodfood.com", "bbc-goodfood"),
    ("waitrose.com", "waitrose-recipes"),
)
COLLECTION_TITLES = {
    "nytimes": "NYTimes",
    "jamie-oliver": "Jamie Oliver",
    "bbc-goodfood": "BBC Good Food",
    "waitrose-recipes": "Waitrose Recipes",
    "other": "Other",
}


class UrlRecipeImportError(ValueError):
    def __init__(self, message: str, *, status_code: int = 422, code: str = "recipe_import_failed") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class RecipeImportDraft(BaseModel):
    import_id: str
    submitted_url: str
    normalized_url: str
    hostname: str
    site_name: str
    collection_slug: str
    excerpt: str
    extraction: RecipeExtractionPayload
    retrieval_method: str = "direct"
    web_search_sources: list[str] = Field(default_factory=list)
    created_at: str


@dataclass(slots=True)
class FetchedRecipePage:
    submitted_url: str
    final_url: str
    canonical_url: str
    hostname: str
    site_name: str
    page_title: str
    model_text: str
    excerpt: str


def normalize_recipe_url(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise UrlRecipeImportError("Enter a recipe URL.", status_code=400, code="invalid_url")

    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise UrlRecipeImportError("The recipe URL is invalid.", status_code=400, code="invalid_url") from exc

    if parsed.scheme.casefold() not in {"http", "https"}:
        raise UrlRecipeImportError(
            "Recipe URLs must start with http:// or https://.",
            status_code=400,
            code="invalid_url",
        )
    if parsed.username or parsed.password:
        raise UrlRecipeImportError(
            "Recipe URLs cannot contain login credentials.",
            status_code=400,
            code="invalid_url",
        )
    if not parsed.hostname:
        raise UrlRecipeImportError("The recipe URL needs a hostname.", status_code=400, code="invalid_url")

    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").casefold().rstrip(".")
    except UnicodeError as exc:
        raise UrlRecipeImportError("The recipe hostname is invalid.", status_code=400, code="invalid_url") from exc

    display_host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = (parsed.scheme.casefold() == "http" and port == 80) or (
        parsed.scheme.casefold() == "https" and port == 443
    )
    netloc = display_host if port is None or default_port else f"{display_host}:{port}"
    query_items = [
        (key, item_value)
        for key, item_value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_QUERY_KEYS
    ]
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            netloc,
            parsed.path or "/",
            urlencode(query_items, doseq=True),
            "",
        )
    )


def collection_slug_for_hostname(hostname: str) -> str:
    normalized = hostname.casefold().rstrip(".")
    for domain, collection_slug in PUBLISHER_COLLECTION_DOMAINS:
        if normalized == domain or normalized.endswith(f".{domain}"):
            return collection_slug
    return "other"


def validate_public_url(
    url: str,
    *,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
) -> None:
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise UrlRecipeImportError(
            "The recipe URL must point to a public website.",
            status_code=400,
            code="unsafe_url",
        )

    try:
        addresses = resolver(
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise UrlRecipeImportError(
            "The recipe website could not be found.",
            status_code=422,
            code="source_unreachable",
        ) from exc

    if not addresses:
        raise UrlRecipeImportError(
            "The recipe website could not be found.",
            status_code=422,
            code="source_unreachable",
        )

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address[4][0])
        except (ValueError, IndexError) as exc:
            raise UrlRecipeImportError(
                "The recipe website resolved to an invalid address.",
                status_code=400,
                code="unsafe_url",
            ) from exc
        if not ip.is_global:
            raise UrlRecipeImportError(
                "The recipe URL must point to a public website.",
                status_code=400,
                code="unsafe_url",
            )


def fetch_recipe_page(
    submitted_url: str,
    *,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
    transport: httpx.BaseTransport | None = None,
) -> FetchedRecipePage:
    current_url = normalize_recipe_url(submitted_url)
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": "HeleyFamilyCookbook/1.0 (+recipe URL importer)",
    }
    timeout = httpx.Timeout(URL_FETCH_TIMEOUT_SECONDS)

    try:
        with httpx.Client(
            follow_redirects=False,
            headers=headers,
            timeout=timeout,
            transport=transport,
            trust_env=False,
        ) as client:
            for redirect_count in range(URL_FETCH_MAX_REDIRECTS + 1):
                validate_public_url(current_url, resolver=resolver)
                with client.stream("GET", current_url) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location", "").strip()
                        if not location:
                            raise UrlRecipeImportError(
                                "The recipe website returned an invalid redirect.",
                                status_code=422,
                                code="source_unreachable",
                            )
                        if redirect_count >= URL_FETCH_MAX_REDIRECTS:
                            raise UrlRecipeImportError(
                                "The recipe website redirected too many times.",
                                status_code=422,
                                code="source_unreachable",
                            )
                        current_url = normalize_recipe_url(urljoin(current_url, location))
                        continue

                    if response.status_code in {401, 403}:
                        raise UrlRecipeImportError(
                            "This recipe page is blocked or requires a login.",
                            status_code=422,
                            code="source_blocked",
                        )
                    if response.status_code >= 400:
                        raise UrlRecipeImportError(
                            f"The recipe website returned HTTP {response.status_code}.",
                            status_code=422,
                            code="source_unreachable",
                        )

                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
                    if content_type not in {"text/html", "application/xhtml+xml"}:
                        raise UrlRecipeImportError(
                            "The recipe URL did not return an HTML page.",
                            status_code=415,
                            code="unsupported_content_type",
                        )

                    chunks: list[bytes] = []
                    total_bytes = 0
                    for chunk in response.iter_bytes():
                        total_bytes += len(chunk)
                        if total_bytes > URL_FETCH_MAX_BYTES:
                            raise UrlRecipeImportError(
                                "The recipe page is too large to import.",
                                status_code=413,
                                code="source_too_large",
                            )
                        chunks.append(chunk)
                    encoding = response.encoding or "utf-8"
                    html = b"".join(chunks).decode(encoding, errors="replace")
                    return prepare_recipe_page(
                        html,
                        submitted_url=submitted_url.strip(),
                        final_url=current_url,
                    )
    except UrlRecipeImportError:
        raise
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise UrlRecipeImportError(
            "The recipe website could not be reached.",
            status_code=422,
            code="source_unreachable",
        ) from exc

    raise UrlRecipeImportError(
        "The recipe website did not return a usable page.",
        status_code=422,
        code="source_unreachable",
    )


def prepare_recipe_page(html: str, *, submitted_url: str, final_url: str) -> FetchedRecipePage:
    soup = BeautifulSoup(html, "html.parser")
    parsed_final = urlsplit(final_url)
    hostname = parsed_final.hostname or ""
    canonical_url = final_url
    canonical_link = soup.find("link", rel=lambda value: value and "canonical" in value)
    if canonical_link and canonical_link.get("href"):
        candidate = normalize_recipe_url(urljoin(final_url, str(canonical_link.get("href"))))
        if urlsplit(candidate).hostname == hostname:
            canonical_url = candidate

    site_name_node = soup.find("meta", attrs={"property": "og:site_name"})
    site_name = ""
    if site_name_node:
        site_name = str(site_name_node.get("content") or "").strip()
    site_name = site_name or hostname.removeprefix("www.")

    title_node = soup.find("meta", attrs={"property": "og:title"})
    page_title = str(title_node.get("content") or "").strip() if title_node else ""
    if not page_title and soup.h1:
        page_title = soup.h1.get_text(" ", strip=True)
    if not page_title and soup.title:
        page_title = soup.title.get_text(" ", strip=True)

    recipe_json: list[Any] = []
    for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(node.string or node.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        recipe_json.extend(_find_recipe_json(payload))

    for node in soup.find_all(["script", "style", "noscript", "svg", "nav", "footer", "form"]):
        node.decompose()
    content_root = soup.find("article") or soup.find("main") or soup.body or soup
    visible_lines = [
        " ".join(line.split())
        for line in content_root.get_text("\n", strip=True).splitlines()
        if " ".join(line.split())
    ]
    visible_text = "\n".join(visible_lines)
    recipe_json_text = json.dumps(recipe_json, ensure_ascii=False) if recipe_json else ""
    model_parts = [
        f"Page title: {page_title}" if page_title else "",
        f"Website: {site_name}",
        f"Source URL: {canonical_url}",
        f"Recipe JSON-LD:\n{recipe_json_text}" if recipe_json_text else "",
        f"Visible page text:\n{visible_text}",
    ]
    model_text = "\n\n".join(part for part in model_parts if part)[:URL_MODEL_TEXT_LIMIT]
    excerpt = visible_text[:URL_EXCERPT_LIMIT].strip()
    if not model_text or not excerpt:
        raise UrlRecipeImportError(
            "The recipe page did not contain readable text.",
            status_code=422,
            code="empty_source",
        )

    return FetchedRecipePage(
        submitted_url=submitted_url,
        final_url=final_url,
        canonical_url=canonical_url,
        hostname=hostname,
        site_name=site_name,
        page_title=page_title,
        model_text=model_text,
        excerpt=excerpt,
    )


def _find_recipe_json(value: Any) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    if isinstance(value, dict):
        raw_types = value.get("@type", [])
        types = [raw_types] if isinstance(raw_types, str) else raw_types
        if isinstance(types, list) and any(str(item).casefold() == "recipe" for item in types):
            matches.append(value)
        for nested in value.values():
            matches.extend(_find_recipe_json(nested))
    elif isinstance(value, list):
        for nested in value:
            matches.extend(_find_recipe_json(nested))
    return matches


class UrlRecipeImporter:
    def __init__(
        self,
        settings: Settings,
        *,
        fetcher: Callable[[str], FetchedRecipePage] = fetch_recipe_page,
        extractor_factory: Callable[[Settings], OpenAIRecipeExtractor] = OpenAIRecipeExtractor,
    ) -> None:
        self.settings = settings
        self.fetcher = fetcher
        self.extractor_factory = extractor_factory

    def preview(self, repository: Any, submitted_url: str) -> dict[str, Any]:
        normalized_submitted_url = normalize_recipe_url(submitted_url)
        existing = repository.get_recipe_for_source_url(normalized_submitted_url)
        if existing:
            return self._existing_result(repository, existing)
        if not self.settings.openai_api_key:
            raise UrlRecipeImportError(
                "OPENAI_API_KEY is required to import a recipe URL.",
                status_code=503,
                code="openai_unavailable",
            )

        extractor = self.extractor_factory(self.settings)
        retrieval_method = "direct"
        web_search_sources: list[str] = []
        try:
            page = self.fetcher(submitted_url.strip())
        except UrlRecipeImportError as fetch_error:
            if fetch_error.code != "source_unreachable":
                raise
            hostname = urlsplit(normalized_submitted_url).hostname or ""
            site_name = _site_name_for_hostname(hostname)
            canonical_url = normalized_submitted_url
            excerpt = ""
            retrieval_method = "openai_web_search"
            try:
                extraction, web_search_sources = extractor.extract_url_with_web_search(
                    source_url=canonical_url,
                    hostname=hostname,
                )
            except ValueError as exc:
                raise UrlRecipeImportError(str(exc), status_code=422, code="incomplete_recipe") from exc
            except Exception as exc:
                logger.exception("OpenAI web-search recipe extraction failed for %s", canonical_url)
                raise _openai_import_error(exc) from exc
            excerpt = _extraction_excerpt(extraction)
        else:
            canonical_url = page.canonical_url
            hostname = page.hostname
            site_name = page.site_name
            excerpt = page.excerpt
            existing = repository.get_recipe_for_source_url(canonical_url)
            if existing:
                return self._existing_result(repository, existing)
            try:
                extraction = extractor.extract_url_payload(
                    source_name=site_name,
                    source_url=canonical_url,
                    page_text=page.model_text,
                    excerpt=excerpt,
                )
            except ValueError as exc:
                raise UrlRecipeImportError(str(exc), status_code=422, code="incomplete_recipe") from exc
            except Exception as exc:
                logger.exception("OpenAI URL recipe extraction failed for %s", canonical_url)
                raise _openai_import_error(exc) from exc

        collection_slug = collection_slug_for_hostname(hostname)
        import_id = str(uuid4())
        draft = RecipeImportDraft(
            import_id=import_id,
            submitted_url=submitted_url.strip(),
            normalized_url=canonical_url,
            hostname=hostname,
            site_name=site_name,
            collection_slug=collection_slug,
            excerpt=excerpt,
            extraction=extraction,
            retrieval_method=retrieval_method,
            web_search_sources=web_search_sources,
            created_at=datetime.now(UTC).isoformat(),
        )
        repository.save_recipe_import_preview(
            import_id,
            draft.model_dump_json(),
            ttl_seconds=URL_IMPORT_PREVIEW_TTL_SECONDS,
        )
        preview_metadata = _payload_metadata(extraction)
        preview_metadata["retrieval_method"] = retrieval_method
        if web_search_sources:
            preview_metadata["web_search_sources"] = web_search_sources
        return {
            "status": "preview",
            "import_id": import_id,
            "source_url": canonical_url,
            "site_name": site_name,
            "collection_slug": collection_slug,
            "collection_title": COLLECTION_TITLES[collection_slug],
            "title": extraction.title.strip(),
            "ingredient_lines": [
                ingredient.raw.strip() for ingredient in extraction.ingredients if ingredient.raw.strip()
            ],
            "method_steps": [step.strip() for step in extraction.method_steps if step.strip()],
            "metadata": preview_metadata,
        }

    def commit(
        self,
        repository: Any,
        import_id: str,
        *,
        title: str,
        ingredient_lines: list[str],
        method_steps: list[str],
    ) -> dict[str, Any]:
        raw_draft = repository.load_recipe_import_preview(import_id)
        if not raw_draft:
            raise UrlRecipeImportError(
                "This recipe preview has expired. Extract the URL again.",
                status_code=410,
                code="preview_expired",
            )
        draft = RecipeImportDraft.model_validate_json(raw_draft)

        existing = repository.get_recipe_for_source_url(draft.normalized_url)
        if existing:
            repository.delete_recipe_import_preview(import_id)
            return self._existing_result(repository, existing)

        cleaned_title = " ".join(title.split()).strip()
        cleaned_ingredients = [" ".join(line.split()).strip() for line in ingredient_lines if line.strip()]
        cleaned_steps = [" ".join(step.split()).strip() for step in method_steps if step.strip()]
        if not cleaned_title or not cleaned_ingredients or not cleaned_steps:
            raise UrlRecipeImportError(
                "Title, ingredients, and method steps are all required.",
                status_code=422,
                code="invalid_preview",
            )
        if any(len(line) > 1_000 for line in [*cleaned_ingredients, *cleaned_steps]):
            raise UrlRecipeImportError(
                "An ingredient or method step is too long.",
                status_code=422,
                code="invalid_preview",
            )

        extracted_by_raw = {
            ingredient.raw.strip(): prepare_ingredient_mapping(ingredient.model_dump())
            for ingredient in draft.extraction.ingredients
            if ingredient.raw.strip()
        }
        ingredients = [
            extracted_by_raw.get(line) or prepare_ingredient_mapping(parse_ingredient_line(line).model_dump())
            for line in cleaned_ingredients
        ]
        source_metadata = _payload_metadata(draft.extraction)
        source_metadata.update(
            {
                "source_url": draft.normalized_url,
                "submitted_url": draft.submitted_url,
                "canonical_url": draft.normalized_url,
                "hostname": draft.hostname,
                "site_name": draft.site_name,
                "retrieval_method": draft.retrieval_method,
            }
        )
        if draft.web_search_sources:
            source_metadata["web_search_sources"] = draft.web_search_sources
        recipe_draft = RecipeDraft(
            title=cleaned_title,
            ingredients=ingredients,
            method_steps=cleaned_steps,
            source={
                "object_key": "",
                "format": "url",
                "anchor": draft.normalized_url,
                "excerpt": draft.excerpt,
                "metadata": source_metadata,
            },
            images=[],
            confidence=draft.extraction.confidence,
            notes=[*draft.extraction.notes, "url_import"],
            review_status="verified",
            review_reasons=[],
        )

        embeddings: list[list[float]] = []
        if self.settings.openai_api_key:
            try:
                embeddings = self.extractor_factory(self.settings).build_embeddings(
                    [recipe_draft.embedding_text()]
                )
            except Exception:
                logger.warning("Could not build embedding for imported URL recipe.", exc_info=True)

        try:
            recipe = repository.store_url_recipe(
                hostname=draft.hostname,
                site_name=draft.site_name,
                collection_slug=draft.collection_slug,
                normalized_url=draft.normalized_url,
                draft=recipe_draft,
                embedding=embeddings[0] if embeddings else None,
            )
        except ValueError as exc:
            raise UrlRecipeImportError(str(exc), status_code=409, code="import_conflict") from exc
        repository.delete_recipe_import_preview(import_id)
        return self._saved_result(repository, recipe, status="created")

    def _existing_result(self, repository: Any, recipe: Any) -> dict[str, Any]:
        return self._saved_result(repository, recipe, status="existing")

    def _saved_result(self, repository: Any, recipe: Any, *, status: str) -> dict[str, Any]:
        cookbook = repository.get_cookbook(recipe.cookbook_id)
        collection_slug = cookbook.collection_slug if cookbook and cookbook.collection_slug else "other"
        return {
            "status": status,
            "recipe_id": recipe.id,
            "title": recipe.title,
            "cookbook_title": recipe.cookbook_title,
            "collection_slug": collection_slug,
            "collection_title": COLLECTION_TITLES.get(collection_slug, collection_slug.replace("-", " ").title()),
        }


def _payload_metadata(payload: RecipeExtractionPayload) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key, value in (
        ("intro", payload.intro.strip()),
        ("serves", payload.serves.strip()),
        ("makes", payload.makes.strip()),
        ("yield", payload.yield_value.strip()),
        ("prep_time", payload.prep_time.strip()),
        ("cook_time", payload.cook_time.strip()),
        ("total_time", payload.total_time.strip()),
    ):
        if value:
            metadata[key] = value
    preparation_notes = [note.strip() for note in payload.preparation_notes if note.strip()]
    if preparation_notes:
        metadata["preparation_notes"] = preparation_notes
    supplemental_sections = [
        {
            "heading": section.heading.strip(),
            "lines": [line.strip() for line in section.lines if line.strip()],
        }
        for section in payload.supplemental_sections
        if section.heading.strip() and any(line.strip() for line in section.lines)
    ]
    if supplemental_sections:
        metadata["supplemental_sections"] = supplemental_sections
    return metadata


def _site_name_for_hostname(hostname: str) -> str:
    collection_slug = collection_slug_for_hostname(hostname)
    if collection_slug != "other":
        return COLLECTION_TITLES[collection_slug]
    return hostname.removeprefix("www.")


def _extraction_excerpt(payload: RecipeExtractionPayload) -> str:
    pieces = [payload.title.strip(), payload.intro.strip()]
    pieces.extend(
        ingredient.raw.strip()
        for ingredient in payload.ingredients[:12]
        if ingredient.raw.strip()
    )
    pieces.extend(step.strip() for step in payload.method_steps[:2] if step.strip())
    return "\n".join(piece for piece in pieces if piece)[:URL_EXCERPT_LIMIT]


def _openai_import_error(exc: Exception) -> UrlRecipeImportError:
    body = getattr(exc, "body", None)
    error_code = ""
    if isinstance(body, dict):
        error_code = str(body.get("code") or "")
        nested_error = body.get("error")
        if not error_code and isinstance(nested_error, dict):
            error_code = str(nested_error.get("code") or "")
    error_text = str(exc).casefold()

    if isinstance(exc, RateLimitError):
        if error_code == "credit_balance_exhausted" or "no credits remaining" in error_text:
            return UrlRecipeImportError(
                "OpenAI API credits have run out. Add credits in the OpenAI API billing settings, then try again.",
                status_code=503,
                code="openai_quota_exhausted",
            )
        return UrlRecipeImportError(
            "OpenAI is temporarily rate-limiting recipe imports. Try again shortly.",
            status_code=429,
            code="openai_rate_limited",
        )
    if isinstance(exc, AuthenticationError):
        return UrlRecipeImportError(
            "The configured OpenAI API key was rejected. Update the API key and try again.",
            status_code=503,
            code="openai_authentication_failed",
        )
    return UrlRecipeImportError(
        "The recipe could not be extracted right now.",
        status_code=502,
        code="openai_error",
    )
