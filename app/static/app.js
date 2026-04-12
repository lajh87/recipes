const lightbox = document.getElementById("lightbox");
const lightboxImage = lightbox?.querySelector(".lightbox__image");

function closeLightbox() {
  if (!lightbox || !lightboxImage) {
    return;
  }
  lightbox.hidden = true;
  lightboxImage.src = "";
  lightboxImage.alt = "";
  document.body.classList.remove("is-lightbox-open");
}

function openLightbox(src, alt) {
  if (!lightbox || !lightboxImage || !src) {
    return;
  }
  lightboxImage.src = src;
  lightboxImage.alt = alt || "";
  lightbox.hidden = false;
  document.body.classList.add("is-lightbox-open");
}

document.addEventListener("click", (event) => {
  const trigger = event.target.closest(".lightbox-trigger");
  if (trigger instanceof HTMLElement) {
    openLightbox(trigger.dataset.lightboxSrc || "", trigger.dataset.lightboxAlt || "");
    return;
  }

  const closeTarget = event.target.closest("[data-lightbox-close]");
  if (closeTarget instanceof HTMLElement) {
    closeLightbox();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeLightbox();
  }
});

function syncFavoriteButtons(recipeId, isFavorite, nextValue, label) {
  const forms = document.querySelectorAll(`[data-favorite-form][data-recipe-id="${CSS.escape(recipeId)}"]`);
  for (const form of forms) {
    if (!(form instanceof HTMLFormElement)) {
      continue;
    }
    const button = form.querySelector("[data-favorite-button]");
    if (!(button instanceof HTMLButtonElement)) {
      continue;
    }
    button.classList.toggle("is-active", isFavorite);
    button.value = nextValue;
    button.setAttribute("aria-label", label);
    button.setAttribute("title", label);
  }
}

function maybeRemoveFavoriteCard(form, isFavorite) {
  if (isFavorite || form.dataset.removeCardOnUnfavorite !== "true") {
    return;
  }

  const card = form.closest(".recipe-summary-card");
  if (card instanceof HTMLElement) {
    card.remove();
  }

  const favoritesGrid = document.querySelector("[data-favorites-grid]");
  if (!(favoritesGrid instanceof HTMLElement)) {
    return;
  }

  if (favoritesGrid.querySelector(".recipe-summary-card")) {
    return;
  }

  const emptyState = document.createElement("article");
  emptyState.className = "empty-state";
  const title = document.createElement("h3");
  title.textContent = favoritesGrid.dataset.emptyTitle || "No favourites yet";
  const body = document.createElement("p");
  body.textContent = favoritesGrid.dataset.emptyBody || "Star recipes from a card or recipe page to build this collection.";
  emptyState.append(title, body);
  favoritesGrid.append(emptyState);
}

function initFavoriteToggles() {
  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-favorite-form]");
    if (!(form instanceof HTMLFormElement)) {
      return;
    }

    event.preventDefault();

    const button = event.submitter instanceof HTMLButtonElement
      ? event.submitter
      : form.querySelector("[data-favorite-button]");
    if (!(button instanceof HTMLButtonElement) || button.disabled) {
      return;
    }

    const formData = new FormData(form);
    formData.set("is_favorite", button.value);
    button.disabled = true;
    button.setAttribute("aria-busy", "true");

    try {
      const response = await fetch(form.action, {
        method: form.method || "POST",
        body: formData,
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
      });

      if (!response.ok) {
        throw new Error(`Favorite toggle failed with status ${response.status}`);
      }

      const payload = await response.json();
      syncFavoriteButtons(payload.recipe_id, payload.is_favorite, payload.next_value, payload.label);
      maybeRemoveFavoriteCard(form, payload.is_favorite);
    } catch (error) {
      console.error(error);
      window.alert("Could not update favourites right now. Try again.");
    } finally {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  });
}

