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

function syncRecipeToggleButtons(recipeId, toggleKind, isActive, nextValue, label) {
  const forms = document.querySelectorAll(
    `[data-recipe-toggle-form][data-toggle-kind="${CSS.escape(toggleKind)}"][data-recipe-id="${CSS.escape(recipeId)}"]`,
  );
  for (const form of forms) {
    if (!(form instanceof HTMLFormElement)) {
      continue;
    }
    const button = form.querySelector("[data-recipe-toggle-button]");
    if (!(button instanceof HTMLButtonElement)) {
      continue;
    }
    button.classList.toggle("is-active", isActive);
    button.value = nextValue;
    button.setAttribute("aria-label", label);
    button.setAttribute("title", label);
  }
}

function maybeRemoveRecipeToggleCard(form, isActive) {
  if (isActive || form.dataset.removeCardOnInactive !== "true") {
    return;
  }

  const card = form.closest(".recipe-summary-card");
  if (card instanceof HTMLElement) {
    card.remove();
  }

  const toggleKind = form.dataset.toggleKind || "";
  const collectionGrid = document.querySelector(`[data-toggle-grid][data-toggle-kind="${CSS.escape(toggleKind)}"]`);
  if (!(collectionGrid instanceof HTMLElement)) {
    return;
  }

  if (collectionGrid.querySelector(".recipe-summary-card")) {
    return;
  }

  const emptyState = document.createElement("article");
  emptyState.className = "empty-state";
  const title = document.createElement("h3");
  title.textContent = collectionGrid.dataset.emptyTitle || "No recipes yet";
  const body = document.createElement("p");
  body.textContent = collectionGrid.dataset.emptyBody || "Use a recipe toggle to build this collection.";
  emptyState.append(title, body);
  collectionGrid.append(emptyState);
}

function initRecipeToggles() {
  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-recipe-toggle-form]");
    if (!(form instanceof HTMLFormElement)) {
      return;
    }

    event.preventDefault();

    const button = event.submitter instanceof HTMLButtonElement
      ? event.submitter
      : form.querySelector("[data-recipe-toggle-button]");
    if (!(button instanceof HTMLButtonElement) || button.disabled) {
      return;
    }

    const formData = new FormData(form);
    const fieldName = button.name;
    if (!fieldName) {
      return;
    }
    formData.set(fieldName, button.value);
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
        throw new Error(`Recipe toggle failed with status ${response.status}`);
      }

      const payload = await response.json();
      syncRecipeToggleButtons(payload.recipe_id, payload.toggle_kind, payload.is_active, payload.next_value, payload.label);
      maybeRemoveRecipeToggleCard(form, payload.is_active);
    } catch (error) {
      console.error(error);
      window.alert("Could not update recipe flags right now. Try again.");
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
  if (best.score < 0.81) {
    return null;
  }
  if (runnerUp && (best.score - runnerUp.score) < 0.035) {
    return null;
  }
  return best.option;
}

function findMealPlanRecipeOptionByLabel(value, recipeOptions) {
  const label = value.trim();
  if (!label) {
    return null;
  }
  return recipeOptions.find((option) => option.label === label) || null;
}

function findMealPlanRecipeOptionById(value, recipeOptions) {
  const recipeId = value.trim();
  if (!recipeId) {
    return null;
  }
  return recipeOptions.find((option) => option.id === recipeId) || null;
}

