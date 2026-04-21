from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
from typing import Callable, TypeAlias
from uuid import uuid4

from app.ingredients import ingredient_index_name
from app.models import RecipeRecord, RecipeReferenceRecord

MONTH_PATTERN = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
DATE_SECTION_RE = re.compile(
    rf"^(?:week(?:\s+of)?\s+)?\d{{1,2}}\s+(?:{MONTH_PATTERN})(?:\s+\d{{2,4}})?$",
    re.IGNORECASE,
)
WEEK_SECTION_RE = re.compile(
    rf"^week(?:\s+\d{{1,2}})?(?:\s+of)?\s+(?:\d{{1,2}}\s+)?(?:{MONTH_PATTERN})(?:\s+\d{{2,4}})?$",
    re.IGNORECASE,
)
CHECKBOX_RE = re.compile(r"^-\s*\[(?P<state>[ xX])\]\s*(?P<body>.+)$")
BULLET_RE = re.compile(r"^(?:-|•|\*)\s*(?P<body>.+)$")
URL_RE = re.compile(r"https?://\S+")
RECIPE_REF_RE = re.compile(r"\[([^\[\]]+)\]\s*$")
WEEKDAY_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
SCHEDULE_SPLIT_RE = re.compile(r"\t+|\s{2,}")
MEAL_GROUP_TITLES = {"meal", "meals", "dinner", "dinners", "lunch", "lunches", "snacks"}
NON_RECIPE_HINTS = {
    "out",
    "leftover",
    "leftovers",
    "luke out",
    "tally out",
    "snack",
    "snacks",
    "soup",
}
TOKEN_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "fresh",
    "of",
    "or",
    "style",
    "the",
    "to",
    "with",
}
TOKEN_ALIASES = {
    "dahl": "daal",
    "dal": "daal",
}
WEEKDAYS = (
    ("monday", "Monday"),
    ("tuesday", "Tuesday"),
    ("wednesday", "Wednesday"),
    ("thursday", "Thursday"),
    ("friday", "Friday"),
    ("saturday", "Saturday"),
    ("sunday", "Sunday"),
)
WEEKDAY_LABELS = tuple(label for _slug, label in WEEKDAYS)
MEALS = (
    ("breakfast", "Breakfast"),
    ("lunch", "Lunch"),
    ("dinner", "Dinner"),
)
MEAL_LABELS = tuple(label for _slug, label in MEALS)
MEAL_PLAN_FILENAME = "meal-plan.json"
LEGACY_MEAL_PLAN_FILENAME = "recipes.txt"
DEFAULT_IMPORT_WEEK_LIMIT = 4
REDIS_MEAL_PLAN_SOURCE = "Redis"
MealPlanRecipe: TypeAlias = RecipeRecord | RecipeReferenceRecord


@dataclass(slots=True)
class MealPlanMatch:
    recipe_id: str
    recipe_title: str
    cookbook_title: str
    score: float


@dataclass(slots=True)
class MealPlanEntry:
    title: str
    raw_text: str
    is_recipe: bool = True
    is_complete: bool | None = None
    day: str | None = None
    external_url: str | None = None
    details: list[str] = field(default_factory=list)
    match: MealPlanMatch | None = None


@dataclass(slots=True)
class MealPlanGroup:
    title: str
    entries: list[MealPlanEntry] = field(default_factory=list)


@dataclass(slots=True)
class MealPlanSection:
    title: str
    groups: list[MealPlanGroup] = field(default_factory=list)


@dataclass(slots=True)
class MealPlanRow:
    id: str
    weekday: str = ""
    meal: str = ""
    title: str = ""
    completed: bool = False
    recipe_id: str | None = None
    recipe: MealPlanRecipe | None = None

    @property
    def recipe_option_value(self) -> str:
        if not self.recipe:
            return ""
        return recipe_option_value(self.recipe)


@dataclass(slots=True)
class ShoppingListRecipeSource:
    recipe_id: str
    recipe_title: str
    use_count: int = 0

    @property
    def label(self) -> str:
        if self.use_count > 1:
            return f"{self.recipe_title} x{self.use_count}"
        return self.recipe_title


@dataclass(slots=True)
class ShoppingListItem:
    name: str
    recipe_sources: list[ShoppingListRecipeSource] = field(default_factory=list)
    total_uses: int = 0

    @property
    def recipe_summary(self) -> str:
        return ", ".join(source.label for source in self.recipe_sources)


@dataclass(slots=True)
class MealPlanWeek:
    id: str
    entries: list[MealPlanRow] = field(default_factory=list)
    start_on: str = ""
    notes: str = ""
    legacy_title: str = ""
    shopping_list: list[ShoppingListItem] = field(default_factory=list)

    @property
    def title(self) -> str:
        if self.start_on:
            return format_week_title(self.start_on)
        return self.legacy_title or "Undated Week"

    @property
    def date_input_value(self) -> str:
        return self.start_on

    @property
    def linked_slot_count(self) -> int:
        return sum(1 for entry in self.entries if entry.recipe_id)

    @property
    def completed_slot_count(self) -> int:
        return sum(1 for entry in self.entries if entry.completed and entry.title)

    @property
    def planned_slot_count(self) -> int:
        return sum(1 for entry in self.entries if entry.title)

    @property
    def shopping_item_count(self) -> int:
        return len(self.shopping_list)