function initCookbookToc() {
  const tocNav = document.querySelector("[data-cookbook-toc]");
  if (!(tocNav instanceof HTMLElement)) {
    return;
  }

  const links = Array.from(tocNav.querySelectorAll("[data-toc-link]")).filter(
    (link) => link instanceof HTMLAnchorElement && link.hash,
  );
  if (links.length === 0) {
    return;
  }

  const entries = links
    .map((link) => {
      const section = document.querySelector(link.hash);
      if (!(section instanceof HTMLElement)) {
        return null;
      }
      return { link, section };
    })
    .filter(Boolean);

  if (entries.length === 0) {
    return;
  }

  let activeId = "";

  function setActive(nextId) {
    if (!nextId || nextId === activeId) {
      return;
    }

    activeId = nextId;
    for (const entry of entries) {
      const isActive = entry.section.id === nextId;
      entry.link.classList.toggle("is-active", isActive);
      if (isActive) {
        entry.link.setAttribute("aria-current", "location");
        entry.link.scrollIntoView({ block: "nearest", inline: "nearest" });
      } else {
        entry.link.removeAttribute("aria-current");
      }
    }
  }

  function currentStickyOffset() {
    const stickyOffset = Number.parseFloat(
      getComputedStyle(document.documentElement).getPropertyValue("--sticky-offset"),
    );
    return Number.isFinite(stickyOffset) ? stickyOffset : 92;
  }

  function updateActiveSection() {
    const activationLine = currentStickyOffset() + 28;
    let nextId = entries[0].section.id;

    for (const entry of entries) {
      if (entry.section.getBoundingClientRect().top <= activationLine) {
        nextId = entry.section.id;
      } else {
        break;
      }
    }

    setActive(nextId);
  }

  tocNav.addEventListener("click", (event) => {
    const target = event.target.closest("[data-toc-link]");
    if (!(target instanceof HTMLAnchorElement) || !target.hash) {
      return;
    }
    const section = document.querySelector(target.hash);
    if (section instanceof HTMLElement) {
      setActive(section.id);
    }
  });

  window.addEventListener("scroll", updateActiveSection, { passive: true });
  window.addEventListener("resize", updateActiveSection);
  updateActiveSection();
}

function normalizeMealPlanText(value) {
  const aliases = new Map([
    ["dahl", "daal"],
    ["dal", "daal"],
  ]);
  const stopwords = new Set(["a", "an", "and", "for", "fresh", "of", "or", "style", "the", "to", "with"]);
  return value
    .toLowerCase()
    .replaceAll("&", " and ")
    .replace(/https?:\/\/\S+/g, " ")
    .replace(/[^a-z0-9]+/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .filter((token) => !stopwords.has(token))
    .map((token) => {
      const aliased = aliases.get(token) || token;
      if (aliased.length > 4 && aliased.endsWith("ies")) {
        return `${aliased.slice(0, -3)}y`;
      }
      if (aliased.length > 4 && aliased.endsWith("s") && !aliased.endsWith("ss") && !aliased.endsWith("us")) {
        return aliased.slice(0, -1);
      }
      return aliased;
    })
    .join(" ");
}

function mealPlanTitleVariants(value) {
  const raw = value.trim();
  if (!raw) {
    return [];
  }

  const variants = new Set();
  const addVariant = (candidate) => {
    const normalized = normalizeMealPlanText(candidate);
    if (normalized) {
      variants.add(normalized);
    }
  };

  addVariant(raw);
  addVariant(raw.replace(/\([^)]*\)/g, " "));
  addVariant(raw.replace(/^(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s*[-:]?\s*/i, ""));
  addVariant(raw.replace(/^(breakfast|lunch|dinner)\s*[-:]?\s*/i, ""));

  for (const match of raw.matchAll(/\(([^)]*)\)/g)) {
    addVariant(match[1]);
  }

  return Array.from(variants);
}

function tokenizeMealPlanValue(value) {
  const normalized = normalizeMealPlanText(value);
  return normalized ? normalized.split(" ") : [];
}

