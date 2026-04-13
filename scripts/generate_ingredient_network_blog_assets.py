from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from datetime import UTC, datetime
import heapq
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from app.config import get_settings
from app.repository import LibraryRepository


def build_graph(
    recipes: list[Any],
    *,
    min_occurrence: int,
) -> tuple[dict[str, int], dict[tuple[str, str], int], dict[str, dict[str, int]]]:
    node_counts: Counter[str] = Counter()
    raw_recipe_ingredients: list[list[str]] = []

    for recipe in recipes:
        ingredients = sorted({name.strip() for name in recipe.ingredient_names if name.strip()})
        if not ingredients:
            continue
        raw_recipe_ingredients.append(ingredients)
        node_counts.update(ingredients)

    kept_nodes = {name for name, count in node_counts.items() if count >= min_occurrence}
    filtered_counts = {name: count for name, count in node_counts.items() if name in kept_nodes}

    edge_weights: Counter[tuple[str, str]] = Counter()
    adjacency: dict[str, dict[str, int]] = {name: {} for name in kept_nodes}
    for ingredients in raw_recipe_ingredients:
        filtered = [name for name in ingredients if name in kept_nodes]
        if len(filtered) < 2:
            continue
        for index, left in enumerate(filtered):
            for right in filtered[index + 1 :]:
                edge = (left, right) if left < right else (right, left)
                edge_weights[edge] += 1

    for (left, right), weight in edge_weights.items():
        adjacency[left][right] = weight
        adjacency[right][left] = weight

    return filtered_counts, dict(edge_weights), adjacency


def connected_components(adjacency: dict[str, dict[str, int]]) -> list[list[str]]:
    remaining = set(adjacency)
    components: list[list[str]] = []
    while remaining:
        start = remaining.pop()
        queue = deque([start])
        component = [start]
        while queue:
            node = queue.popleft()
            for neighbor in adjacency[node]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
                    component.append(neighbor)
        components.append(sorted(component))
    components.sort(key=len, reverse=True)
    return components


def shortest_paths(adjacency: dict[str, dict[str, int]], source: str) -> dict[str, int]:
    distances = {source: 0}
    queue = deque([source])
    while queue:
        node = queue.popleft()
        for neighbor in adjacency[node]:
            if neighbor in distances:
                continue
            distances[neighbor] = distances[node] + 1
            queue.append(neighbor)
    return distances


def weighted_distances(adjacency: dict[str, dict[str, int]], source: str) -> dict[str, float]:
    distances = {source: 0.0}
    heap: list[tuple[float, str]] = [(0.0, source)]
    while heap:
        distance, node = heapq.heappop(heap)
        if distance > distances[node]:
            continue
        for neighbor, weight in adjacency[node].items():
            step = 1.0 / max(weight, 1)
            candidate = distance + step
            current = distances.get(neighbor)
            if current is None or candidate < current:
                distances[neighbor] = candidate
                heapq.heappush(heap, (candidate, neighbor))
    return distances


def force_layout(
    nodes: list[str],
    adjacency: dict[str, dict[str, int]],
    *,
    width: float = 1040.0,
    height: float = 760.0,
    seed: int = 7,
    iterations: int = 220,
) -> dict[str, tuple[float, float]]:
    if not nodes:
        return {}

    rng = np.random.default_rng(seed)
    positions = rng.random((len(nodes), 2))
    positions[:, 0] *= width
    positions[:, 1] *= height
    node_index = {node: index for index, node in enumerate(nodes)}
    area = width * height
    k = math.sqrt(area / max(len(nodes), 1))

    edges = [
        (node_index[left], node_index[right], adjacency[left][right])
        for left in nodes
        for right in adjacency[left]
        if left < right and right in node_index
    ]

    for iteration in range(iterations):
        disp = np.zeros_like(positions)

        for left in range(len(nodes)):
            delta = positions[left] - positions
            distance = np.linalg.norm(delta, axis=1)
            distance[left] = 1.0
            repulsive = (k * k) / distance
            normalized = delta / distance[:, None]
            disp[left] += np.sum(normalized * repulsive[:, None], axis=0)

        for left, right, weight in edges:
            delta = positions[left] - positions[right]
            distance = float(np.linalg.norm(delta)) or 1.0
            attractive = (distance * distance / k) * (0.35 + min(weight, 12) / 12)
            direction = delta / distance
            disp[left] -= direction * attractive
            disp[right] += direction * attractive

        temperature = max(width, height) * (0.12 * (1 - iteration / iterations))
        lengths = np.linalg.norm(disp, axis=1)
        lengths[lengths == 0] = 1.0
        positions += (disp / lengths[:, None]) * np.minimum(lengths[:, None], temperature)
        positions[:, 0] = np.clip(positions[:, 0], 40, width - 40)
        positions[:, 1] = np.clip(positions[:, 1], 40, height - 40)

    return {node: (float(positions[index, 0]), float(positions[index, 1])) for index, node in enumerate(nodes)}