@dataclass(slots=True)
class MealPlanDocument:
    weeks: list[MealPlanWeek]
    source_path: str
    legacy_source_path: str | None = None
    imported_from_legacy: bool = False

    @property
    def slot_count(self) -> int:
        return sum(week.planned_slot_count for week in self.weeks)

    @property
    def linked_slot_count(self) -> int:
        return sum(week.linked_slot_count for week in self.weeks)

    @property
    def completed_slot_count(self) -> int:
        return sum(week.completed_slot_count for week in self.weeks)


@dataclass(slots=True)
class _PreparedRecipe:
    recipe: MealPlanRecipe
    normalized_title: str
    tokens: set[str]


@dataclass(slots=True)
class _ResolvedRecipeMatch:
    recipe: MealPlanRecipe
    score: float


def resolve_meal_plan_path(base_dir: Path) -> Path:
    return base_dir / "data-raw" / MEAL_PLAN_FILENAME


def resolve_legacy_meal_plan_path(base_dir: Path) -> Path | None:
    candidates = (
        base_dir / "data-raw" / LEGACY_MEAL_PLAN_FILENAME,
        base_dir / LEGACY_MEAL_PLAN_FILENAME,
    )
    return next((path for path in candidates if path.exists()), None)


def create_blank_document(base_dir: Path, *, source_path: str | None = None) -> MealPlanDocument:
    return MealPlanDocument(
        weeks=[create_blank_week()],
        source_path=source_path or _display_path(resolve_meal_plan_path(base_dir), base_dir),
    )


def create_blank_week(title: str = "New Week") -> MealPlanWeek:
    start_on = normalize_week_start_value(title)
    legacy_title = ""
    if not start_on:
        cleaned_title = _display_title(title)
        legacy_title = "" if cleaned_title.lower() == "new week" else cleaned_title
    return MealPlanWeek(
        id=f"week-{uuid4().hex[:8]}",
        start_on=start_on,
        entries=[create_blank_row()],
        legacy_title=legacy_title,
    )


def append_blank_week(document: MealPlanDocument) -> None:
    next_start_on = _next_week_start_on(document.weeks)
    week = create_blank_week(next_start_on or "New Week")
    if next_start_on:
        week.start_on = next_start_on
        week.legacy_title = ""
    document.weeks.insert(0, week)


def create_blank_row(*, weekday: str = "", meal: str = "") -> MealPlanRow:
    return MealPlanRow(
        id=f"row-{uuid4().hex[:8]}",
        weekday=normalize_weekday_label(weekday),
        meal=normalize_meal_label(meal),
    )


def append_blank_row(week: MealPlanWeek) -> None:
    week.entries.append(create_blank_row())
    sort_week_entries(week)


def remove_week(document: MealPlanDocument, week_id: str) -> None:
    document.weeks = [week for week in document.weeks if week.id != week_id]
    if not document.weeks:
        document.weeks.append(create_blank_week())


def load_or_import_meal_plan(
    base_dir: Path,
    recipes: list[MealPlanRecipe],
    *,
    week_limit: int = DEFAULT_IMPORT_WEEK_LIMIT,
    source_path: str | None = None,
    load_payload: Callable[[], str | None] | None = None,
    save_payload: Callable[[str], None] | None = None,
) -> MealPlanDocument:
    resolved_source_path = source_path or _display_path(resolve_meal_plan_path(base_dir), base_dir)
    if load_payload:
        payload = load_payload()
        if payload:
            document = meal_plan_from_dict(json.loads(payload), base_dir, source_path=resolved_source_path)
            hydrate_linked_recipes(document, recipes)
            return document

    plan_path = resolve_meal_plan_path(base_dir)
    if plan_path.exists():
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
        document = meal_plan_from_dict(payload, base_dir, source_path=resolved_source_path)
        hydrate_linked_recipes(document, recipes)
        if save_payload:
            save_payload(meal_plan_json(document))
        return document

    legacy_path = resolve_legacy_meal_plan_path(base_dir)
    if legacy_path:
        document = import_recent_weeks_from_text(
            legacy_path.read_text(encoding="utf-8"),
            recipes,
            base_dir=base_dir,
            source_path=resolved_source_path,
            legacy_source_path=_display_path(legacy_path, base_dir),
            week_limit=week_limit,
        )
        save_meal_plan(base_dir, document, save_payload=save_payload)
        return document

    document = create_blank_document(base_dir, source_path=resolved_source_path)
    hydrate_linked_recipes(document, recipes)
    return document


def save_meal_plan(
    base_dir: Path,
    document: MealPlanDocument,
    *,
    save_payload: Callable[[str], None] | None = None,
) -> None:
    payload = meal_plan_json(document)
    if save_payload:
        save_payload(payload)
        return
    path = resolve_meal_plan_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def meal_plan_json(document: MealPlanDocument) -> str:
    return json.dumps(meal_plan_to_dict(document), indent=2)