function parseMealPlanRecipeOptions() {
  const datalist = document.getElementById("meal-plan-recipe-options");
  if (!(datalist instanceof HTMLDataListElement)) {
    return [];
  }

  return Array.from(datalist.options)
    .map((option) => option.value.trim())
    .filter(Boolean)
    .map((label) => {
      const idMatch = label.match(/\[([^\]]+)\]\s*$/);
      if (!idMatch) {
        return null;
      }
      const recipeId = idMatch[1];
      const base = label.slice(0, idMatch.index).trim();
      const separatorIndex = base.lastIndexOf(" - ");
      const title = separatorIndex >= 0 ? base.slice(0, separatorIndex).trim() : base;
      const normalizedTitle = normalizeMealPlanText(title);
      return {
        id: recipeId,
        label,
        title,
        normalizedTitle,
        tokens: new Set(tokenizeMealPlanValue(title)),
      };
    })
    .filter(Boolean);
}

function mealPlanSimilarityScore(variant, option) {
  if (!variant || !option.normalizedTitle) {
    return 0;
  }
  if (variant === option.normalizedTitle) {
    return 1;
  }

  const variantTokens = new Set(variant.split(" ").filter(Boolean));
  if (variantTokens.size === 0) {
    return 0;
  }

  let shared = 0;
  for (const token of variantTokens) {
    if (option.tokens.has(token)) {
      shared += 1;
    }
  }

  const overlap = shared / Math.max(variantTokens.size, option.tokens.size, 1);
  const coverage = shared / Math.max(variantTokens.size, 1);
  let score = (overlap * 0.5) + (coverage * 0.35);
  if (option.normalizedTitle.includes(variant) || variant.includes(option.normalizedTitle)) {
    score += 0.15;
  }
  if (shared > 0 && shared === variantTokens.size) {
    score += 0.08;
  }
  if (variantTokens.size === 1 && coverage < 1) {
    score -= 0.12;
  }
  return score;
}

function findBestMealPlanRecipeMatch(value, recipeOptions) {
  const variants = mealPlanTitleVariants(value);
  if (variants.length === 0) {
    return null;
  }

  const scored = [];
  for (const option of recipeOptions) {
    let bestScore = 0;
    for (const variant of variants) {
      const score = mealPlanSimilarityScore(variant, option);
      if (score > bestScore) {
        bestScore = score;
      }
    }
    if (bestScore >= 0.8) {
      scored.push({ option, score: bestScore });
    }
  }

  if (scored.length === 0) {
    return null;
  }

  scored.sort((a, b) => b.score - a.score);
  const best = scored[0];
  const runnerUp = scored[1];
  if (best.score < 0.9) {
    return null;
  }
  if (runnerUp && (best.score - runnerUp.score) < 0.04) {
    return null;
  }
  return best.option;
}

function initMealPlanAutoLink() {
  const mealPlanForm = document.querySelector("[data-meal-plan-form]");
  if (!(mealPlanForm instanceof HTMLFormElement)) {
    return;
  }

  const recipeOptions = parseMealPlanRecipeOptions();
  if (recipeOptions.length === 0) {
    return;
  }

  function maybeAutoFillRecipe(titleInput) {
    if (!(titleInput instanceof HTMLInputElement)) {
      return;
    }
    const row = titleInput.closest("tr");
    if (!(row instanceof HTMLTableRowElement)) {
      return;
    }
    const recipeInput = row.querySelector("[data-meal-plan-recipe]");
    if (!(recipeInput instanceof HTMLInputElement)) {
      return;
    }

    const currentRecipeValue = recipeInput.value.trim();
    const isManual = recipeInput.dataset.manualEntry === "true";
    const isAutofilled = recipeInput.dataset.autofilled === "true";
    if (currentRecipeValue && isManual && !isAutofilled) {
      return;
    }

    const match = findBestMealPlanRecipeMatch(titleInput.value, recipeOptions);
    if (match) {
      recipeInput.value = match.label;
      recipeInput.dataset.autofilled = "true";
      recipeInput.dataset.manualEntry = "false";
      return;
    }

    if (isAutofilled) {
      recipeInput.value = "";
      recipeInput.dataset.autofilled = "false";
    }
  }

  mealPlanForm.addEventListener("input", (event) => {
    const titleInput = event.target.closest("[data-meal-plan-title]");
    if (titleInput instanceof HTMLInputElement) {
      window.clearTimeout(titleInput._mealPlanAutoLinkTimer);
      titleInput._mealPlanAutoLinkTimer = window.setTimeout(() => {
        maybeAutoFillRecipe(titleInput);
      }, 180);
      return;
    }

    const recipeInput = event.target.closest("[data-meal-plan-recipe]");
    if (recipeInput instanceof HTMLInputElement) {
      const hasValue = recipeInput.value.trim() !== "";
      recipeInput.dataset.manualEntry = hasValue ? "true" : "false";
      if (!hasValue) {
        recipeInput.dataset.autofilled = "false";
      }
    }
  });

  mealPlanForm.addEventListener("blur", (event) => {
    const titleInput = event.target.closest("[data-meal-plan-title]");
    if (titleInput instanceof HTMLInputElement) {
      maybeAutoFillRecipe(titleInput);
    }
  }, true);
}