def summarize_network(
    node_counts: dict[str, int],
    edge_weights: dict[tuple[str, str], int],
    adjacency: dict[str, dict[str, int]],
    *,
    recipe_count: int,
    min_occurrence: int,
) -> dict[str, Any]:
    node_count = len(node_counts)
    edge_count = len(edge_weights)
    degrees = {node: len(neighbors) for node, neighbors in adjacency.items()}
    strengths = {node: sum(neighbors.values()) for node, neighbors in adjacency.items()}
    density = (2 * edge_count / (node_count * (node_count - 1))) if node_count > 1 else 0.0
    average_degree = (sum(degrees.values()) / node_count) if node_count else 0.0

    components = connected_components(adjacency)
    giant_component = components[0] if components else []
    giant_share = (len(giant_component) / node_count) if node_count else 0.0
    giant_adjacency = {node: {neighbor: weight for neighbor, weight in adjacency[node].items() if neighbor in giant_component} for node in giant_component}

    closeness: dict[str, float] = {}
    average_shortest_path = 0.0
    diameter = 0
    pair_count = 0
    distance_sum = 0
    weighted_closeness: dict[str, float] = {}
    for node in giant_component:
        distances = shortest_paths(giant_adjacency, node)
        weighted = weighted_distances(giant_adjacency, node)
        total_distance = sum(distance for target, distance in distances.items() if target != node)
        total_weighted_distance = sum(distance for target, distance in weighted.items() if target != node)
        reachable = len(distances) - 1
        if reachable > 0:
            closeness[node] = reachable / total_distance if total_distance else 0.0
            weighted_closeness[node] = reachable / total_weighted_distance if total_weighted_distance else 0.0
        max_distance = max(distances.values(), default=0)
        diameter = max(diameter, max_distance)
        for target, distance in distances.items():
            if target <= node or target == node:
                continue
            pair_count += 1
            distance_sum += distance
    average_shortest_path = (distance_sum / pair_count) if pair_count else 0.0

    degree_centrality = {
        node: (degrees[node] / (node_count - 1)) if node_count > 1 else 0.0
        for node in adjacency
    }

    top_degree = sorted(degree_centrality.items(), key=lambda item: (-item[1], item[0]))[:8]
    top_strength = sorted(strengths.items(), key=lambda item: (-item[1], item[0]))[:8]
    top_closeness = sorted(closeness.items(), key=lambda item: (-item[1], item[0]))[:8]
    top_weighted_closeness = sorted(weighted_closeness.items(), key=lambda item: (-item[1], item[0]))[:8]
    top_frequency = sorted(node_counts.items(), key=lambda item: (-item[1], item[0]))[:10]
    top_edges = sorted(edge_weights.items(), key=lambda item: (-item[1], item[0]))[:10]

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "recipe_count": recipe_count,
        "min_occurrence": min_occurrence,
        "node_count": node_count,
        "edge_count": edge_count,
        "density": density,
        "average_degree": average_degree,
        "component_count": len(components),
        "giant_component_node_count": len(giant_component),
        "giant_component_share": giant_share,
        "average_shortest_path": average_shortest_path,
        "diameter": diameter,
        "top_degree_centrality": [{"ingredient": name, "value": value} for name, value in top_degree],
        "top_weighted_degree": [{"ingredient": name, "value": value} for name, value in top_strength],
        "top_closeness_centrality": [{"ingredient": name, "value": value} for name, value in top_closeness],
        "top_weighted_closeness_centrality": [
            {"ingredient": name, "value": value} for name, value in top_weighted_closeness
        ],
        "top_frequency": [{"ingredient": name, "value": value} for name, value in top_frequency],
        "top_cooccurrence_edges": [
            {"source": left, "target": right, "value": weight}
            for (left, right), weight in top_edges
        ],
    }