def meal_plan_to_dict(document: MealPlanDocument) -> dict[str, object]:
    return {
        "version": 2,
        "weeks": [
            {
                "id": week.id,
                "title": week.title if week.start_on else week.legacy_title,
                "start_on": week.start_on,
                "legacy_title": week.legacy_title,
                "notes": week.notes,
                "entries": [
                    {
                        "id": entry.id,
                        "weekday": entry.weekday,
                        "meal": entry.meal,
                        "title": entry.title,
                        "completed": entry.completed,
                        "recipe_id": entry.recipe_id,
                    }
                    for entry in week.entries
                ],
            }
            for week in document.weeks
        ],
    }


def meal_plan_from_dict(
    payload: dict[str, object],
    base_dir: Path,
    *,
    source_path: str | None = None,
) -> MealPlanDocument:
    weeks_payload = payload.get("weeks")
    weeks: list[MealPlanWeek] = []
    if isinstance(weeks_payload, list):
        for item in weeks_payload:
            if isinstance(item, dict):
                weeks.append(_week_from_dict(item))

    if not weeks:
        weeks = [create_blank_week()]

    return MealPlanDocument(
        weeks=weeks,
        source_path=source_path or _display_path(resolve_meal_plan_path(base_dir), base_dir),
    )


def parse_meal_plan_form(
    form: object,
    recipes: list[MealPlanRecipe],
    *,
    base_dir: Path,
    source_path: str | None = None,
) -> MealPlanDocument:
    recipe_map = {recipe.id: recipe for recipe in recipes}
    prepared = _prepare_recipes(recipes)
    exact_index: dict[str, list[_PreparedRecipe]] = {}
    for item in prepared:
        exact_index.setdefault(item.normalized_title, []).append(item)
    week_ids = list(form.getlist("week_id")) if hasattr(form, "getlist") else []
    document = MealPlanDocument(
        weeks=[],
        source_path=source_path or _display_path(resolve_meal_plan_path(base_dir), base_dir),
    )

    for raw_week_id in week_ids:
        week_id = str(raw_week_id).strip()
        if not week_id:
            continue
        week_start_on = normalize_week_start_value(str(form.get(f"week_start_on__{week_id}", "")).strip())
        week_title = str(form.get(f"week_title__{week_id}", "")).strip()
        week = create_blank_week(title=week_start_on or week_title or "Untitled Week")
        week.id = week_id
        week.start_on = week_start_on
        week.legacy_title = _display_title(week_title) if week_title and not week_start_on else ""
        week.notes = str(form.get(f"week_notes__{week_id}", "")).strip()
        week.entries = []
        row_ids = list(form.getlist(f"week_entry_id__{week_id}")) if hasattr(form, "getlist") else []
        for raw_row_id in row_ids:
            row_id = str(raw_row_id).strip()
            if not row_id:
                continue
            prefix = f"entry__{week_id}__{row_id}"
            entry = create_blank_row(
                weekday=str(form.get(f"{prefix}__weekday", "")).strip(),
                meal=str(form.get(f"{prefix}__meal", "")).strip(),
            )
            entry.id = row_id
            entry.title = str(form.get(f"{prefix}__title", "")).strip()
            entry.completed = form.get(f"{prefix}__completed") is not None

            recipe_ref = str(form.get(f"{prefix}__recipe_ref", "")).strip() or entry.title
            recipe_id = str(form.get(f"{prefix}__recipe_id", "")).strip()
            recipe = resolve_recipe_reference(
                recipe_ref,
                recipe_map,
                recipes,
                fallback_title=entry.title,
                recipe_id=recipe_id,
                prepared=prepared,
                exact_index=exact_index,
            )
            if recipe:
                entry.recipe_id = recipe.id
                entry.recipe = recipe
                if not entry.title or entry.title == recipe_option_value(recipe):
                    entry.title = recipe.title
            week.entries.append(entry)

        if not week.entries:
            week.entries.append(create_blank_row())
        sort_week_entries(week)
        document.weeks.append(week)

    if not document.weeks:
        document.weeks.append(create_blank_week())

    return document


def resolve_recipe_reference(
    value: str,
    recipe_map: dict[str, MealPlanRecipe],
    recipes: list[MealPlanRecipe],
    *,
    fallback_title: str = "",
    recipe_id: str = "",
    prepared: list[_PreparedRecipe] | None = None,
    exact_index: dict[str, list[_PreparedRecipe]] | None = None,
) -> MealPlanRecipe | None:
    explicit_recipe_id = recipe_id.strip()
    if explicit_recipe_id:
        recipe = recipe_map.get(explicit_recipe_id)
        if recipe:
            return recipe

    candidate = value.strip()
    if candidate:
        ref_match = RECIPE_REF_RE.search(candidate)
        if ref_match:
            recipe = recipe_map.get(ref_match.group(1))
            if recipe:
                return recipe

    title = fallback_title.strip() or candidate
    if not title:
        return None

    prepared_recipes = prepared if prepared is not None else _prepare_recipes(recipes)
    exact_matches = exact_index
    if exact_matches is None:
        exact_matches = {}
        for item in prepared_recipes:
            exact_matches.setdefault(item.normalized_title, []).append(item)
    match = _match_title_to_recipe(title, prepared_recipes, exact_matches)
    return match.recipe if match else None