function uploadFormElements(form) {
  return {
    fileInput: form.querySelector('input[type="file"][name="files"]'),
    label: form.querySelector(".upload-inline__label"),
    progress: form.querySelector("[data-upload-progress]"),
    progressBar: form.querySelector("[data-upload-progress-bar]"),
    progressText: form.querySelector("[data-upload-progress-text]"),
  };
}

function setUploadProgressState(form, state) {
  const { fileInput, label, progress, progressBar, progressText } = uploadFormElements(form);
  if (!(fileInput instanceof HTMLInputElement) || !(label instanceof HTMLElement)) {
    return;
  }

  const uploading = state === "uploading";
  fileInput.disabled = uploading;
  label.classList.toggle("is-uploading", uploading);
  label.setAttribute("aria-disabled", uploading ? "true" : "false");

  if (progress instanceof HTMLElement) {
    progress.hidden = !uploading;
  }
  if (progressText instanceof HTMLElement) {
    progressText.hidden = !uploading;
  }
  if (!uploading && progressBar instanceof HTMLElement) {
    progressBar.style.width = "0%";
  }
}

function updateUploadProgress(form, percent, message) {
  const { progressBar, progressText } = uploadFormElements(form);
  if (progressBar instanceof HTMLElement) {
    progressBar.style.width = `${Math.max(0, Math.min(percent, 100))}%`;
  }
  if (progressText instanceof HTMLElement) {
    progressText.textContent = message;
  }
}

window.submitUploadFormWithProgress = function submitUploadFormWithProgress(form) {
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  const { fileInput } = uploadFormElements(form);
  if (!(fileInput instanceof HTMLInputElement) || !fileInput.files || fileInput.files.length === 0) {
    return;
  }

  const formData = new FormData(form);
  const fileCount = fileInput.files.length;
  const noun = fileCount === 1 ? "file" : "files";
  setUploadProgressState(form, "uploading");
  updateUploadProgress(form, 0, `Uploading ${fileCount} ${noun}... 0%`);

  const xhr = new XMLHttpRequest();
  xhr.open(form.method || "POST", form.action, true);
  xhr.upload.addEventListener("progress", (event) => {
    if (!event.lengthComputable) {
      updateUploadProgress(form, 100, `Uploading ${fileCount} ${noun}...`);
      return;
    }
    const percent = Math.round((event.loaded / event.total) * 100);
    updateUploadProgress(form, percent, `Uploading ${fileCount} ${noun}... ${percent}%`);
  });

  xhr.addEventListener("load", () => {
    if (xhr.status >= 200 && xhr.status < 400) {
      updateUploadProgress(form, 100, `Upload complete. Redirecting...`);
      window.location.href = xhr.responseURL || window.location.href;
      return;
    }
    setUploadProgressState(form, "idle");
    updateUploadProgress(form, 0, "Upload failed. Try again.");
    window.alert("Upload failed. Try again.");
  });

  xhr.addEventListener("error", () => {
    setUploadProgressState(form, "idle");
    updateUploadProgress(form, 0, "Upload failed. Try again.");
    window.alert("Upload failed. Try again.");
  });

  xhr.addEventListener("abort", () => {
    setUploadProgressState(form, "idle");
    updateUploadProgress(form, 0, "Upload cancelled.");
  });

  xhr.send(formData);
};