def render_svg(
    output_path: Path,
    node_counts: dict[str, int],
    edge_weights: dict[tuple[str, str], int],
    adjacency: dict[str, dict[str, int]],
    summary: dict[str, Any],
) -> None:
    top_nodes = [item["ingredient"] for item in summary["top_weighted_degree"][:36]]
    selected = set(top_nodes)
    selected_edges = [
        (left, right, weight)
        for (left, right), weight in edge_weights.items()
        if left in selected and right in selected and weight >= 6
    ]
    selected_edges.sort(key=lambda item: (-item[2], item[0], item[1]))
    if len(selected_edges) > 90:
        selected_edges = selected_edges[:90]

    connected = {node for edge in selected_edges for node in edge[:2]}
    nodes = [node for node in top_nodes if node in connected] or top_nodes[:24]
    node_adjacency = {
        node: {neighbor: adjacency[node][neighbor] for neighbor in adjacency[node] if neighbor in set(nodes)}
        for node in nodes
    }
    positions = force_layout(nodes, node_adjacency)
    strengths = {node: sum(node_adjacency[node].values()) for node in nodes}
    max_strength = max(strengths.values(), default=1)
    min_strength = min(strengths.values(), default=0)

    width = 1240
    height = 920
    chart_x = 44
    chart_y = 118
    chart_width = 820
    chart_height = 740

    def chart_point(node: str) -> tuple[float, float]:
        x, y = positions[node]
        return chart_x + x * (chart_width / 1040.0), chart_y + y * (chart_height / 760.0)

    lines: list[str] = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1240 920" role="img" aria-labelledby="title desc">',
        "<title id=\"title\">Ingredient co-occurrence network</title>",
        (
            "<desc id=\"desc\">A network diagram showing ingredients as nodes and co-occurrence inside recipes as links. "
            "More central ingredients appear larger and warmer in colour.</desc>"
        ),
        '<rect width="1240" height="920" fill="#f7f3ea"/>',
        '<rect x="20" y="20" width="1200" height="880" rx="28" fill="#fffdf9" stroke="#e7dfd2"/>',
        '<text x="48" y="64" fill="#171717" font-family="Avenir Next, Helvetica Neue, Arial, sans-serif" font-size="28" font-weight="700">Ingredient Co-occurrence Network</text>',
        '<text x="48" y="92" fill="#5d5a54" font-family="Avenir Next, Helvetica Neue, Arial, sans-serif" font-size="15">'
        f'Ingredients appearing in at least {summary["min_occurrence"]} recipes. Link weight = shared recipes.</text>',
        '<rect x="40" y="118" width="828" height="744" rx="22" fill="#fcfbf7" stroke="#ece5da"/>',
        '<rect x="890" y="118" width="300" height="744" rx="22" fill="#f8f5ee" stroke="#ece5da"/>',
    ]

    max_edge = max((weight for _left, _right, weight in selected_edges), default=1)
    for left, right, weight in selected_edges:
        x1, y1 = chart_point(left)
        x2, y2 = chart_point(right)
        opacity = 0.12 + (weight / max_edge) * 0.44
        stroke_width = 1.2 + (weight / max_edge) * 4.8
        lines.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="#8a775b" stroke-opacity="{opacity:.3f}" stroke-width="{stroke_width:.2f}" />'
        )

    label_nodes = set(node for node, _value in sorted(strengths.items(), key=lambda item: (-item[1], item[0]))[:16])
    for node in nodes:
        x, y = chart_point(node)
        normalized_strength = (strengths[node] - min_strength) / max(max_strength - min_strength, 1)
        radius = 8 + normalized_strength * 22
        hue = 42 - normalized_strength * 14
        fill = f"hsl({hue:.1f} 72% 58%)"
        lines.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{fill}" fill-opacity="0.92" stroke="#fffaf2" stroke-width="2.5" />'
        )
        if node in label_nodes:
            lines.append(
                f'<text x="{x + radius + 8:.1f}" y="{y + 5:.1f}" fill="#3e3a34" '
                'font-family="Avenir Next, Helvetica Neue, Arial, sans-serif" font-size="16" font-weight="600">'
                f"{escape_xml(node)}</text>"
            )

    lines.extend(
        [
            '<text x="916" y="154" fill="#262626" font-family="Avenir Next, Helvetica Neue, Arial, sans-serif" font-size="18" font-weight="700">Network Snapshot</text>',
            stat_line(916, 192, "Recipes", str(summary["recipe_count"])),
            stat_line(916, 232, "Ingredients", str(summary["node_count"])),
            stat_line(916, 272, "Links", str(summary["edge_count"])),
            stat_line(916, 312, "Density", f'{summary["density"]:.4f}'),
            stat_line(916, 352, "Avg degree", f'{summary["average_degree"]:.1f}'),
            stat_line(916, 392, "Largest component", f'{summary["giant_component_node_count"]} ({summary["giant_component_share"]:.0%})'),
            stat_line(916, 432, "Avg shortest path", f'{summary["average_shortest_path"]:.2f}'),
            stat_line(916, 472, "Diameter", str(summary["diameter"])),
            '<text x="916" y="540" fill="#262626" font-family="Avenir Next, Helvetica Neue, Arial, sans-serif" font-size="18" font-weight="700">Top Connections</text>',
        ]
    )

    for index, edge in enumerate(summary["top_cooccurrence_edges"][:6]):
        y = 576 + index * 38
        lines.append(
            f'<text x="916" y="{y}" fill="#4c4944" font-family="Avenir Next, Helvetica Neue, Arial, sans-serif" font-size="15">'
            f'{index + 1}. {escape_xml(edge["source"])} + {escape_xml(edge["target"])} '
            f'<tspan fill="#8a775b">({edge["value"]})</tspan></text>'
        )

    lines.append("</svg>")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def stat_line(x: int, y: int, label: str, value: str) -> str:
    return (
        f'<text x="{x}" y="{y}" fill="#6b675f" font-family="Avenir Next, Helvetica Neue, Arial, sans-serif" font-size="13" '
        f'text-transform="uppercase" letter-spacing="0.08em">{escape_xml(label)}</text>'
        f'<text x="{x}" y="{y + 22}" fill="#1f1f1f" font-family="Avenir Next, Helvetica Neue, Arial, sans-serif" '
        f'font-size="24" font-weight="700">{escape_xml(value)}</text>'
    )


