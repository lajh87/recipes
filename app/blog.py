from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


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
        image_path="/blog/ingredient-network-2026-04-13.svg",
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
        image_path="/blog/meal-planning-origin.svg",
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