def recipe_option_value(recipe: MealPlanRecipe) -> str:
    return f"{recipe.title} - {recipe.cookbook_title} [{recipe.id}]"


def hydrate_linked_recipes(document: MealPlanDocument, recipes: list[MealPlanRecipe]) -> None:
    recipe_map = {recipe.id: recipe for recipe in recipes}
    for week in document.weeks:
        for entry in week.entries:
            entry.recipe = recipe_map.get(entry.recipe_id or "")
            if entry.recipe is None:
                entry.recipe_id = None


def build_week_shopping_list(
    week: MealPlanWeek,
    recipes: list[RecipeRecord],
) -> list[ShoppingListItem]:
    recipe_map = {recipe.id: recipe for recipe in recipes}
    aggregated: dict[str, ShoppingListItem] = {}
    source_maps: dict[str, dict[str, ShoppingListRecipeSource]] = defaultdict(dict)
    label_variants: dict[str, set[str]] = defaultdict(set)

    for entry in week.entries:
        recipe_id = entry.recipe_id or ""
        if not recipe_id:
            continue
        recipe = recipe_map.get(recipe_id)
        if recipe is None:
            continue

        for ingredient_key, ingredient_label in _shopping_ingredients(recipe):
            item = aggregated.setdefault(ingredient_key, ShoppingListItem(name=ingredient_key))
            item.total_uses += 1
            label_variants[ingredient_key].add(ingredient_label)
            source = source_maps[ingredient_key].get(recipe.id)
            if source is None:
                source = ShoppingListRecipeSource(recipe_id=recipe.id, recipe_title=recipe.title)
                source_maps[ingredient_key][recipe.id] = source
                item.recipe_sources.append(source)
            source.use_count += 1

    for item in aggregated.values():
        variants = {label for label in label_variants.get(item.name, set()) if label}
        if len(variants) == 1:
            item.name = next(iter(variants))
        item.recipe_sources.sort(key=lambda source: source.recipe_title.casefold())

    return sorted(aggregated.values(), key=lambda item: (item.name.casefold(), item.recipe_summary.casefold()))


def populate_week_shopping_lists(
    document: MealPlanDocument,
    recipes: list[RecipeRecord],
) -> None:
    for week in document.weeks:
        week.shopping_list = build_week_shopping_list(week, recipes)


def import_recent_weeks_from_text(
    text: str,
    recipes: list[MealPlanRecipe],
    *,
    base_dir: Path,
    source_path: str,
    legacy_source_path: str,
    week_limit: int = DEFAULT_IMPORT_WEEK_LIMIT,
) -> MealPlanDocument:
    sections = parse_meal_plan_text(text)
    recent_sections = sections[:week_limit]
    attach_recipe_matches(recent_sections, recipes)
    weeks = [_section_to_week(section) for section in recent_sections]
    if not weeks:
        weeks = [create_blank_week()]
    document = MealPlanDocument(
        weeks=weeks,
        source_path=source_path,
        legacy_source_path=legacy_source_path,
        imported_from_legacy=True,
    )
    hydrate_linked_recipes(document, recipes)
    return document


def parse_meal_plan_text(text: str) -> list[MealPlanSection]:
    sections: list[MealPlanSection] = [MealPlanSection(title="Imported Plan")]
    current_section = sections[0]
    current_group = _ensure_group(current_section, "Meals")
    pending_group_title: str | None = None
    last_entry: MealPlanEntry | None = None
    lines = text.splitlines()

    for index, line in enumerate(lines):
        raw_line = line.rstrip()
        stripped = raw_line.strip()
        if not stripped:
            last_entry = None
            continue

        if URL_RE.fullmatch(stripped):
            current_group.entries.append(
                MealPlanEntry(
                    title=_external_link_label(stripped),
                    raw_text=stripped,
                    is_recipe=False,
                    external_url=stripped,
                )
            )
            last_entry = None
            continue

        schedule = _parse_schedule_row(stripped)
        if schedule:
            lunch_group = _ensure_group(current_section, "Lunches")
            dinner_group = _ensure_group(current_section, "Dinners")
            if schedule["lunch"]:
                lunch_group.entries.append(_build_entry(schedule["lunch"], day=schedule["day"]))
            if schedule["dinner"]:
                dinner_group.entries.append(_build_entry(schedule["dinner"], day=schedule["day"]))
            last_entry = None
            continue

        if _is_section_heading(stripped):
            current_section = MealPlanSection(title=_trim_heading(stripped))
            sections.append(current_section)
            current_group = _ensure_group(current_section, pending_group_title or "Meals")
            pending_group_title = None
            last_entry = None
            continue

        if _is_group_heading(stripped):
            title = _trim_heading(stripped)
            if (
                len(current_section.groups) == 1
                and not current_section.groups[0].entries
                and current_section.title == "Imported Plan"
            ):
                pending_group_title = title
            current_group = _ensure_group(current_section, title)
            last_entry = None
            continue

        if last_entry and _is_detail_line(raw_line):
            detail = _clean_entry_text(BULLET_RE.sub(lambda match: match.group("body"), stripped))
            if detail:
                last_entry.details.append(detail)
            continue

        if _looks_like_standalone_entry(stripped, lines, index):
            entry = _build_entry(stripped)
            current_group.entries.append(entry)
            last_entry = entry
            continue

        parsed = _parse_bulleted_entry(stripped)
        if parsed:
            current_group.entries.append(parsed)
            last_entry = parsed
            continue

        current_group.entries.append(
            MealPlanEntry(
                title=_clean_entry_text(stripped),
                raw_text=stripped,
                is_recipe=False,
            )
        )
        last_entry = current_group.entries[-1]

    return [section for section in sections if any(group.entries for group in section.groups)]