def escape_xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-04-13")
    parser.add_argument("--min-occurrence", type=int, default=3)
    parser.add_argument("--output-dir", default="app/static/blog")
    parser.add_argument("--summary-dir", default="app/blog_data")
    args = parser.parse_args()

    settings = get_settings()
    repository = LibraryRepository.from_settings(settings)
    try:
        recipes = repository.list_recipes()
    finally:
        repository.close()

    node_counts, edge_weights, adjacency = build_graph(recipes, min_occurrence=args.min_occurrence)
    summary = summarize_network(
        node_counts,
        edge_weights,
        adjacency,
        recipe_count=len(recipes),
        min_occurrence=args.min_occurrence,
    )

    slug = f"ingredient-network-{args.date}"
    output_dir = Path(args.output_dir)
    summary_dir = Path(args.summary_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_dir.mkdir(parents=True, exist_ok=True)
    svg_path = output_dir / f"{slug}.svg"
    json_path = summary_dir / f"{slug}.json"

    render_svg(svg_path, node_counts, edge_weights, adjacency, summary)
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "svg_path": str(svg_path),
                "json_path": str(json_path),
                "node_count": summary["node_count"],
                "edge_count": summary["edge_count"],
                "average_shortest_path": round(summary["average_shortest_path"], 4),
                "diameter": summary["diameter"],
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