const searchForm = document.querySelector(".search-form--hero");
const searchQueryInput = searchForm?.querySelector("#search-page-q");
const ingredientSelectize = searchForm?.querySelector("[data-ingredient-selectize]");
const selectedContainer = ingredientSelectize?.querySelector("[data-search-selected]");
const ingredientInput = ingredientSelectize?.querySelector("[data-ingredient-input]");
const ingredientOptions = ingredientSelectize?.querySelector("[data-ingredient-options]");
const searchSubmitButton = searchForm?.querySelector('button[type="submit"]');

let searchResultsPanel = document.querySelector("[data-search-results-panel]");
let ingredientIndexPanel = document.querySelector("[data-ingredient-index-panel]");

let ingredientSuggestions = [];
let activeIngredientSuggestion = -1;
let ingredientFetchController = null;
let ingredientFetchTimeout = 0;
let ingredientRequestId = 0;
let searchPageController = null;
let searchPageRequestId = 0;

function selectedIngredients() {
  if (!selectedContainer) {
    return [];
  }
  return Array.from(selectedContainer.querySelectorAll('input[name="ingredient"]'))
    .map((input) => input.value.trim().toLowerCase())
    .filter(Boolean);
}

function renderSelectedIngredients(values = selectedIngredients()) {
  if (!selectedContainer || !(ingredientInput instanceof HTMLInputElement)) {
    return;
  }

  const uniqueIngredients = [...new Set(
    values
      .map((value) => value.trim().toLowerCase())
      .filter(Boolean),
  )];

  selectedContainer.innerHTML = "";
  for (const ingredient of uniqueIngredients) {
    const hiddenInput = document.createElement("input");
    hiddenInput.type = "hidden";
    hiddenInput.name = "ingredient";
    hiddenInput.value = ingredient;
    selectedContainer.appendChild(hiddenInput);

    const chip = document.createElement("button");
    chip.className = "search-selectize__token";
    chip.type = "button";
    chip.dataset.removeIngredient = ingredient;
    chip.setAttribute("aria-label", `Remove ${ingredient}`);

    const label = document.createElement("span");
    label.textContent = ingredient;
    const close = document.createElement("span");
    close.setAttribute("aria-hidden", "true");
    close.textContent = "×";
    chip.append(label, close);

    selectedContainer.appendChild(chip);
  }
  selectedContainer.appendChild(ingredientInput);
}

function addSelectedIngredient(value) {
  const normalized = value.trim().toLowerCase();
  if (!normalized) {
    return false;
  }

  const nextIngredients = selectedIngredients();
  if (nextIngredients.includes(normalized)) {
    return false;
  }

  nextIngredients.push(normalized);
  renderSelectedIngredients(nextIngredients);
  return true;
}

function removeSelectedIngredient(value) {
  const normalized = value.trim().toLowerCase();
  const currentIngredients = selectedIngredients();
  const nextIngredients = currentIngredients.filter((ingredient) => ingredient !== normalized);
  if (nextIngredients.length === currentIngredients.length) {
    return false;
  }
  renderSelectedIngredients(nextIngredients);
  return true;
}

function closeIngredientOptions() {
  activeIngredientSuggestion = -1;
  if (ingredientOptions instanceof HTMLElement) {
    ingredientOptions.hidden = true;
  }
  if (ingredientInput instanceof HTMLInputElement) {
    ingredientInput.setAttribute("aria-expanded", "false");
    ingredientInput.removeAttribute("aria-activedescendant");
  }
}