def attach_recipe_matches(sections: list[MealPlanSection], recipes: list[MealPlanRecipe]) -> None:
    prepared = _prepare_recipes(recipes)
    exact_index: dict[str, list[_PreparedRecipe]] = {}
    for item in prepared:
        exact_index.setdefault(item.normalized_title, []).append(item)

    for section in sections:
        for group in section.groups:
            for entry in group.entries:
                if not entry.is_recipe:
                    continue
                match = _match_title_to_recipe(entry.title, prepared, exact_index)
                if match:
                    entry.match = MealPlanMatch(
                        recipe_id=match.recipe.id,
                        recipe_title=match.recipe.title,
                        cookbook_title=match.recipe.cookbook_title,
                        score=match.score,
                    )


def _section_to_week(section: MealPlanSection) -> MealPlanWeek:
    week = create_blank_week(title=section.title)
    week.entries = []
    week_notes: list[str] = []

    for group in section.groups:
        default_meal = _group_to_meal(group.title)
        for entry in group.entries:
            note = _import_entry_into_week(week, entry, default_meal)
            if note:
                week_notes.append(note)

    if not week.entries:
        week.entries.append(create_blank_row())
    week.notes = "\n".join(line for line in week_notes if line).strip()
    sort_week_entries(week)
    return week


def _import_entry_into_week(
    week: MealPlanWeek,
    entry: MealPlanEntry,
    default_meal: str,
) -> str:
    title = _slot_title_from_entry(entry).strip()
    if not title and entry.external_url:
        return f"Link: {entry.external_url}"
    if not title:
        return ""

    weekday = _infer_day_label(entry)
    meal = _infer_meal_label(entry, default_meal)
    row = create_blank_row(weekday=weekday, meal=meal)
    row.title = title
    row.completed = bool(entry.is_complete)
    row.recipe_id = entry.match.recipe_id if entry.match else None
    week.entries.append(row)
    if entry.details or entry.external_url:
        prefix = "Notes:"
        if weekday or meal:
            prefix = f"{weekday or 'Any day'} {meal or 'Meal'} notes:"
        return _entry_note_line(entry, prefix=prefix, include_title=False)
    return ""


def _entry_note_line(entry: MealPlanEntry, *, prefix: str, include_title: bool = True) -> str:
    parts: list[str] = []
    if include_title and entry.title:
        parts.append(entry.title)
    parts.extend(detail for detail in entry.details if detail)
    if entry.external_url:
        parts.append(entry.external_url)
    if not parts:
        return ""
    return f"{prefix} {' | '.join(parts)}".strip()


def _group_to_meal(title: str) -> str:
    lowered = title.strip().lower()
    if "breakfast" in lowered:
        return "Breakfast"
    if "lunch" in lowered:
        return "Lunch"
    return "Dinner"


def _infer_day_label(entry: MealPlanEntry) -> str:
    raw_candidates = [entry.day or "", entry.raw_text, entry.title]
    for candidate in raw_candidates:
        match = WEEKDAY_RE.search(candidate)
        if match:
            return normalize_weekday_label(match.group(1))
    return ""


def _infer_meal_label(entry: MealPlanEntry, default_meal: str) -> str:
    combined = " ".join(part for part in (entry.raw_text, entry.title) if part).lower()
    if "breakfast" in combined:
        return "Breakfast"
    if "lunch" in combined:
        return "Lunch"
    if "night" in combined or "dinner" in combined:
        return "Dinner"
    return normalize_meal_label(default_meal)


