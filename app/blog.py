from __future__ import annotations

from collections import Counter, defaultdict, deque
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.models import RecipeRecord


BASE_DIR = Path(__file__).resolve().parent
INGREDIENT_NETWORK_SLUG = "mapping-the-ingredient-network"
INGREDIENT_NETWORK_PREVIEW_MAX_NODES = 180
INGREDIENT_NETWORK_PREVIEW_MAX_EDGES = 900
INGREDIENT_NETWORK_DEFAULT_DISPLAY_NODES = 100
INGREDIENT_NETWORK_MIN_DISPLAY_NODES = 20


@dataclass(frozen=True)
class BlogSection:
    title: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class BlogPost:
    slug: str
    title: str
    published_at: str
    published_label: str
    author: str
    excerpt: str
    image_path: str
    image_alt: str
    paragraphs: tuple[str, ...]
    figure_caption: str = ""
    key_stats: tuple[tuple[str, str], ...] = ()
    sections: tuple[BlogSection, ...] = ()
    figure_kind: str = "image"
    network_data: dict[str, Any] | None = None


def _load_json_summary(relative_path: str) -> dict:
    path = BASE_DIR / relative_path
    return json.loads(path.read_text(encoding="utf-8"))


def _format_ranked_items(items: list[dict], *, decimals: int = 3) -> tuple[str, ...]:
    return tuple(
        f'{index + 1}. {item["ingredient"]} ({item["value"]:.{decimals}f})'
        for index, item in enumerate(items)
    )


def _format_ranked_counts(items: list[dict]) -> tuple[str, ...]:
    return tuple(
        f'{index + 1}. {item["ingredient"]} ({item["value"]})'
        for index, item in enumerate(items)
    )


def _format_ranked_edges(items: list[dict]) -> tuple[str, ...]:
    return tuple(
        f'{index + 1}. {item["source"]} + {item["target"]} ({item["value"]})'
        for index, item in enumerate(items)
    )


def _rank_lookup(items: list[dict[str, Any]]) -> dict[str, float]:
    return {
        str(item["ingredient"]): float(item["value"])
        for item in items
        if item.get("ingredient") is not None and item.get("value") is not None
    }


def _with_network_display_defaults(data: dict[str, Any]) -> dict[str, Any]:
    preview_node_count = len(data.get("nodes", []))
    preview_edge_count = len(data.get("links", []))
    slider_max = preview_node_count
    slider_min = min(preview_node_count, INGREDIENT_NETWORK_MIN_DISPLAY_NODES) if preview_node_count else 0
    default_display = min(preview_node_count, INGREDIENT_NETWORK_DEFAULT_DISPLAY_NODES) if preview_node_count else 0
    slider_step = 1 if preview_node_count <= 40 else 5
    return {
        **data,
        "preview_node_count": preview_node_count,
        "preview_edge_count": preview_edge_count,
        "slider_min_node_count": slider_min,
        "slider_max_node_count": slider_max,
        "slider_step": slider_step,
        "default_display_node_count": default_display,
    }