function setActiveIngredientSuggestion(index) {
  if (!(ingredientOptions instanceof HTMLElement) || !(ingredientInput instanceof HTMLInputElement)) {
    return;
  }

  const options = Array.from(ingredientOptions.querySelectorAll("[data-ingredient-option]"));
  if (options.length === 0) {
    activeIngredientSuggestion = -1;
    ingredientInput.removeAttribute("aria-activedescendant");
    return;
  }

  const boundedIndex = Math.max(0, Math.min(index, options.length - 1));
  activeIngredientSuggestion = boundedIndex;

  for (const [optionIndex, option] of options.entries()) {
    const isActive = optionIndex === boundedIndex;
    option.classList.toggle("is-active", isActive);
    option.setAttribute("aria-selected", isActive ? "true" : "false");
    if (isActive) {
      ingredientInput.setAttribute("aria-activedescendant", option.id);
      option.scrollIntoView({ block: "nearest" });
    }
  }
}

function renderIngredientOptions(items) {
  if (!(ingredientOptions instanceof HTMLElement) || !(ingredientInput instanceof HTMLInputElement)) {
    return;
  }

  ingredientSuggestions = items
    .filter(
      (item) => item
        && typeof item.name === "string"
        && !selectedIngredients().includes(item.name.trim().toLowerCase()),
    )
    .slice(0, 8);
  ingredientOptions.innerHTML = "";
  activeIngredientSuggestion = -1;
  ingredientInput.removeAttribute("aria-activedescendant");

  if (ingredientSuggestions.length === 0) {
    const emptyState = document.createElement("div");
    emptyState.className = "search-selectize__empty";
    emptyState.textContent = "No indexed ingredients match that search.";
    ingredientOptions.appendChild(emptyState);
  } else {
    for (const [index, item] of ingredientSuggestions.entries()) {
      const option = document.createElement("button");
      option.type = "button";
      option.id = `search-page-ingredient-option-${index}`;
      option.className = "search-selectize__option";
      option.dataset.ingredientOption = item.name;
      option.dataset.ingredientOptionIndex = String(index);
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", "false");

      const name = document.createElement("span");
      name.textContent = item.name;

      const count = document.createElement("span");
      count.className = "search-selectize__option-count";
      count.textContent = `${item.recipe_count} recipe${item.recipe_count === 1 ? "" : "s"}`;

      option.append(name, count);
      ingredientOptions.appendChild(option);
    }
  }

  ingredientOptions.hidden = false;
  ingredientInput.setAttribute("aria-expanded", "true");
}

async function loadIngredientOptions(query = "") {
  if (
    !(ingredientSelectize instanceof HTMLElement)
    || !(ingredientOptions instanceof HTMLElement)
    || !(ingredientInput instanceof HTMLInputElement)
  ) {
    return;
  }

  const endpoint = ingredientSelectize.dataset.ingredientsUrl;
  if (!endpoint) {
    return;
  }

  ingredientFetchController?.abort();
  ingredientFetchController = new AbortController();

  const params = new URLSearchParams();
  const trimmedQuery = query.trim();
  if (trimmedQuery) {
    params.set("q", trimmedQuery);
  }
  for (const ingredient of selectedIngredients()) {
    params.append("ingredient", ingredient);
  }

  const requestId = ingredientRequestId + 1;
  ingredientRequestId = requestId;

  try {
    const response = await fetch(`${endpoint}?${params.toString()}`, {
      headers: { Accept: "application/json" },
      signal: ingredientFetchController.signal,
    });

    if (!response.ok) {
      throw new Error(`Ingredient lookup failed with status ${response.status}`);
    }

    const payload = await response.json();
    if (requestId !== ingredientRequestId) {
      return;
    }

    renderIngredientOptions(Array.isArray(payload.items) ? payload.items : []);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      return;
    }
    console.error(error);
    renderIngredientOptions([]);
  }
}

function queueIngredientOptions() {
  if (!(ingredientInput instanceof HTMLInputElement)) {
    return;
  }
  window.clearTimeout(ingredientFetchTimeout);
  ingredientFetchTimeout = window.setTimeout(() => {
    void loadIngredientOptions(ingredientInput.value);
  }, 120);
}