def _week_from_dict(payload: dict[str, object]) -> MealPlanWeek:
    start_on = normalize_week_start_value(str(payload.get("start_on", "")).strip())
    raw_title = str(payload.get("title", "")).strip()
    if raw_title in {"Undated Week", "New Week"}:
        raw_title = ""
    legacy_title = str(payload.get("legacy_title", "")).strip()
    inferred_legacy_title = legacy_title or (raw_title if raw_title and not start_on else "")
    week = create_blank_week(title=start_on or inferred_legacy_title or "Untitled Week")
    week.id = str(payload.get("id", "")).strip() or week.id
    week.start_on = start_on
    week.legacy_title = _display_title(inferred_legacy_title) if inferred_legacy_title and not start_on else ""
    week.notes = str(payload.get("notes", "")).strip()
    week.entries = []

    entries_payload = payload.get("entries")
    if isinstance(entries_payload, list):
        for item in entries_payload:
            if not isinstance(item, dict):
                continue
            entry = create_blank_row(
                weekday=str(item.get("weekday", "")).strip(),
                meal=str(item.get("meal", "")).strip(),
            )
            entry.id = str(item.get("id", "")).strip() or entry.id
            entry.title = str(item.get("title", "")).strip()
            entry.completed = bool(item.get("completed", False))
            recipe_id = str(item.get("recipe_id", "")).strip()
            entry.recipe_id = recipe_id or None
            week.entries.append(entry)

    # Backward compatibility for older structured plans that used fixed days/meals.
    days_payload = payload.get("days")
    if not week.entries and isinstance(days_payload, list):
        for day_item in days_payload:
            if not isinstance(day_item, dict):
                continue
            day_label = normalize_weekday_label(str(day_item.get("label", "")).strip() or str(day_item.get("slug", "")).strip())
            meals_payload = day_item.get("meals")
            if not isinstance(meals_payload, list):
                continue
            for meal_item in meals_payload:
                if not isinstance(meal_item, dict):
                    continue
                entry = create_blank_row(
                    weekday=day_label,
                    meal=str(meal_item.get("meal_label", "")).strip() or str(meal_item.get("meal_slug", "")).strip(),
                )
                entry.title = str(meal_item.get("title", "")).strip()
                entry.completed = bool(meal_item.get("completed", False))
                recipe_id = str(meal_item.get("recipe_id", "")).strip()
                entry.recipe_id = recipe_id or None
                if entry.title or entry.recipe_id or entry.weekday or entry.meal:
                    week.entries.append(entry)
    if not week.entries:
        week.entries.append(create_blank_row())
    sort_week_entries(week)
    return week


def sort_week_entries(week: MealPlanWeek) -> None:
    indexed_entries = list(enumerate(week.entries))
    indexed_entries.sort(
        key=lambda item: _week_entry_sort_key(week, item[1], item[0]),
    )
    week.entries = [entry for _index, entry in indexed_entries]


def _week_entry_sort_key(
    week: MealPlanWeek,
    entry: MealPlanRow,
    original_index: int,
) -> tuple[int, int, int]:
    return (
        _weekday_sort_offset(entry.weekday, week.start_on),
        _meal_sort_order(entry.meal),
        original_index,
    )


def _weekday_sort_offset(weekday: str, week_start_on: str) -> int:
    normalized_weekday = normalize_weekday_label(weekday)
    if not normalized_weekday:
        return len(WEEKDAYS) + 1

    weekday_positions = {label: index for index, (_slug, label) in enumerate(WEEKDAYS)}
    weekday_position = weekday_positions.get(normalized_weekday)
    if weekday_position is None:
        return len(WEEKDAYS) + 1

    start_weekday_position = 6
    if week_start_on:
        try:
            start_weekday_position = date.fromisoformat(week_start_on).weekday()
        except ValueError:
            start_weekday_position = 6

    return (weekday_position - start_weekday_position) % len(WEEKDAYS)


def _meal_sort_order(meal: str) -> int:
    normalized_meal = normalize_meal_label(meal)
    if not normalized_meal:
        return len(MEALS) + 1

    meal_positions = {label: index for index, (_slug, label) in enumerate(MEALS)}
    return meal_positions.get(normalized_meal, len(MEALS) + 1)


def _parse_bulleted_entry(text: str) -> MealPlanEntry | None:
    checkbox = CHECKBOX_RE.match(text)
    if checkbox:
        return _build_entry(
            checkbox.group("body"),
            is_complete=checkbox.group("state").lower() == "x",
        )
    bullet = BULLET_RE.match(text)
    if bullet:
        return _build_entry(bullet.group("body"))
    return None


def _build_entry(
    text: str,
    *,
    day: str | None = None,
    is_complete: bool | None = None,
) -> MealPlanEntry:
    cleaned = _clean_entry_text(text)
    title = cleaned
    external_url = None
    urls = URL_RE.findall(cleaned)
    if urls:
        external_url = urls[0]
        title = _clean_entry_text(URL_RE.sub("", cleaned))
        if not title:
            title = _external_link_label(external_url)

    if " like " in title.lower():
        title = re.split(r"\slike\s", title, maxsplit=1, flags=re.IGNORECASE)[0]
        title = _clean_entry_text(title)

    recipe_title = _display_title(title)
    is_recipe = _looks_like_recipe_entry(recipe_title)
    return MealPlanEntry(
        title=recipe_title,
        raw_text=text,
        is_recipe=is_recipe,
        is_complete=is_complete,
        day=day,
        external_url=external_url,
    )