def _derive_network_data(summary: dict[str, Any]) -> dict[str, Any]:
    graph_preview = summary.get("graph_preview")
    if isinstance(graph_preview, dict) and graph_preview.get("nodes") and graph_preview.get("links"):
        return _with_network_display_defaults(graph_preview)

    top_edges = summary.get("top_cooccurrence_edges", [])
    degree_lookup = _rank_lookup(summary.get("top_degree_centrality", []))
    weighted_degree_lookup = _rank_lookup(summary.get("top_weighted_degree", []))
    closeness_lookup = _rank_lookup(summary.get("top_closeness_centrality", []))
    weighted_closeness_lookup = _rank_lookup(summary.get("top_weighted_closeness_centrality", []))
    frequency_lookup = _rank_lookup(summary.get("top_frequency", []))

    node_order: list[str] = []
    for collection in (
        summary.get("top_weighted_degree", []),
        summary.get("top_degree_centrality", []),
        summary.get("top_closeness_centrality", []),
        summary.get("top_weighted_closeness_centrality", []),
        summary.get("top_frequency", []),
    ):
        for item in collection:
            ingredient = str(item.get("ingredient", "")).strip()
            if ingredient and ingredient not in node_order:
                node_order.append(ingredient)

    for edge in top_edges:
        for key in ("source", "target"):
            ingredient = str(edge.get(key, "")).strip()
            if ingredient and ingredient not in node_order:
                node_order.append(ingredient)

    edge_strengths: dict[str, float] = {}
    for edge in top_edges:
        source = str(edge.get("source", "")).strip()
        target = str(edge.get("target", "")).strip()
        weight = float(edge.get("value", 0) or 0)
        if source:
            edge_strengths[source] = edge_strengths.get(source, 0.0) + weight
        if target:
            edge_strengths[target] = edge_strengths.get(target, 0.0) + weight

    nodes = [
        {
            "id": ingredient,
            "label": ingredient,
            "frequency": frequency_lookup.get(ingredient, 0.0),
            "degree_centrality": degree_lookup.get(ingredient, 0.0),
            "weighted_degree": weighted_degree_lookup.get(ingredient, edge_strengths.get(ingredient, 0.0)),
            "closeness_centrality": closeness_lookup.get(ingredient, 0.0),
            "weighted_closeness": weighted_closeness_lookup.get(ingredient, 0.0),
        }
        for ingredient in node_order
    ]
    links = [
        {
            "source": str(edge.get("source", "")).strip(),
            "target": str(edge.get("target", "")).strip(),
            "value": int(edge.get("value", 0) or 0),
        }
        for edge in top_edges
        if str(edge.get("source", "")).strip() and str(edge.get("target", "")).strip()
    ]
    return _with_network_display_defaults(
        {
            "min_occurrence": int(summary.get("min_occurrence", 0) or 0),
            "node_count": int(summary.get("node_count", len(nodes)) or len(nodes)),
            "edge_count": int(summary.get("edge_count", len(links)) or len(links)),
            "nodes": nodes,
            "links": links,
        }
    )


def build_ingredient_network_preview(
    recipes: list[RecipeRecord],
    *,
    min_occurrence: int = 3,
    max_nodes: int = INGREDIENT_NETWORK_PREVIEW_MAX_NODES,
    max_edges: int = INGREDIENT_NETWORK_PREVIEW_MAX_EDGES,
) -> dict[str, Any]:
    node_counts: Counter[str] = Counter()
    recipe_ingredients: list[list[str]] = []
    for recipe in recipes:
        ingredients = sorted({name.strip() for name in recipe.ingredient_names if name.strip()})
        if not ingredients:
            continue
        recipe_ingredients.append(ingredients)
        node_counts.update(ingredients)

    kept_nodes = {name for name, count in node_counts.items() if count >= min_occurrence}
    ranked_nodes = [
        name
        for name, _count in sorted(node_counts.items(), key=lambda item: (-item[1], item[0]))
        if name in kept_nodes
    ][:max_nodes]
    selected = set(ranked_nodes)

    edge_weights: Counter[tuple[str, str]] = Counter()
    adjacency: dict[str, set[str]] = defaultdict(set)
    weighted_degree: Counter[str] = Counter()
    for ingredients in recipe_ingredients:
        filtered = [name for name in ingredients if name in selected]
        if len(filtered) < 2:
            continue
        for index, left in enumerate(filtered):
            for right in filtered[index + 1 :]:
                edge = (left, right) if left < right else (right, left)
                edge_weights[edge] += 1

    ranked_edges = sorted(edge_weights.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))[:max_edges]
    for (left, right), weight in ranked_edges:
        adjacency[left].add(right)
        adjacency[right].add(left)
        weighted_degree[left] += weight
        weighted_degree[right] += weight

    nodes = ranked_nodes
    if not nodes:
        return _with_network_display_defaults(
            {
                "min_occurrence": min_occurrence,
                "node_count": 0,
                "edge_count": 0,
                "nodes": [],
                "links": [],
            }
        )

    node_set = set(nodes)
    connected_adjacency = {
        node: {neighbor for neighbor in adjacency.get(node, set()) if neighbor in node_set}
        for node in nodes
    }
    closeness = _closeness_centrality(nodes, connected_adjacency)
    node_count = len(nodes)
    return _with_network_display_defaults(
        {
            "min_occurrence": min_occurrence,
            "node_count": node_count,
            "edge_count": len(edge_weights),
            "nodes": [
                {
                    "id": node,
                    "label": node,
                    "frequency": int(node_counts.get(node, 0)),
                    "degree_centrality": (len(connected_adjacency.get(node, set())) / max(node_count - 1, 1)),
                    "weighted_degree": int(weighted_degree.get(node, 0)),
                    "closeness_centrality": closeness.get(node, 0.0),
                    "weighted_closeness": closeness.get(node, 0.0),
                }
                for node in nodes
            ],
            "links": [
                {"source": left, "target": right, "value": int(weight)}
                for (left, right), weight in ranked_edges
                if left in node_set and right in node_set
            ],
        }
    )