function buildSearchPageUrl() {
  if (!(searchForm instanceof HTMLFormElement)) {
    return window.location.pathname + window.location.search;
  }

  const params = new URLSearchParams();
  const query = searchQueryInput instanceof HTMLInputElement ? searchQueryInput.value.trim() : "";
  if (query) {
    params.set("q", query);
  }
  for (const ingredient of selectedIngredients()) {
    params.append("ingredient", ingredient);
  }

  const actionUrl = new URL(searchForm.action, window.location.origin);
  const queryString = params.toString();
  return queryString ? `${actionUrl.pathname}?${queryString}` : actionUrl.pathname;
}

function setSearchLoadingState(isLoading) {
  if (searchResultsPanel instanceof HTMLElement) {
    searchResultsPanel.setAttribute("aria-busy", isLoading ? "true" : "false");
  }
  if (ingredientIndexPanel instanceof HTMLElement) {
    ingredientIndexPanel.setAttribute("aria-busy", isLoading ? "true" : "false");
  }
  if (searchSubmitButton instanceof HTMLButtonElement) {
    searchSubmitButton.disabled = isLoading;
    searchSubmitButton.setAttribute("aria-busy", isLoading ? "true" : "false");
  }
}

async function refreshSearchPage() {
  if (!(searchResultsPanel instanceof HTMLElement) || !(ingredientIndexPanel instanceof HTMLElement)) {
    return;
  }

  const nextUrl = buildSearchPageUrl();
  searchPageController?.abort();
  searchPageController = new AbortController();

  const requestId = searchPageRequestId + 1;
  searchPageRequestId = requestId;
  setSearchLoadingState(true);

  try {
    const response = await fetch(nextUrl, {
      headers: {
        Accept: "text/html",
        "X-Requested-With": "XMLHttpRequest",
      },
      signal: searchPageController.signal,
    });

    if (!response.ok) {
      throw new Error(`Search page refresh failed with status ${response.status}`);
    }

    const html = await response.text();
    if (requestId !== searchPageRequestId) {
      return;
    }

    const parser = new DOMParser();
    const nextDocument = parser.parseFromString(html, "text/html");
    const nextResultsPanel = nextDocument.querySelector("[data-search-results-panel]");
    const nextIngredientIndexPanel = nextDocument.querySelector("[data-ingredient-index-panel]");

    if (!(nextResultsPanel instanceof HTMLElement) || !(nextIngredientIndexPanel instanceof HTMLElement)) {
      throw new Error("Search page refresh did not return the expected panels.");
    }

    searchResultsPanel.innerHTML = nextResultsPanel.innerHTML;
    ingredientIndexPanel.innerHTML = nextIngredientIndexPanel.innerHTML;
    window.history.replaceState(null, "", nextUrl);
    if (nextDocument.title) {
      document.title = nextDocument.title;
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      return;
    }
    console.error(error);
    window.location.assign(nextUrl);
  } finally {
    if (requestId === searchPageRequestId) {
      setSearchLoadingState(false);
    }
  }
}

function commitAutocompleteIngredient(index) {
  const nextItem = ingredientSuggestions[index];
  if (!nextItem || !addSelectedIngredient(nextItem.name)) {
    return false;
  }

  if (ingredientInput instanceof HTMLInputElement) {
    ingredientInput.value = "";
    ingredientInput.focus();
  }
  closeIngredientOptions();
  void refreshSearchPage();
  return true;
}

function commitTypedIngredient() {
  if (!(ingredientInput instanceof HTMLInputElement)) {
    return false;
  }

  const normalizedValue = ingredientInput.value.trim().toLowerCase();
  if (!normalizedValue) {
    return false;
  }

  const exactMatchIndex = ingredientSuggestions.findIndex(
    (item) => item.name.trim().toLowerCase() === normalizedValue,
  );
  if (exactMatchIndex >= 0) {
    return commitAutocompleteIngredient(exactMatchIndex);
  }

  if (!addSelectedIngredient(normalizedValue)) {
    return false;
  }

  ingredientInput.value = "";
  closeIngredientOptions();
  void refreshSearchPage();
  return true;
}