def _prepare_recipes(recipes: list[MealPlanRecipe]) -> list[_PreparedRecipe]:
    prepared: list[_PreparedRecipe] = []
    for recipe in recipes:
        normalized_title = normalize_meal_text(recipe.title)
        if not normalized_title:
            continue
        prepared.append(
            _PreparedRecipe(
                recipe=recipe,
                normalized_title=normalized_title,
                tokens=set(_tokenize(recipe.title)),
            )
        )
    return prepared


def _match_title_to_recipe(
    title: str,
    prepared: list[_PreparedRecipe],
    exact_index: dict[str, list[_PreparedRecipe]],
) -> _ResolvedRecipeMatch | None:
    variants = [variant for variant in _title_variants(title) if variant]
    if not variants:
        return None

    for variant in variants:
        exact_matches = exact_index.get(variant)
        if exact_matches:
            candidate = exact_matches[0]
            return _ResolvedRecipeMatch(
                recipe=candidate.recipe,
                score=1.0,
            )

    scored: list[tuple[float, _PreparedRecipe]] = []
    for recipe in prepared:
        best_score = 0.0
        for variant in variants:
            score = _similarity_score(variant, recipe)
            if score > best_score:
                best_score = score
        if best_score >= 0.74:
            scored.append((best_score, recipe))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_recipe = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if best_score < 0.81:
        return None
    if second_score and best_score - second_score < 0.035:
        return None

    return _ResolvedRecipeMatch(
        recipe=best_recipe.recipe,
        score=round(best_score, 4),
    )


def _similarity_score(variant: str, recipe: _PreparedRecipe) -> float:
    if variant == recipe.normalized_title:
        return 1.0

    variant_tokens = set(_tokenize(variant))
    if not variant_tokens:
        return 0.0

    shared = len(variant_tokens & recipe.tokens)
    overlap = shared / max(len(variant_tokens), len(recipe.tokens), 1)
    coverage = shared / max(len(variant_tokens), 1)
    ratio = SequenceMatcher(None, variant, recipe.normalized_title).ratio()
    score = (ratio * 0.46) + (overlap * 0.22) + (coverage * 0.32)

    if variant in recipe.normalized_title or recipe.normalized_title in variant:
        score += 0.08
    if variant_tokens <= recipe.tokens:
        score += 0.08
    if len(variant_tokens) == 1 and ratio < 0.7:
        score -= 0.12
    return score


def _title_variants(title: str) -> set[str]:
    base = _display_title(title)
    variants = {normalize_meal_text(base)}
    variants.add(normalize_meal_text(re.sub(r"\([^)]*\)", "", base)))
    variants.add(normalize_meal_text(re.sub(r"^(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+", "", base, flags=re.IGNORECASE)))

    match = re.match(r"^[^-:]+[-:]\s*(.+)$", base)
    if match and WEEKDAY_RE.match(base):
        variants.add(normalize_meal_text(match.group(1)))

    for parenthetical in re.findall(r"\(([^)]*)\)", base):
        if _parenthetical_looks_like_title(parenthetical):
            variants.add(normalize_meal_text(parenthetical))

    for prefix in ("breakfast", "lunch", "lunches", "dinner", "dinners"):
        if base.lower().startswith(f"{prefix} "):
            variants.add(normalize_meal_text(base[len(prefix):]))

    return {variant for variant in variants if variant}


def normalize_meal_text(text: str) -> str:
    normalized = URL_RE.sub(" ", text.lower())
    normalized = normalized.replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    tokens = [
        _normalize_token(token)
        for token in normalized.split()
        if token and token not in TOKEN_STOPWORDS
    ]
    return " ".join(token for token in tokens if token)


def _normalize_token(token: str) -> str:
    token = TOKEN_ALIASES.get(token, token)
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 4 and token.endswith("s") and not token.endswith(("ss", "us")):
        return token[:-1]
    return token


def _tokenize(text: str) -> list[str]:
    return normalize_meal_text(text).split()


def _is_section_heading(text: str) -> bool:
    if DATE_SECTION_RE.match(text) or WEEK_SECTION_RE.match(text):
        return True
    return text.lower().startswith("week ")


def _is_group_heading(text: str) -> bool:
    trimmed = _trim_heading(text).lower()
    return trimmed in MEAL_GROUP_TITLES or trimmed == "dinner recipes"


def _looks_like_standalone_entry(text: str, lines: list[str], index: int) -> bool:
    if _is_group_heading(text) or _is_section_heading(text):
        return False
    next_index = index + 1
    while next_index < len(lines):
        candidate = lines[next_index].strip()
        if not candidate:
            return False
        return _is_detail_line(lines[next_index])
    return False


def _looks_like_recipe_entry(text: str) -> bool:
    normalized = normalize_meal_text(text)
    if not normalized:
        return False
    if normalized in NON_RECIPE_HINTS:
        return False
    if normalized.endswith(" out") or normalized.startswith("out "):
        return False
    if normalized.endswith(" leftover") or normalized.endswith(" leftovers"):
        return False
    return not normalized.startswith("day lunch dinner")