def enrich_blog_post(post: BlogPost, recipes: list[RecipeRecord] | None = None) -> BlogPost:
    if post.slug != INGREDIENT_NETWORK_SLUG or recipes is None:
        return post

    summary = _load_json_summary("blog_data/ingredient-network-2026-04-13.json")
    network_data = build_ingredient_network_preview(
        recipes,
        min_occurrence=int(summary.get("min_occurrence", 3) or 3),
        max_nodes=INGREDIENT_NETWORK_PREVIEW_MAX_NODES,
        max_edges=INGREDIENT_NETWORK_PREVIEW_MAX_EDGES,
    )
    network_data["node_count"] = int(summary.get("node_count", network_data["node_count"]) or network_data["node_count"])
    network_data["edge_count"] = int(summary.get("edge_count", network_data["edge_count"]) or network_data["edge_count"])
    return replace(post, network_data=network_data)


def _closeness_centrality(nodes: list[str], adjacency: dict[str, set[str]]) -> dict[str, float]:
    closeness: dict[str, float] = {}
    for node in nodes:
        distances = _shortest_paths(node, adjacency)
        reachable = len(distances) - 1
        total_distance = sum(distance for target, distance in distances.items() if target != node)
        closeness[node] = (reachable / total_distance) if reachable > 0 and total_distance else 0.0
    return closeness


def _shortest_paths(source: str, adjacency: dict[str, set[str]]) -> dict[str, int]:
    distances = {source: 0}
    queue = deque([source])
    while queue:
        node = queue.popleft()
        for neighbor in adjacency.get(node, set()):
            if neighbor in distances:
                continue
            distances[neighbor] = distances[node] + 1
            queue.append(neighbor)
    return distances