if (searchForm && selectedContainer) {
  renderSelectedIngredients();

  if (
    ingredientSelectize instanceof HTMLElement
    && ingredientInput instanceof HTMLInputElement
    && ingredientOptions instanceof HTMLElement
  ) {
    ingredientInput.addEventListener("focus", () => {
      void loadIngredientOptions(ingredientInput.value);
    });

    ingredientInput.addEventListener("input", () => {
      closeIngredientOptions();
      queueIngredientOptions();
    });

    ingredientInput.addEventListener("blur", () => {
      window.setTimeout(() => {
        closeIngredientOptions();
      }, 120);
    });

    ingredientInput.addEventListener("keydown", (event) => {
      if (event.key === "Backspace" && ingredientInput.value === "") {
        const currentIngredients = selectedIngredients();
        const lastIngredient = currentIngredients.at(-1);
        if (lastIngredient) {
          event.preventDefault();
          const didRemoveIngredient = removeSelectedIngredient(lastIngredient);
          if (didRemoveIngredient) {
            void refreshSearchPage();
          }
        }
        return;
      }

      if (event.key === "ArrowDown") {
        event.preventDefault();
        if (ingredientOptions.hidden) {
          void loadIngredientOptions(ingredientInput.value);
          return;
        }
        setActiveIngredientSuggestion(activeIngredientSuggestion + 1);
        return;
      }

      if (event.key === "ArrowUp") {
        event.preventDefault();
        if (ingredientOptions.hidden) {
          void loadIngredientOptions(ingredientInput.value);
          return;
        }
        setActiveIngredientSuggestion(activeIngredientSuggestion <= 0 ? 0 : activeIngredientSuggestion - 1);
        return;
      }

      if (event.key === "Enter") {
        if (!ingredientOptions.hidden && activeIngredientSuggestion >= 0) {
          event.preventDefault();
          commitAutocompleteIngredient(activeIngredientSuggestion);
          return;
        }

        if (commitTypedIngredient()) {
          event.preventDefault();
        }
        return;
      }

      if (event.key === "Escape") {
        closeIngredientOptions();
      }
    });

    ingredientOptions.addEventListener("mousedown", (event) => {
      event.preventDefault();
    });

    ingredientOptions.addEventListener("click", (event) => {
      const option = event.target.closest("[data-ingredient-option]");
      if (!(option instanceof HTMLElement)) {
        return;
      }
      const optionIndex = Number.parseInt(option.dataset.ingredientOptionIndex || "-1", 10);
      if (optionIndex >= 0) {
        commitAutocompleteIngredient(optionIndex);
      }
    });

    searchForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const didCommitIngredient = commitTypedIngredient();
      closeIngredientOptions();
      if (!didCommitIngredient) {
        void refreshSearchPage();
      }
    });

    document.addEventListener("click", (event) => {
      if (event.target.closest("[data-ingredient-selectize]")) {
        return;
      }
      closeIngredientOptions();
    });

    selectedContainer.addEventListener("click", () => {
      ingredientInput.focus();
    });
  }
}

document.addEventListener("click", (event) => {
  const addTarget = event.target.closest("[data-add-ingredient]");
  if (addTarget instanceof HTMLElement && searchForm) {
    const didAddIngredient = addSelectedIngredient(addTarget.dataset.addIngredient || "");
    if (ingredientInput instanceof HTMLInputElement) {
      ingredientInput.value = "";
      ingredientInput.focus();
    }
    closeIngredientOptions();
    if (didAddIngredient) {
      void refreshSearchPage();
    }
    return;
  }

  const removeTarget = event.target.closest("[data-remove-ingredient]");
  if (removeTarget instanceof HTMLElement && searchForm) {
    const didRemoveIngredient = removeSelectedIngredient(removeTarget.dataset.removeIngredient || "");
    closeIngredientOptions();
    if (ingredientInput instanceof HTMLInputElement) {
      ingredientInput.focus();
    }
    if (didRemoveIngredient) {
      void refreshSearchPage();
    }
  }
});

initFavoriteToggles();
initCookbookToc();
initMealPlanAutoLink();
