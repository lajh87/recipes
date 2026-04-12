from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
from uuid import uuid4

from app.models import RecipeRecord

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
    recipe: RecipeRecord | None = None

    @property
    def recipe_option_value(self) -> str:
        if not self.recipe:
            return ""
        return recipe_option_value(self.recipe)


@dataclass(slots=True)
class MealPlanWeek:
    id: str
    title: str
    entries: list[MealPlanRow]
    notes: str = ""

    @property
    def linked_slot_count(self) -> int:
        return sum(1 for entry in self.entries if entry.recipe_id)

    @property
    def completed_slot_count(self) -> int:
        return sum(1 for entry in self.entries if entry.completed and entry.title)

    @property
    def planned_slot_count(self) -> int:
        return sum(1 for entry in self.entries if entry.title)


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
    recipe: RecipeRecord
    normalized_title: str
    tokens: set[str]


@dataclass(slots=True)
class _ResolvedRecipeMatch:
    recipe: RecipeRecord
    score: float


def resolve_meal_plan_path(base_dir: Path) -> Path:
    return base_dir / "data-raw" / MEAL_PLAN_FILENAME


def resolve_legacy_meal_plan_path(base_dir: Path) -> Path | None:
    candidates = (
        base_dir / "data-raw" / LEGACY_MEAL_PLAN_FILENAME,
        base_dir / LEGACY_MEAL_PLAN_FILENAME,
    )
    return next((path for path in candidates if path.exists()), None)


def create_blank_document(base_dir: Path) -> MealPlanDocument:
    return MealPlanDocument(
        weeks=[create_blank_week()],
        source_path=_display_path(resolve_meal_plan_path(base_dir), base_dir),
    )


def create_blank_week(title: str = "New Week") -> MealPlanWeek:
    return MealPlanWeek(
        id=f"week-{uuid4().hex[:8]}",
        title=title,
        entries=[create_blank_row()],
    )


def append_blank_week(document: MealPlanDocument) -> None:
    document.weeks.append(create_blank_week(title=f"Week {len(document.weeks) + 1}"))


def create_blank_row(*, weekday: str = "", meal: str = "") -> MealPlanRow:
    return MealPlanRow(
        id=f"row-{uuid4().hex[:8]}",
        weekday=normalize_weekday_label(weekday),
        meal=normalize_meal_label(meal),
    )


def append_blank_row(week: MealPlanWeek) -> None:
    week.entries.append(create_blank_row())


def remove_week(document: MealPlanDocument, week_id: str) -> None:
    document.weeks = [week for week in document.weeks if week.id != week_id]
    if not document.weeks:
        document.weeks.append(create_blank_week())


def load_or_import_meal_plan(
    base_dir: Path,
    recipes: list[RecipeRecord],
    *,
    week_limit: int = DEFAULT_IMPORT_WEEK_LIMIT,
) -> MealPlanDocument:
    plan_path = resolve_meal_plan_path(base_dir)
    if plan_path.exists():
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
        document = meal_plan_from_dict(payload, base_dir)
        hydrate_linked_recipes(document, recipes)
        return document

    legacy_path = resolve_legacy_meal_plan_path(base_dir)
    if legacy_path:
        document = import_recent_weeks_from_text(
            legacy_path.read_text(encoding="utf-8"),
            recipes,
            base_dir=base_dir,
            source_path=_display_path(plan_path, base_dir),
            legacy_source_path=_display_path(legacy_path, base_dir),
            week_limit=week_limit,
        )
        save_meal_plan(base_dir, document)
        return document

    document = create_blank_document(base_dir)
    hydrate_linked_recipes(document, recipes)
    return document


def save_meal_plan(base_dir: Path, document: MealPlanDocument) -> None:
    path = resolve_meal_plan_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meal_plan_to_dict(document), indent=2), encoding="utf-8")


def meal_plan_to_dict(document: MealPlanDocument) -> dict[str, object]:
    return {
        "version": 1,
        "weeks": [
            {
                "id": week.id,
                "title": week.title,
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


def meal_plan_from_dict(payload: dict[str, object], base_dir: Path) -> MealPlanDocument:
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
        source_path=_display_path(resolve_meal_plan_path(base_dir), base_dir),
    )


def parse_meal_plan_form(
    form: object,
    recipes: list[RecipeRecord],
    *,
    base_dir: Path,
) -> MealPlanDocument:
    recipe_map = {recipe.id: recipe for recipe in recipes}
    week_ids = list(form.getlist("week_id")) if hasattr(form, "getlist") else []
    document = MealPlanDocument(
        weeks=[],
        source_path=_display_path(resolve_meal_plan_path(base_dir), base_dir),
    )

    for raw_week_id in week_ids:
        week_id = str(raw_week_id).strip()
        if not week_id:
            continue
        week = create_blank_week(title=str(form.get(f"week_title__{week_id}", "")).strip() or "Untitled Week")
        week.id = week_id
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

            recipe_ref = str(form.get(f"{prefix}__recipe_ref", "")).strip()
            recipe = resolve_recipe_reference(recipe_ref, recipe_map, recipes, fallback_title=entry.title)
            if recipe:
                entry.recipe_id = recipe.id
                entry.recipe = recipe
            week.entries.append(entry)

        if not week.entries:
            week.entries.append(create_blank_row())
        document.weeks.append(week)

    if not document.weeks:
        document.weeks.append(create_blank_week())

    return document


def resolve_recipe_reference(
    value: str,
    recipe_map: dict[str, RecipeRecord],
    recipes: list[RecipeRecord],
    *,
    fallback_title: str = "",
) -> RecipeRecord | None:
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

    prepared = _prepare_recipes(recipes)
    exact_index: dict[str, list[_PreparedRecipe]] = {}
    for item in prepared:
        exact_index.setdefault(item.normalized_title, []).append(item)
    match = _match_title_to_recipe(title, prepared, exact_index)
    return match.recipe if match else None


def recipe_option_value(recipe: RecipeRecord) -> str:
    return f"{recipe.title} - {recipe.cookbook_title} [{recipe.id}]"


def hydrate_linked_recipes(document: MealPlanDocument, recipes: list[RecipeRecord]) -> None:
    recipe_map = {recipe.id: recipe for recipe in recipes}
    for week in document.weeks:
        for entry in week.entries:
            entry.recipe = recipe_map.get(entry.recipe_id or "")
            if entry.recipe is None:
                entry.recipe_id = None


def import_recent_weeks_from_text(
    text: str,
    recipes: list[RecipeRecord],
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


def attach_recipe_matches(sections: list[MealPlanSection], recipes: list[RecipeRecord]) -> None:
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
    week = create_blank_week(title=str(payload.get("title", "")).strip() or "Untitled Week")
    week.id = str(payload.get("id", "")).strip() or week.id
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
    return week


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


def _prepare_recipes(recipes: list[RecipeRecord]) -> list[_PreparedRecipe]:
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