function initMealPlanAutoLink() {
  const mealPlanForm = document.querySelector("[data-meal-plan-form]");
  if (!(mealPlanForm instanceof HTMLFormElement)) {
    return;
  }

  const mealPlanSuggestionsUrl = mealPlanForm.dataset.mealPlanSuggestionsUrl || "";
  const mealPlanRecipeUrlTemplate = mealPlanForm.dataset.mealPlanRecipeUrlTemplate || "";
  const recipeOptions = parseMealPlanRecipeOptions();
  const recipeOptionsDatalist = document.getElementById("meal-plan-recipe-options");
  const saveStatus = mealPlanForm.querySelector("[data-meal-plan-save-status]");
  const statLabels = {
    weeks: "weeks",
    slot_count: "planned meals",
    linked_slot_count: "recipe links",
    completed_slot_count: "complete",
  };
  let autosaveTimer = 0;
  let autosaveController = null;
  let recipeSuggestionController = null;
  let recipeSuggestionRequestId = 0;
  let saveNonce = 0;
  let draggingRow = null;
  let dragSourceWeekEntries = null;
  let dragStartOrder = "";

  if (recipeOptionsDatalist instanceof HTMLDataListElement) {
    recipeOptionsDatalist.innerHTML = "";
  }

  function updateSaveStatus(message, state = "") {
    if (!(saveStatus instanceof HTMLElement)) {
      return;
    }
    saveStatus.textContent = message;
    saveStatus.dataset.state = state;
  }

  function updateMealPlanStats(stats) {
    if (!stats || typeof stats !== "object") {
      return;
    }
    for (const [key, label] of Object.entries(statLabels)) {
      const element = document.querySelector(`[data-meal-plan-stat="${CSS.escape(key)}"]`);
      if (!(element instanceof HTMLElement)) {
        continue;
      }
      const value = Number.parseInt(String(stats[key] ?? ""), 10);
      if (!Number.isFinite(value)) {
        continue;
      }
      element.textContent = `${value} ${label}`;
    }
  }

  function mealPlanRowElements(itemInput) {
    const row = itemInput.closest("[data-meal-plan-row]");
    if (!(row instanceof HTMLElement)) {
      return null;
    }
    const recipeIdInput = row.querySelector("[data-meal-plan-recipe-id]");
    if (!(recipeIdInput instanceof HTMLInputElement)) {
      return null;
    }
    const actionContent = row.querySelector("[data-meal-plan-row-action-content]");
    return { row, recipeIdInput, actionContent };
  }

  function mealPlanRecipeUrl(recipeId) {
    const trimmedRecipeId = recipeId.trim();
    if (!mealPlanRecipeUrlTemplate || !trimmedRecipeId) {
      return "";
    }
    return mealPlanRecipeUrlTemplate.replace("__RECIPE_ID__", encodeURIComponent(trimmedRecipeId));
  }

  function renderMealPlanRowAction(actionContent, option) {
    if (!(actionContent instanceof HTMLElement)) {
      return;
    }

    actionContent.replaceChildren();
    if (option) {
      const link = document.createElement("a");
      link.className = "button button--secondary";
      link.href = mealPlanRecipeUrl(option.id);
      link.textContent = "Open Recipe";
      actionContent.appendChild(link);
      return;
    }

    const placeholder = document.createElement("span");
    placeholder.className = "meal-plan-row__action-placeholder";
    placeholder.textContent = "Custom item";
    actionContent.appendChild(placeholder);
  }

  function setMealPlanRecipeSelection(itemInput, rowElements, option) {
    itemInput.value = option.title;
    rowElements.recipeIdInput.value = option.id;
    itemInput.dataset.recipeLinked = "true";
    renderMealPlanRowAction(rowElements.actionContent, option);
  }

  function clearMealPlanRecipeSelection(itemInput, rowElements) {
    rowElements.recipeIdInput.value = "";
    itemInput.dataset.recipeLinked = "false";
    renderMealPlanRowAction(rowElements.actionContent, null);
  }

  function syncMealPlanItemInput(itemInput) {
    if (!(itemInput instanceof HTMLInputElement)) {
      return null;
    }
    const rowElements = mealPlanRowElements(itemInput);
    if (!rowElements) {
      return null;
    }
    const { recipeIdInput, actionContent } = rowElements;

    const currentValue = itemInput.value.trim();
    if (!currentValue) {
      clearMealPlanRecipeSelection(itemInput, rowElements);
      return null;
    }

    const linkedOption = findMealPlanRecipeOptionById(recipeIdInput.value, recipeOptions);
    if (linkedOption && (currentValue === linkedOption.title || currentValue === linkedOption.label)) {
      itemInput.dataset.recipeLinked = "true";
      renderMealPlanRowAction(actionContent, linkedOption);
      return linkedOption;
    }

    const exactMatch = findMealPlanRecipeOptionByLabel(currentValue, recipeOptions);
    if (exactMatch) {
      setMealPlanRecipeSelection(itemInput, rowElements, exactMatch);
      return exactMatch;
    }

    const normalizedValue = normalizeMealPlanText(currentValue);
    if (normalizedValue) {
      const exactTitleMatches = recipeOptions.filter((option) => option.normalizedTitle === normalizedValue);
      if (exactTitleMatches.length === 1) {
        setMealPlanRecipeSelection(itemInput, rowElements, exactTitleMatches[0]);
        return exactTitleMatches[0];
      }
    }

    clearMealPlanRecipeSelection(itemInput, rowElements);
    return null;
  }

  function maybeResolveMealPlanItemInput(itemInput) {
    const rowElements = mealPlanRowElements(itemInput);
    if (!rowElements) {
      return;
    }
    const exactMatch = syncMealPlanItemInput(itemInput);
    if (exactMatch || !itemInput.value.trim()) {
      return;
    }

    const fuzzyMatch = findBestMealPlanRecipeMatch(itemInput.value, recipeOptions);
    if (fuzzyMatch) {
      setMealPlanRecipeSelection(itemInput, rowElements, fuzzyMatch);
    }
  }

  function renderMealPlanRecipeSuggestions(items) {
    if (!(recipeOptionsDatalist instanceof HTMLDataListElement)) {
      return;
    }
    recipeOptionsDatalist.innerHTML = "";
    for (const item of items) {
      if (!item || typeof item.label !== "string" || !item.label.trim()) {
        continue;
      }
      const option = document.createElement("option");
      option.value = item.label.trim();
      recipeOptionsDatalist.appendChild(option);
    }
  }

  async function loadMealPlanRecipeSuggestions(query) {
    if (!(recipeOptionsDatalist instanceof HTMLDataListElement)) {
      return;
    }
    const trimmedQuery = query.trim();
    if (!mealPlanSuggestionsUrl || !trimmedQuery) {
      renderMealPlanRecipeSuggestions([]);
      return;
    }

    recipeSuggestionController?.abort();
    recipeSuggestionController = new AbortController();
    const requestId = recipeSuggestionRequestId + 1;
    recipeSuggestionRequestId = requestId;

    const params = new URLSearchParams({
      q: trimmedQuery,
      limit: "6",
    });

    try {
      const response = await fetch(`${mealPlanSuggestionsUrl}?${params.toString()}`, {
        headers: { Accept: "application/json" },
        signal: recipeSuggestionController.signal,
      });
      if (!response.ok) {
        throw new Error(`Meal plan recipe lookup failed with status ${response.status}`);
      }
      const payload = await response.json();
      if (requestId !== recipeSuggestionRequestId) {
        return;
      }
      renderMealPlanRecipeSuggestions(Array.isArray(payload.items) ? payload.items : []);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return;
      }
      console.error(error);
      renderMealPlanRecipeSuggestions([]);
    }
  }

  async function saveMealPlanNow() {
    window.clearTimeout(autosaveTimer);
    const currentSave = ++saveNonce;
    if (autosaveController) {
      autosaveController.abort();
    }
    autosaveController = new AbortController();
    updateSaveStatus("Saving…", "saving");

    const formData = new FormData(mealPlanForm);
    formData.set("planner_action", "autosave");

    try {
      const response = await fetch(mealPlanForm.action, {
        method: mealPlanForm.method || "POST",
        body: formData,
        signal: autosaveController.signal,
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
      });
      if (!response.ok) {
        throw new Error(`Meal plan autosave failed with status ${response.status}`);
      }
      const payload = await response.json();
      if (currentSave !== saveNonce) {
        return;
      }
      updateMealPlanStats(payload.stats);
      updateSaveStatus(payload.notice || "Saved.", "saved");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return;
      }
      console.error(error);
      updateSaveStatus("Autosave failed. Changes are still on the page.", "error");
    }
  }

  function queueMealPlanSave(delay = 700) {
    window.clearTimeout(autosaveTimer);
    updateSaveStatus("Unsaved changes…", "dirty");
    autosaveTimer = window.setTimeout(() => {
      saveMealPlanNow();
    }, delay);
  }

  function mealPlanWeekOrder(weekEntries) {
    if (!(weekEntries instanceof HTMLElement)) {
      return "";
    }
    return Array.from(weekEntries.querySelectorAll("[data-meal-plan-row]"))
      .map((row) => row.querySelector('input[name^="week_entry_id__"]'))
      .filter((input) => input instanceof HTMLInputElement)
      .map((input) => input.value)
      .join("|");
  }

  function mealPlanDragTargetRow(weekEntries, clientY) {
    if (!(weekEntries instanceof HTMLElement)) {
      return null;
    }

    const rows = Array.from(weekEntries.querySelectorAll("[data-meal-plan-row]"))
      .filter((row) => row instanceof HTMLElement && row !== draggingRow);
    let targetRow = null;
    let targetOffset = Number.NEGATIVE_INFINITY;

    for (const row of rows) {
      const bounds = row.getBoundingClientRect();
      const offset = clientY - bounds.top - (bounds.height / 2);
      if (offset < 0 && offset > targetOffset) {
        targetOffset = offset;
        targetRow = row;
      }
    }

    return targetRow;
  }

  function cleanupMealPlanDragState() {
    if (draggingRow instanceof HTMLElement) {
      draggingRow.classList.remove("is-dragging");
    }
    if (dragSourceWeekEntries instanceof HTMLElement) {
      dragSourceWeekEntries.classList.remove("is-drag-target");
    }
    draggingRow = null;
    dragSourceWeekEntries = null;
    dragStartOrder = "";
  }

  mealPlanForm.addEventListener("submit", () => {
    window.clearTimeout(autosaveTimer);
    if (autosaveController) {
      autosaveController.abort();
    }
  });

  mealPlanForm.addEventListener("input", (event) => {
    const itemInput = event.target.closest("[data-meal-plan-item]");
    if (itemInput instanceof HTMLInputElement) {
      syncMealPlanItemInput(itemInput);
      window.clearTimeout(itemInput._mealPlanSuggestionTimer);
      itemInput._mealPlanSuggestionTimer = window.setTimeout(() => {
        void loadMealPlanRecipeSuggestions(itemInput.value);
      }, 120);
      queueMealPlanSave();
      return;
    }

    if (
      event.target instanceof HTMLInputElement
      || event.target instanceof HTMLTextAreaElement
      || event.target instanceof HTMLSelectElement
    ) {
      queueMealPlanSave(event.target instanceof HTMLInputElement && event.target.type === "checkbox" ? 120 : 700);
    }
  });

  mealPlanForm.addEventListener("blur", (event) => {
    const itemInput = event.target.closest("[data-meal-plan-item]");
    if (itemInput instanceof HTMLInputElement) {
      maybeResolveMealPlanItemInput(itemInput);
      void loadMealPlanRecipeSuggestions(itemInput.value);
      queueMealPlanSave(120);
    }
  }, true);

  mealPlanForm.addEventListener("focusin", (event) => {
    const itemInput = event.target.closest("[data-meal-plan-item]");
    if (itemInput instanceof HTMLInputElement) {
      void loadMealPlanRecipeSuggestions(itemInput.value);
    }
  });

  mealPlanForm.querySelectorAll("[data-meal-plan-item]").forEach((input) => {
    if (input instanceof HTMLInputElement) {
      syncMealPlanItemInput(input);
    }
  });

  mealPlanForm.addEventListener("dragstart", (event) => {
    if (!(event.target instanceof Element)) {
      return;
    }
    const handle = event.target.closest("[data-meal-plan-drag-handle]");
    if (!(handle instanceof HTMLElement)) {
      return;
    }

    const row = handle.closest("[data-meal-plan-row]");
    const weekEntries = row?.closest("[data-meal-plan-week-entries]");
    if (!(row instanceof HTMLElement) || !(weekEntries instanceof HTMLElement) || !event.dataTransfer) {
      return;
    }

    draggingRow = row;
    dragSourceWeekEntries = weekEntries;
    dragStartOrder = mealPlanWeekOrder(weekEntries);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", dragStartOrder);
    event.dataTransfer.setDragImage(row, 24, 24);
    row.classList.add("is-dragging");
    weekEntries.classList.add("is-drag-target");
  });

  mealPlanForm.addEventListener("dragover", (event) => {
    if (!(event.target instanceof Element)) {
      return;
    }
    const weekEntries = event.target.closest("[data-meal-plan-week-entries]");
    if (
      !(draggingRow instanceof HTMLElement)
      || !(dragSourceWeekEntries instanceof HTMLElement)
      || !(weekEntries instanceof HTMLElement)
      || weekEntries !== dragSourceWeekEntries
    ) {
      return;
    }

    event.preventDefault();
    const targetRow = mealPlanDragTargetRow(weekEntries, event.clientY);
    if (targetRow) {
      weekEntries.insertBefore(draggingRow, targetRow);
      return;
    }
    weekEntries.append(draggingRow);
  });

  mealPlanForm.addEventListener("drop", (event) => {
    if (!(event.target instanceof Element)) {
      return;
    }
    const weekEntries = event.target.closest("[data-meal-plan-week-entries]");
    if (
      !(draggingRow instanceof HTMLElement)
      || !(dragSourceWeekEntries instanceof HTMLElement)
      || !(weekEntries instanceof HTMLElement)
      || weekEntries !== dragSourceWeekEntries
    ) {
      return;
    }
    event.preventDefault();
  });

  mealPlanForm.addEventListener("dragend", () => {
    const startOrder = dragStartOrder;
    const finalOrder = mealPlanWeekOrder(dragSourceWeekEntries);
    cleanupMealPlanDragState();
    if (startOrder && finalOrder && startOrder !== finalOrder) {
      queueMealPlanSave(120);
    }
  });
}