def _parse_schedule_row(text: str) -> dict[str, str] | None:
    parts = [part.strip() for part in SCHEDULE_SPLIT_RE.split(text) if part.strip()]
    if len(parts) < 3:
        return None
    if parts[0].lower() == "day":
        return None
    if not WEEKDAY_RE.match(parts[0]):
        return None
    return {
        "day": parts[0],
        "lunch": parts[1] if len(parts) > 1 else "",
        "dinner": parts[2] if len(parts) > 2 else "",
    }


def _is_detail_line(text: str) -> bool:
    return text.strip().startswith("•")


def _parenthetical_looks_like_title(text: str) -> bool:
    candidate = normalize_meal_text(text)
    if not candidate:
        return False
    if WEEKDAY_RE.match(text):
        return False
    return not any(
        part in candidate
        for part in (
            "luke out",
            "tally out",
            "friday",
            "saturday",
            "sunday",
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
        )
    )


def _display_title(text: str) -> str:
    cleaned = text.strip(" -:\t")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = cleaned[1:-1].strip()
    return re.sub(r"\s+", " ", cleaned).strip()


def _trim_heading(text: str) -> str:
    return text.strip().rstrip(":").strip()


def _clean_entry_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" -:\t")


def _ensure_group(section: MealPlanSection, title: str) -> MealPlanGroup:
    for group in section.groups:
        if group.title.lower() == title.lower():
            return group
    group = MealPlanGroup(title=title)
    section.groups.append(group)
    return group


def _external_link_label(url: str) -> str:
    label = re.sub(r"^https?://", "", url).rstrip("/")
    return label if len(label) <= 64 else f"{label[:61]}..."


def _display_path(path: Path, base_dir: Path) -> str:
    try:
        return str(path.relative_to(base_dir))
    except ValueError:
        return str(path)


def _slot_title_from_entry(entry: MealPlanEntry) -> str:
    title = _display_title(entry.title)
    title = re.sub(r"^(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s*[-:]?\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\((monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?:[^)]*)\)", "", title, flags=re.IGNORECASE)
    return _display_title(title)


def normalize_weekday_label(value: str) -> str:
    cleaned = _display_title(value)
    if not cleaned:
        return ""
    for _slug, label in WEEKDAYS:
        if cleaned.lower() == label.lower() or cleaned.lower() == _slug:
            return label
    return cleaned


def normalize_meal_label(value: str) -> str:
    cleaned = _display_title(value)
    if not cleaned:
        return ""
    for _slug, label in MEALS:
        if cleaned.lower() == label.lower() or cleaned.lower() == _slug:
            return label
    return cleaned


def normalize_week_start_value(value: str) -> str:
    cleaned = _display_title(value)
    if not cleaned:
        return ""

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        try:
            return date.fromisoformat(cleaned).isoformat()
        except ValueError:
            return ""

    text = re.sub(r"^week(?:\s+\d+)?(?:\s+of)?\s+", "", cleaned, flags=re.IGNORECASE).strip()
    if not text:
        return ""

    for fmt in ("%d %b %Y", "%d %B %Y", "%d %b %y", "%d %B %y", "%d %b", "%d %B"):
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        if "%Y" not in fmt and "%y" not in fmt:
            parsed = parsed.replace(year=date.today().year)
        return parsed.isoformat()

    return ""


def format_week_title(value: str) -> str:
    normalized = normalize_week_start_value(value)
    if not normalized:
        return _display_title(value)
    parsed = date.fromisoformat(normalized)
    return f"Week of {parsed.day} {parsed.strftime('%B %Y')}"


def _next_week_start_on(weeks: list[MealPlanWeek]) -> str:
    for week in weeks:
        if not week.start_on:
            continue
        parsed = date.fromisoformat(week.start_on)
        return _week_start_sunday(parsed + timedelta(days=7)).isoformat()
    return ""


def _week_start_sunday(value: date) -> date:
    return value - timedelta(days=(value.weekday() + 1) % 7)


def _shopping_ingredients(recipe: RecipeRecord) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for ingredient in recipe.ingredients:
        ingredient_key = ingredient_index_name(ingredient)
        if not ingredient_key:
            continue
        items.append((ingredient_key, _shopping_ingredient_label(ingredient, ingredient_key)))

    if items:
        return items

    return [
        (name.strip().casefold(), name.strip())
        for name in recipe.ingredient_names
        if name.strip()
    ]


def _shopping_ingredient_label(ingredient: RecipeRecord | object, ingredient_key: str) -> str:
    raw_value = str(getattr(ingredient, "raw", "") or "")
    if isinstance(ingredient, dict):
        raw_value = str(ingredient.get("raw", "") or "")

    raw = raw_value.strip()
    if not raw:
        return ingredient_key

    normalized_raw = re.sub(r"\s+", " ", raw.casefold())
    normalized_key = re.sub(r"\s+", " ", ingredient_key.casefold())
    if normalized_raw == normalized_key:
        return ingredient_key

    if re.search(r"\d+\.\d+", raw) or "," in raw or re.search(r"\bor\b", raw, re.IGNORECASE) or "/" in raw:
        return raw

    return ingredient_key