def _ingredient_network_post() -> BlogPost:
    summary = _load_json_summary("blog_data/ingredient-network-2026-04-13.json")
    top_degree = summary["top_degree_centrality"][:5]
    top_closeness = summary["top_closeness_centrality"][:5]
    top_edges = summary["top_cooccurrence_edges"][:6]
    top_frequency = summary["top_frequency"][:5]

    return BlogPost(
        slug="mapping-the-ingredient-network",
        title="Mapping The Ingredient Network",
        published_at="2026-04-13",
        published_label="April 13, 2026",
        author="Luke",
        excerpt=(
            "A first look at the ingredient co-occurrence graph across the recipe library, including "
            "centrality, path length, density, and the strongest recurring pairings."
        ),
        image_path="blog/ingredient-network-2026-04-13.svg",
        image_alt=(
            "Ingredient co-occurrence network diagram showing olive oil, salt, garlic, onion, butter, and other central pantry ingredients."
        ),
        paragraphs=(
            f'I wanted a way to see the library as a connected cooking system rather than just as individual recipes. '
            f'This network uses ingredients as nodes and connects any two ingredients that appear together in the same recipe. '
            f'To keep the graph interpretable, I treated it as a core network and kept only ingredients that appear in at least '
            f'{summary["min_occurrence"]} recipes.',
            f'That leaves a graph of {summary["node_count"]} ingredients and {summary["edge_count"]} co-occurrence links drawn from '
            f'{summary["recipe_count"]} recipes. The whole filtered graph sits inside a single connected component, which is useful: '
            f'it means the ingredient vocabulary is not splintered into isolated cuisines or one-off clusters, but tied together by a shared pantry backbone.',
            f'The shortest-path structure is tight. The average distance across the network is {summary["average_shortest_path"]:.2f} steps and the diameter is only '
            f'{summary["diameter"]}, so the graph is compact. In practice, that means most ingredients are only one or two hops away from one another through common recipe combinations.',
            f'At the centre, the graph behaves exactly like a home-cooking pantry. {top_degree[0]["ingredient"].title()}, '
            f'{top_degree[1]["ingredient"]}, and {top_degree[2]["ingredient"]} dominate both degree and closeness centrality, which is a good sign that the network is picking up the actual structural ingredients of the collection rather than noise.',
            f'The strongest links are also intuitive. {top_edges[0]["source"]} + {top_edges[0]["target"]} appears together in '
            f'{top_edges[0]["value"]} recipes, followed by {top_edges[1]["source"]} + {top_edges[1]["target"]} in {top_edges[1]["value"]}. '
            f'This is the kind of view that should become useful later for recipe discovery, substitution suggestions, shopping prompts, and spotting where the library is over-indexed on the same flavour base.',
        ),
        figure_caption=(
            "Nodes are ingredients, node size reflects weighted connectivity, and line weight reflects how many recipes contain a pair together."
        ),
        key_stats=(
            ("Recipes", str(summary["recipe_count"])),
            ("Ingredients", str(summary["node_count"])),
            ("Links", str(summary["edge_count"])),
            ("Density", f'{summary["density"]:.4f}'),
            ("Avg Shortest Path", f'{summary["average_shortest_path"]:.2f}'),
            ("Diameter", str(summary["diameter"])),
        ),
        sections=(
            BlogSection(
                title="Highest Degree Centrality",
                items=_format_ranked_items(top_degree),
            ),
            BlogSection(
                title="Highest Closeness Centrality",
                items=_format_ranked_items(top_closeness),
            ),
            BlogSection(
                title="Most Frequent Ingredients",
                items=_format_ranked_counts(top_frequency),
            ),
            BlogSection(
                title="Strongest Co-occurrence Pairs",
                items=_format_ranked_edges(top_edges),
            ),
        ),
        figure_kind="interactive_network",
        network_data=_derive_network_data(summary),
    )


BLOG_POSTS = (
    _ingredient_network_post(),
    BlogPost(
        slug="why-this-cookbook-exists",
        title="Why This Cookbook Exists",
        published_at="2026-04-13",
        published_label="April 13, 2026",
        author="Luke",
        excerpt=(
            "A note on building a calmer way to agree the week’s meals, do the shop together, "
            "and actually use the recipes we already have."
        ),
        image_path="blog/meal-planning-origin.svg",
        image_alt=(
            "Meal planning notes showing dinners, lunches, and a running edit history from a shared planning document."
        ),
        paragraphs=(
            "This project started because our weekly meal planning process was too loose to be useful. "
            "My wife and I would agree a rough list of meals for the week, then do the shopping, but the plan itself "
            "lived in scattered notes and half-remembered messages rather than in a system we could actually rely on.",
            "That meant the same problems kept repeating. We would forget a good idea from the week before, lose track "
            "of what we had already agreed, or default back to the same dependable dinners because it was faster than "
            "searching for a recipe again. The result was more repetition than we wanted and less use of the recipe library "
            "we had already spent time building.",
            "The goal of this app is to make that weekly rhythm simpler. We can settle the plan together, connect meals to "
            "real recipes, and turn the plan into something concrete enough to support the shop. It should reduce the friction "
            "between deciding what to eat and getting the ingredients in, while also nudging me to cook from recipes more often "
            "instead of rotating through the same fallback meals.",
            "In short, this is a practical tool for household planning, not just a recipe archive. If it works properly, it "
            "helps us agree the week faster, shop with less guesswork, and keep dinner more varied without making the process "
            "feel like admin.",
        ),
    ),
)

BLOG_POSTS_BY_SLUG = {post.slug: post for post in BLOG_POSTS}