function initIngredientNetwork() {
  const container = document.querySelector("[data-ingredient-network]");
  if (!(container instanceof HTMLElement)) {
    return;
  }

  const canvas = container.querySelector("[data-ingredient-network-canvas]");
  const fallback = container.querySelector("[data-ingredient-network-fallback]");
  const payloadNode = container.querySelector("[data-network-payload]");
  const controls = container.querySelector("[data-ingredient-network-controls]");
  const slider = container.querySelector("[data-ingredient-network-slider]");
  const sliderValue = container.querySelector("[data-ingredient-network-slider-value]");
  const hint = container.querySelector("[data-ingredient-network-hint]");
  if (!(canvas instanceof HTMLElement)) {
    return;
  }

  let payload = null;
  try {
    payload = JSON.parse(payloadNode?.textContent || "{}");
  } catch (error) {
    console.error(error);
  }

  const nodes = Array.isArray(payload?.nodes) ? payload.nodes : [];
  const links = Array.isArray(payload?.links) ? payload.links : [];
  if (nodes.length === 0 || links.length === 0) {
    canvas.hidden = true;
    if (controls instanceof HTMLElement) {
      controls.hidden = true;
    }
    if (fallback instanceof HTMLElement) {
      fallback.hidden = false;
    }
    return;
  }

  const previewNodeCount = nodes.length;
  const totalNodeCount = Math.max(Number(payload?.node_count || 0), previewNodeCount);
  const sliderMin = Math.min(
    previewNodeCount,
    Math.max(1, Number(payload?.slider_min_node_count || Math.min(previewNodeCount, 20))),
  );
  const sliderMax = Math.min(
    previewNodeCount,
    Math.max(sliderMin, Number(payload?.slider_max_node_count || previewNodeCount)),
  );
  const sliderStep = Math.max(1, Number(payload?.slider_step || (sliderMax <= 40 ? 1 : 5)));
  const defaultDisplayCount = Math.max(
    sliderMin,
    Math.min(sliderMax, Number(payload?.default_display_node_count || sliderMax)),
  );
  const tooltip = document.createElement("div");
  tooltip.className = "ingredient-network__tooltip";
  tooltip.hidden = true;
  container.appendChild(tooltip);
  let currentDisplayCount = defaultDisplayCount;
  let pendingFrame = null;
  let activeDrag = null;
  const svgNamespace = "http://www.w3.org/2000/svg";

  function escapeIngredientNetworkText(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function createSvgElement(tagName) {
    return document.createElementNS(svgNamespace, tagName);
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function hexToRgb(hex) {
    const normalized = String(hex).replace("#", "");
    const value = normalized.length === 3
      ? normalized.split("").map((part) => part + part).join("")
      : normalized;
    return {
      r: Number.parseInt(value.slice(0, 2), 16),
      g: Number.parseInt(value.slice(2, 4), 16),
      b: Number.parseInt(value.slice(4, 6), 16),
    };
  }

  function interpolateColor(startHex, endHex, t) {
    const start = hexToRgb(startHex);
    const end = hexToRgb(endHex);
    const amount = clamp(t, 0, 1);
    const r = Math.round(start.r + ((end.r - start.r) * amount));
    const g = Math.round(start.g + ((end.g - start.g) * amount));
    const b = Math.round(start.b + ((end.b - start.b) * amount));
    return `rgb(${r}, ${g}, ${b})`;
  }

  function createLinearScale(minValue, maxValue, minOutput, maxOutput) {
    if (minValue === maxValue) {
      return () => (minOutput + maxOutput) / 2;
    }
    return (value) => {
      const ratio = (value - minValue) / (maxValue - minValue);
      return minOutput + ((maxOutput - minOutput) * clamp(ratio, 0, 1));
    };
  }

  function createSqrtScale(minValue, maxValue, minOutput, maxOutput) {
    if (minValue === maxValue) {
      return () => (minOutput + maxOutput) / 2;
    }
    const minRoot = Math.sqrt(Math.max(minValue, 0));
    const maxRoot = Math.sqrt(Math.max(maxValue, 0));
    return (value) => {
      const root = Math.sqrt(Math.max(value, 0));
      const ratio = (root - minRoot) / Math.max(maxRoot - minRoot, 1e-6);
      return minOutput + ((maxOutput - minOutput) * clamp(ratio, 0, 1));
    };
  }

  function clampDisplayCount(value) {
    return Math.max(sliderMin, Math.min(sliderMax, Number(value || defaultDisplayCount)));
  }

  function selectedGraph(displayCount) {
    const limitedNodes = nodes.slice(0, displayCount);
    const nodeIds = new Set(limitedNodes.map((node) => node.id));
    const limitedLinks = links.filter((link) => nodeIds.has(link.source) && nodeIds.has(link.target));
    return { limitedNodes, limitedLinks };
  }

  function syncNetworkSummary(displayCount, edgeCount) {
    if (sliderValue instanceof HTMLElement) {
      sliderValue.textContent = String(displayCount);
    }
    if (hint instanceof HTMLElement) {
      hint.textContent = `Showing ${displayCount} of the top ${previewNodeCount} ranked ingredients and ${edgeCount} strong links from the wider ${totalNodeCount}-ingredient network. Drag nodes and hover for context.`;
    }
  }

  function layoutGraph(simulationNodes, simulationLinks, width, height, radiusScale) {
    const nodeCount = Math.max(simulationNodes.length, 1);
    const centerX = width / 2;
    const centerY = height / 2;
    const maxOrbit = Math.min(width, height) * 0.42;
    const ringGap = Math.max(36, Math.min(64, maxOrbit / Math.max(Math.ceil(Math.sqrt(nodeCount)), 1)));
    const sortedNodes = [...simulationNodes].sort((left, right) => (
      (right.weighted_degree || 0) - (left.weighted_degree || 0)
    ));

    let ringIndex = 0;
    let nodeIndex = 0;
    while (nodeIndex < sortedNodes.length) {
      const nodesInRing = ringIndex === 0 ? 1 : Math.min(sortedNodes.length - nodeIndex, 6 * ringIndex);
      const radius = ringIndex * ringGap;
      for (let index = 0; index < nodesInRing && nodeIndex < sortedNodes.length; index += 1) {
        const node = sortedNodes[nodeIndex];
        node.radius = radiusScale(node.weighted_degree || 1);
        if (ringIndex === 0) {
          node.x = centerX;
          node.y = centerY;
        } else {
          const angle = ((Math.PI * 2) / nodesInRing) * index - (Math.PI / 2) + (ringIndex * 0.14);
          node.x = centerX + (Math.cos(angle) * radius);
          node.y = centerY + (Math.sin(angle) * radius);
        }
        node.x = clamp(node.x, node.radius + 24, width - node.radius - 24);
        node.y = clamp(node.y, node.radius + 24, height - node.radius - 24);
        nodeIndex += 1;
      }
      ringIndex += 1;
    }
  }

  function render(displayCount) {
    activeDrag = null;
    tooltip.hidden = true;
    canvas.innerHTML = "";
    const width = Math.max(canvas.clientWidth || 0, 720);
    const height = Math.max(Math.round(width * 0.62), 520);
    const { limitedNodes, limitedLinks } = selectedGraph(displayCount);
    syncNetworkSummary(displayCount, limitedLinks.length);

    const simulationNodes = limitedNodes.map((node) => ({
      ...node,
      x: Number.isFinite(Number(node.x)) ? Number(node.x) : undefined,
      y: Number.isFinite(Number(node.y)) ? Number(node.y) : undefined,
      frequency: Number(node.frequency || 0),
      degree_centrality: Number(node.degree_centrality || 0),
      weighted_degree: Number(node.weighted_degree || 0),
      closeness_centrality: Number(node.closeness_centrality || 0),
      weighted_closeness: Number(node.weighted_closeness || 0),
    }));
    const simulationLinks = limitedLinks.map((link) => ({
      ...link,
      value: Number(link.value || 0),
    }));
    const weightValues = simulationNodes.map((node) => node.weighted_degree || 0);
    const frequencyValues = simulationNodes.map((node) => node.frequency || 0);
    const linkValues = simulationLinks.map((link) => link.value || 0);
    const minWeight = Math.max(Math.min(...weightValues), 1);
    const maxWeight = Math.max(Math.max(...weightValues), 1);
    const minFrequency = Math.max(Math.min(...frequencyValues), 1);
    const maxFrequency = Math.max(Math.max(...frequencyValues), 1);
    const minLink = linkValues.length > 0 ? Math.max(Math.min(...linkValues), 1) : 1;
    const maxLink = linkValues.length > 0 ? Math.max(Math.max(...linkValues), 1) : 1;
    const sizeScale = createSqrtScale(minWeight, maxWeight, 9, 28);
    const colorRatioScale = createLinearScale(minFrequency, maxFrequency, 0, 1);
    const strokeScale = createLinearScale(minLink, maxLink, 1.2, 5.2);

    const neighborMap = new Map();
    for (const node of simulationNodes) {
      neighborMap.set(node.id, new Set([node.id]));
    }
    for (const link of simulationLinks) {
      neighborMap.get(link.source)?.add(link.target);
      neighborMap.get(link.target)?.add(link.source);
    }

    layoutGraph(simulationNodes, simulationLinks, width, height, sizeScale);

    const svg = createSvgElement("svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("width", String(width));
    svg.setAttribute("height", String(height));
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    svg.setAttribute("class", "ingredient-network__svg");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", canvas.getAttribute("aria-label") || "Interactive ingredient network");
    canvas.appendChild(svg);

    const linkLayer = createSvgElement("g");
    linkLayer.setAttribute("class", "ingredient-network__links");
    svg.appendChild(linkLayer);

    const labelLayer = createSvgElement("g");
    labelLayer.setAttribute("class", "ingredient-network__labels");
    svg.appendChild(labelLayer);

    const nodeLayer = createSvgElement("g");
    nodeLayer.setAttribute("class", "ingredient-network__nodes");
    svg.appendChild(nodeLayer);

    const nodeElements = new Map();
    const labelElements = new Map();
    const linkElements = [];

    for (const edge of simulationLinks) {
      const line = createSvgElement("line");
      line.setAttribute("stroke", "#8b7555");
      line.setAttribute("stroke-opacity", "0.28");
      line.setAttribute("stroke-width", String(strokeScale(edge.value || 1)));
      lineLayer.appendChild(line);
      linkElements.push({ edge, element: line });
    }

    for (const item of simulationNodes) {
      const circle = createSvgElement("circle");
      circle.setAttribute("r", String(sizeScale(item.weighted_degree || 1)));
      circle.setAttribute("fill", interpolateColor("#d6c1a0", "#b15c2f", colorRatioScale(item.frequency || 1)));
      circle.setAttribute("fill-opacity", "0.92");
      circle.setAttribute("stroke", "#fffaf2");
      circle.setAttribute("stroke-width", "2.2");
      circle.style.cursor = "grab";
      nodeLayer.appendChild(circle);
      nodeElements.set(item.id, circle);

      const label = createSvgElement("text");
      label.textContent = item.label || item.id;
      label.setAttribute("font-size", sizeScale(item.weighted_degree || 1) >= 17 ? "14" : "12");
      label.setAttribute("font-weight", sizeScale(item.weighted_degree || 1) >= 20 ? "700" : "600");
      label.setAttribute("fill", "#3b3329");
      label.setAttribute("pointer-events", "none");
      labelLayer.appendChild(label);
      labelElements.set(item.id, label);
    }

    function syncPositions() {
      for (const { edge, element } of linkElements) {
        const source = simulationNodes.find((node) => node.id === edge.source);
        const target = simulationNodes.find((node) => node.id === edge.target);
        if (!source || !target) {
          continue;
        }
        element.setAttribute("x1", String(source.x));
        element.setAttribute("y1", String(source.y));
        element.setAttribute("x2", String(target.x));
        element.setAttribute("y2", String(target.y));
      }

      for (const item of simulationNodes) {
        const circle = nodeElements.get(item.id);
        const label = labelElements.get(item.id);
        if (circle) {
          circle.setAttribute("cx", String(item.x));
          circle.setAttribute("cy", String(item.y));
        }
        if (label) {
          label.setAttribute("x", String(item.x + item.radius + 8));
          label.setAttribute("y", String(item.y + 4));
        }
      }
    }

    function updateHighlight(activeNode = null) {
      const activeNeighbors = activeNode ? neighborMap.get(activeNode.id) || new Set([activeNode.id]) : null;
      for (const item of simulationNodes) {
        const opacity = !activeNeighbors || activeNeighbors.has(item.id) ? "1" : "0.2";
        nodeElements.get(item.id)?.setAttribute("opacity", opacity);
        labelElements.get(item.id)?.setAttribute("opacity", !activeNeighbors || activeNeighbors.has(item.id) ? "1" : "0.22");
      }
      for (const { edge, element } of linkElements) {
        if (!activeNeighbors || !activeNode) {
          element.setAttribute("stroke-opacity", "0.28");
          continue;
        }
        element.setAttribute("stroke-opacity", edge.source === activeNode.id || edge.target === activeNode.id ? "0.8" : "0.08");
      }
    }

    function showTooltip(event, item) {
      tooltip.hidden = false;
      tooltip.innerHTML = `
        <strong>${escapeIngredientNetworkText(item.label || item.id)}</strong>
        <span>Weighted connectivity: ${Math.round(item.weighted_degree || 0)}</span>
        <span>Recipe frequency: ${Math.round(item.frequency || 0)}</span>
        <span>Closeness: ${(item.closeness_centrality || 0).toFixed(3)}</span>
      `;
      const bounds = container.getBoundingClientRect();
      tooltip.style.left = `${event.clientX - bounds.left + 16}px`;
      tooltip.style.top = `${event.clientY - bounds.top + 16}px`;
    }

    function hideTooltip() {
      tooltip.hidden = true;
    }

    for (const item of simulationNodes) {
      const circle = nodeElements.get(item.id);
      if (!circle) {
        continue;
      }

      circle.addEventListener("mouseenter", (event) => {
        updateHighlight(item);
        showTooltip(event, item);
      });
      circle.addEventListener("mousemove", (event) => {
        showTooltip(event, item);
      });
      circle.addEventListener("mouseleave", () => {
        if (activeDrag?.id === item.id) {
          return;
        }
        updateHighlight();
        hideTooltip();
      });
      circle.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        activeDrag = {
          id: item.id,
          pointerId: event.pointerId,
        };
        circle.setPointerCapture(event.pointerId);
        circle.style.cursor = "grabbing";
        updateHighlight(item);
        showTooltip(event, item);
      });
      circle.addEventListener("pointermove", (event) => {
        if (!activeDrag || activeDrag.id !== item.id || activeDrag.pointerId !== event.pointerId) {
          return;
        }
        const bounds = svg.getBoundingClientRect();
        const scaleX = width / Math.max(bounds.width, 1);
        const scaleY = height / Math.max(bounds.height, 1);
        item.x = clamp((event.clientX - bounds.left) * scaleX, item.radius + 24, width - item.radius - 24);
        item.y = clamp((event.clientY - bounds.top) * scaleY, item.radius + 24, height - item.radius - 24);
        syncPositions();
        showTooltip(event, item);
      });
      circle.addEventListener("pointerup", (event) => {
        if (!activeDrag || activeDrag.id !== item.id || activeDrag.pointerId !== event.pointerId) {
          return;
        }
        activeDrag = null;
        circle.releasePointerCapture(event.pointerId);
        circle.style.cursor = "grab";
        hideTooltip();
        updateHighlight();
      });
      circle.addEventListener("pointercancel", (event) => {
        if (!activeDrag || activeDrag.id !== item.id || activeDrag.pointerId !== event.pointerId) {
          return;
        }
        activeDrag = null;
        circle.style.cursor = "grab";
        hideTooltip();
        updateHighlight();
      });
    }

    syncPositions();
    canvas.hidden = false;
    if (fallback instanceof HTMLElement) {
      fallback.hidden = true;
    }
  }

  function scheduleRender(nextDisplayCount) {
    currentDisplayCount = clampDisplayCount(nextDisplayCount);
    if (slider instanceof HTMLInputElement) {
      slider.value = String(currentDisplayCount);
    }
    if (pendingFrame !== null) {
      window.cancelAnimationFrame(pendingFrame);
    }
    pendingFrame = window.requestAnimationFrame(() => {
      pendingFrame = null;
      try {
        render(currentDisplayCount);
      } catch (error) {
        console.error(error);
        canvas.innerHTML = "";
        canvas.hidden = true;
        if (controls instanceof HTMLElement) {
          controls.hidden = false;
        }
        if (fallback instanceof HTMLElement) {
          fallback.hidden = false;
        }
      }
    });
  }

  if (controls instanceof HTMLElement) {
    controls.hidden = sliderMax <= sliderMin;
  }
  if (slider instanceof HTMLInputElement) {
    slider.min = String(sliderMin);
    slider.max = String(sliderMax);
    slider.step = String(sliderStep);
    slider.value = String(defaultDisplayCount);
    slider.addEventListener("input", () => {
      scheduleRender(slider.value);
    });
  }

  scheduleRender(defaultDisplayCount);

  if (typeof ResizeObserver === "function") {
    const resizeObserver = new ResizeObserver(() => {
      scheduleRender(currentDisplayCount);
    });
    resizeObserver.observe(canvas);
  }
}

function initIngredientNetworkD3() {
  const container = document.querySelector("[data-ingredient-network]");
  if (!(container instanceof HTMLElement)) {
    return;
  }

  const canvas = container.querySelector("[data-ingredient-network-canvas]");
  const fallback = container.querySelector("[data-ingredient-network-fallback]");
  const controls = container.querySelector("[data-ingredient-network-controls]");
  const slider = container.querySelector("[data-ingredient-network-slider]");
  const sliderValue = container.querySelector("[data-ingredient-network-slider-value]");
  const hint = container.querySelector("[data-ingredient-network-hint]");
  const payloadNode = container.querySelector("[data-network-payload]");
  if (!(canvas instanceof HTMLElement)) {
    return;
  }

  let payload = null;
  try {
    payload = JSON.parse(payloadNode?.textContent || "{}");
  } catch (error) {
    console.error(error);
  }

  const allNodes = Array.isArray(payload?.nodes) ? payload.nodes : [];
  const allLinks = Array.isArray(payload?.links) ? payload.links : [];
  if (typeof window.d3 !== "object" || allNodes.length === 0 || allLinks.length === 0) {
    canvas.hidden = true;
    if (controls instanceof HTMLElement) {
      controls.hidden = typeof window.d3 !== "object";
    }
    if (fallback instanceof HTMLElement) {
      fallback.hidden = false;
    }
    return;
  }

  const d3 = window.d3;
  const previewNodeCount = allNodes.length;
  const totalNodeCount = Math.max(Number(payload?.node_count || 0), previewNodeCount);
  const sliderMin = Math.min(
    previewNodeCount,
    Math.max(1, Number(payload?.slider_min_node_count || Math.min(previewNodeCount, 20))),
  );
  const sliderMax = Math.min(
    previewNodeCount,
    Math.max(sliderMin, Number(payload?.slider_max_node_count || previewNodeCount)),
  );
  const sliderStep = Math.max(1, Number(payload?.slider_step || (sliderMax <= 40 ? 1 : 5)));
  const defaultDisplayCount = Math.max(
    sliderMin,
    Math.min(sliderMax, Number(payload?.default_display_node_count || sliderMax)),
  );
  let currentDisplayCount = defaultDisplayCount;
  let activeSimulation = null;
  let pendingFrame = null;

  const tooltip = document.createElement("div");
  tooltip.className = "ingredient-network__tooltip";
  tooltip.hidden = true;
  container.appendChild(tooltip);

  function escapeIngredientNetworkText(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function clampDisplayCount(value) {
    return Math.max(sliderMin, Math.min(sliderMax, Number(value || defaultDisplayCount)));
  }

  function selectedGraph(displayCount) {
    const nodes = allNodes.slice(0, displayCount);
    const nodeIds = new Set(nodes.map((node) => node.id));
    const links = allLinks.filter((link) => nodeIds.has(link.source) && nodeIds.has(link.target));
    return { nodes, links };
  }

  function syncNetworkSummary(displayCount, edgeCount) {
    if (sliderValue instanceof HTMLElement) {
      sliderValue.textContent = String(displayCount);
    }
    if (hint instanceof HTMLElement) {
      hint.textContent = `Showing ${displayCount} of the top ${previewNodeCount} ranked ingredients and ${edgeCount} strong links from the wider ${totalNodeCount}-ingredient network. Drag nodes and hover for context.`;
    }
  }

  function render(displayCount) {
    activeSimulation?.stop();
    tooltip.hidden = true;
    canvas.innerHTML = "";

    const width = Math.max(canvas.clientWidth || 0, 720);
    const height = Math.max(Math.round(width * 0.62), 520);
    const graph = selectedGraph(displayCount);
    syncNetworkSummary(displayCount, graph.links.length);

    const nodes = graph.nodes.map((node) => ({
      ...node,
      x: Number.isFinite(Number(node.x)) ? Number(node.x) : undefined,
      y: Number.isFinite(Number(node.y)) ? Number(node.y) : undefined,
      frequency: Number(node.frequency || 0),
      degree_centrality: Number(node.degree_centrality || 0),
      weighted_degree: Number(node.weighted_degree || 0),
      closeness_centrality: Number(node.closeness_centrality || 0),
      weighted_closeness: Number(node.weighted_closeness || 0),
    }));
    const links = graph.links.map((link) => ({
      ...link,
      value: Number(link.value || 0),
    }));

    const weightExtent = d3.extent(nodes, (node) => node.weighted_degree);
    const sizeScale = d3
      .scaleSqrt()
      .domain([Math.max(weightExtent[0] || 1, 1), Math.max(weightExtent[1] || 1, 1)])
      .range([9, 28]);
    const frequencyExtent = d3.extent(nodes, (node) => node.frequency);
    const colorScale = d3
      .scaleLinear()
      .domain([Math.max(frequencyExtent[0] || 1, 1), Math.max(frequencyExtent[1] || 1, 1)])
      .range(["#d6c1a0", "#b15c2f"]);
    const linkExtent = d3.extent(links, (link) => link.value);
    const strokeScale = d3
      .scaleLinear()
      .domain([Math.max(linkExtent[0] || 1, 1), Math.max(linkExtent[1] || 1, 1)])
      .range([1.2, 5.2]);

    const neighborMap = new Map();
    for (const node of nodes) {
      neighborMap.set(node.id, new Set([node.id]));
    }
    for (const link of links) {
      neighborMap.get(link.source)?.add(link.target);
      neighborMap.get(link.target)?.add(link.source);
    }

    const svg = d3
      .select(canvas)
      .append("svg")
      .attr("viewBox", `0 0 ${width} ${height}`)
      .attr("class", "ingredient-network__svg")
      .attr("role", "img")
      .attr("aria-label", canvas.getAttribute("aria-label") || "Interactive ingredient network");
    const root = svg.append("g");

    svg.call(
      d3.zoom().scaleExtent([0.7, 2.8]).on("zoom", (event) => {
        root.attr("transform", event.transform);
      }),
    );

    const link = root
      .append("g")
      .attr("class", "ingredient-network__links")
      .selectAll("line")
      .data(links)
      .join("line")
      .attr("stroke", "#8b7555")
      .attr("stroke-opacity", 0.28)
      .attr("stroke-width", (edge) => strokeScale(edge.value));

    const node = root
      .append("g")
      .attr("class", "ingredient-network__nodes")
      .selectAll("circle")
      .data(nodes)
      .join("circle")
      .attr("r", (item) => sizeScale(item.weighted_degree || 1))
      .attr("fill", (item) => colorScale(item.frequency || 1))
      .attr("fill-opacity", 0.92)
      .attr("stroke", "#fffaf2")
      .attr("stroke-width", 2.2)
      .style("cursor", "grab");

    const labels = root
      .append("g")
      .attr("class", "ingredient-network__labels")
      .selectAll("text")
      .data(nodes)
      .join("text")
      .text((item) => item.label || item.id)
      .attr("font-size", (item) => (sizeScale(item.weighted_degree || 1) >= 17 ? 14 : 12))
      .attr("font-weight", (item) => (sizeScale(item.weighted_degree || 1) >= 20 ? 700 : 600))
      .attr("fill", "#3b3329")
      .style("pointer-events", "none");

    function updateHighlight(activeNode = null) {
      const activeNeighbors = activeNode ? neighborMap.get(activeNode.id) || new Set([activeNode.id]) : null;
      node.attr("opacity", (item) => (!activeNeighbors || activeNeighbors.has(item.id) ? 1 : 0.2));
      labels.attr("opacity", (item) => (!activeNeighbors || activeNeighbors.has(item.id) ? 1 : 0.22));
      link.attr("stroke-opacity", (edge) => {
        if (!activeNeighbors || !activeNode) {
          return 0.28;
        }
        const sourceId = typeof edge.source === "object" ? edge.source.id : edge.source;
        const targetId = typeof edge.target === "object" ? edge.target.id : edge.target;
        return sourceId === activeNode.id || targetId === activeNode.id ? 0.8 : 0.08;
      });
    }

    function showTooltip(event, item) {
      tooltip.hidden = false;
      tooltip.innerHTML = `
        <strong>${escapeIngredientNetworkText(item.label || item.id)}</strong>
        <span>Weighted connectivity: ${Math.round(item.weighted_degree || 0)}</span>
        <span>Recipe frequency: ${Math.round(item.frequency || 0)}</span>
        <span>Closeness: ${(item.closeness_centrality || 0).toFixed(3)}</span>
      `;
      const bounds = container.getBoundingClientRect();
      tooltip.style.left = `${event.clientX - bounds.left + 16}px`;
      tooltip.style.top = `${event.clientY - bounds.top + 16}px`;
    }

    function hideTooltip() {
      tooltip.hidden = true;
    }

    const drag = d3
      .drag()
      .on("start", (event, item) => {
        if (!event.active) {
          simulation.alphaTarget(0.24).restart();
        }
        item.fx = item.x;
        item.fy = item.y;
      })
      .on("drag", (event, item) => {
        item.fx = event.x;
        item.fy = event.y;
      })
      .on("end", (event, item) => {
        if (!event.active) {
          simulation.alphaTarget(0);
        }
        item.fx = null;
        item.fy = null;
      });

    node
      .call(drag)
      .on("mouseenter", (event, item) => {
        updateHighlight(item);
        showTooltip(event, item);
      })
      .on("mousemove", (event, item) => {
        showTooltip(event, item);
      })
      .on("mouseleave", () => {
        updateHighlight();
        hideTooltip();
      });

    const simulation = d3
      .forceSimulation(nodes)
      .force(
        "link",
        d3
          .forceLink(links)
          .id((item) => item.id)
          .distance((edge) => Math.max(44, 170 - (edge.value * 0.32)))
          .strength((edge) => Math.min(0.92, 0.16 + (edge.value / Math.max(linkExtent[1] || 1, 1)) * 0.58)),
      )
      .force("charge", d3.forceManyBody().strength((item) => -120 - (sizeScale(item.weighted_degree || 1) * 14)))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide().radius((item) => sizeScale(item.weighted_degree || 1) + 18))
      .force("x", d3.forceX(width / 2).strength(0.04))
      .force("y", d3.forceY(height / 2).strength(0.05))
      .on("tick", () => {
        link
          .attr("x1", (edge) => edge.source.x)
          .attr("y1", (edge) => edge.source.y)
          .attr("x2", (edge) => edge.target.x)
          .attr("y2", (edge) => edge.target.y);

        node
          .attr("cx", (item) => item.x)
          .attr("cy", (item) => item.y);

        labels
          .attr("x", (item) => item.x + sizeScale(item.weighted_degree || 1) + 8)
          .attr("y", (item) => item.y + 4);
      });

    activeSimulation = simulation;
    canvas.hidden = false;
    if (fallback instanceof HTMLElement) {
      fallback.hidden = true;
    }
  }

  function scheduleRender(nextDisplayCount) {
    currentDisplayCount = clampDisplayCount(nextDisplayCount);
    if (slider instanceof HTMLInputElement) {
      slider.value = String(currentDisplayCount);
    }
    if (pendingFrame !== null) {
      window.cancelAnimationFrame(pendingFrame);
    }
    pendingFrame = window.requestAnimationFrame(() => {
      pendingFrame = null;
      try {
        render(currentDisplayCount);
      } catch (error) {
        console.error(error);
        canvas.innerHTML = "";
        canvas.hidden = true;
        if (fallback instanceof HTMLElement) {
          fallback.hidden = false;
        }
      }
    });
  }

  if (controls instanceof HTMLElement) {
    controls.hidden = sliderMax <= sliderMin;
  }
  if (slider instanceof HTMLInputElement) {
    slider.min = String(sliderMin);
    slider.max = String(sliderMax);
    slider.step = String(sliderStep);
    slider.value = String(defaultDisplayCount);
    slider.addEventListener("input", () => {
      scheduleRender(slider.value);
    });
  }

  scheduleRender(defaultDisplayCount);

  if (typeof ResizeObserver === "function") {
    const resizeObserver = new ResizeObserver(() => {
      scheduleRender(currentDisplayCount);
    });
    resizeObserver.observe(canvas);
  }
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

initRecipeToggles();
initCookbookToc();
initMealPlanAutoLink();
initIngredientNetworkD3();
