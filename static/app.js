const state = {
  view: "words",
  groups: [],
  activeGroupId: null,
  posOptions: [],
  lessons: [],
  activeLessonKey: null,
  specialCategories: [],
  activeSpecialCategory: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "请求失败");
  }
  return data;
}

function mojiUrl(term) {
  return `https://www.mojidict.com/searchText/${encodeURIComponent(term)}`;
}

function setStatus(message) {
  $("#word-summary").textContent = message;
}

function showEmpty(container, message) {
  container.innerHTML = `<div class="empty">${message}</div>`;
}

function switchView(view) {
  state.view = view;
  $$(".nav-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  $$(".view").forEach((section) => {
    section.classList.toggle("active", section.id === `${view}-view`);
  });
}

function renderStars(container, difficulty, onChange) {
  container.innerHTML = "";
  for (let value = 1; value <= 5; value += 1) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `star-button${value <= difficulty ? " active" : ""}`;
    button.textContent = "★";
    button.title = `${value} 星`;
    button.addEventListener("click", () => onChange(value));
    container.append(button);
  }
}

function fillPosSelect(select, value) {
  select.innerHTML = '<option value="">词性</option>';
  state.posOptions.forEach((option) => {
    const item = document.createElement("option");
    item.value = option;
    item.textContent = option;
    item.selected = option === value;
    select.append(item);
  });
}

function wordMetaText(word) {
  const items = [];
  if (word.part_of_speech) items.push(word.part_of_speech);
  if (word.meaning) items.push(word.meaning);
  return items.join(" · ") || "缺少词义或词性，请补充";
}

function createWordCard(word, options = {}) {
  const template = $("#word-card-template");
  const card = template.content.firstElementChild.cloneNode(true);
  $(".term", card).textContent = word.term;
  $(".reading", card).textContent = word.reading || "";
  $(".moji-link", card).href = mojiUrl(word.term);
  $(".word-meta", card).textContent = wordMetaText(word);

  const posSelect = $(".pos-select", card);
  const meaningInput = $(".meaning-input", card);
  fillPosSelect(posSelect, word.part_of_speech || "");
  meaningInput.value = word.meaning || "";

  $(".save-word", card).addEventListener("click", async () => {
    const updated = await api(`/api/words/${word.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        part_of_speech: posSelect.value,
        meaning: meaningInput.value.trim(),
      }),
    });
    Object.assign(word, updated);
    $(".word-meta", card).textContent = wordMetaText(word);
  });

  const updateStars = async (difficulty) => {
    const updated = await api(`/api/words/${word.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ difficulty }),
    });
    Object.assign(word, updated);
    renderStars($(".stars", card), word.difficulty || 5, updateStars);
  };
  renderStars($(".stars", card), word.difficulty || 5, updateStars);

  const deleteButton = $(".delete-word", card);
  if (options.canDelete && state.activeGroupId) {
    deleteButton.addEventListener("click", async () => {
      await api(`/api/group-items/${state.activeGroupId}/${word.id}`, { method: "DELETE" });
      card.remove();
      await loadGroups();
    });
  } else {
    deleteButton.remove();
  }

  return card;
}

async function loadGroups() {
  const data = await api("/api/groups");
  state.groups = data.groups;
  if (!state.activeGroupId && state.groups.length) {
    state.activeGroupId = state.groups[0].id;
  }
  renderGroups();
}

function renderGroups() {
  const list = $("#group-list");
  list.innerHTML = "";
  state.groups.forEach((group) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `group-button${group.id === state.activeGroupId ? " active" : ""}`;
    button.innerHTML = `<span>${escapeHtml(group.name)}</span><span class="count">${group.word_count}</span>`;
    button.addEventListener("click", async () => {
      state.activeGroupId = group.id;
      renderGroups();
      await loadWords();
    });
    list.append(button);
  });
}

async function loadWords() {
  const container = $("#word-list");
  if (!state.activeGroupId) {
    showEmpty(container, "还没有词组");
    return;
  }
  const search = $("#word-search").value.trim();
  const data = await api(`/api/words?group_id=${state.activeGroupId}&search=${encodeURIComponent(search)}`);
  container.innerHTML = "";
  data.words.forEach((word) => container.append(createWordCard(word, { canDelete: true })));
  if (!data.words.length) showEmpty(container, "没有匹配的单词");
  const group = state.groups.find((item) => item.id === state.activeGroupId);
  setStatus(`${group?.name || ""} · ${data.words.length} 个单词，按难度从高到低展示`);
}

async function importWords(event) {
  event.preventDefault();
  const fileInput = $("#word-file");
  if (!fileInput.files.length) {
    alert("请选择 txt 文件");
    return;
  }
  const form = new FormData(event.currentTarget);
  const result = await api("/api/groups/import", {
    method: "POST",
    body: form,
  });
  state.activeGroupId = result.group_id;
  event.currentTarget.reset();
  await loadGroups();
  await loadWords();
}

async function loadLessons() {
  const data = await api("/api/grammar/lessons");
  state.lessons = data.lessons;
  state.activeLessonKey = state.activeLessonKey || state.lessons[0]?.category_key;
  renderLessons();
}

function renderLessons() {
  const list = $("#lesson-list");
  list.innerHTML = "";
  state.lessons.forEach((lesson) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `group-button${lesson.category_key === state.activeLessonKey ? " active" : ""}`;
    button.innerHTML = `<span>${escapeHtml(lesson.category_name)}</span><span class="count">${lesson.example_count}</span>`;
    button.addEventListener("click", () => {
      state.activeLessonKey = lesson.category_key;
      renderLessons();
      renderLessonDetail();
    });
    list.append(button);
  });
  renderLessonDetail();
}

function renderLessonDetail() {
  const lesson = state.lessons.find((item) => item.category_key === state.activeLessonKey);
  $("#lesson-detail").innerHTML = lesson ? lesson.description_html : "";
}

async function loadVerbs() {
  const data = await api("/api/verbs?limit=160");
  const shuffled = data.verbs.sort(() => Math.random() - 0.5).slice(0, 24);
  renderVerbs(shuffled);
}

function renderVerbs(verbs) {
  const container = $("#verb-list");
  container.innerHTML = "";
  verbs.forEach((verb) => {
    const card = document.createElement("article");
    card.className = "item-card verb-card";
    card.innerHTML = `
      <div class="card-main">
        <div>
          <h3 class="term">${escapeHtml(verb.dictionary_form)}</h3>
          <p class="reading">${escapeHtml(verb.verb_type)} · ${escapeHtml(verb.meaning || "")} · ${escapeHtml(verb.lesson || "")}</p>
        </div>
        <button type="button">展开</button>
      </div>
      <div class="verb-forms">
        ${[
          ["ます形", verb.masu_form],
          ["て形", verb.te_form],
          ["た形", verb.ta_form],
          ["ない形", verb.nai_form],
          ["被动形", verb.passive_form],
          ["使役形", verb.causative_form],
          ["使役被动", verb.causative_passive],
          ["可能形", verb.potential_form],
          ["意志形", verb.volitional_form],
          ["ば形", verb.ba_form],
        ]
          .map(([label, value]) => `<div class="form-pill"><b>${escapeHtml(label)}</b>${escapeHtml(value || "-")}</div>`)
          .join("")}
      </div>
    `;
    card.addEventListener("click", () => {
      card.classList.toggle("revealed");
      $("button", card).textContent = card.classList.contains("revealed") ? "收起" : "展开";
    });
    container.append(card);
  });
}

async function loadSpecialCategories() {
  const data = await api("/api/special/categories");
  state.specialCategories = data.categories;
  state.activeSpecialCategory = state.activeSpecialCategory || state.specialCategories[0]?.category;
  renderSpecialCategories();
}

function renderSpecialCategories() {
  const list = $("#special-category-list");
  list.innerHTML = "";
  state.specialCategories.forEach((category) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `group-button${category.category === state.activeSpecialCategory ? " active" : ""}`;
    button.innerHTML = `<span>${escapeHtml(category.category)}</span><span class="count">${category.item_count}</span>`;
    button.addEventListener("click", async () => {
      state.activeSpecialCategory = category.category;
      renderSpecialCategories();
      await loadSpecialItems();
    });
    list.append(button);
  });
}

async function loadSpecialItems() {
  const container = $("#special-list");
  const category = encodeURIComponent(state.activeSpecialCategory || "");
  const data = await api(`/api/special?category=${category}`);
  container.innerHTML = "";
  data.items.forEach((item) => {
    const vocab = item.vocab || {
      id: null,
      term: item.expression,
      reading: item.reading,
      meaning: item.meaning,
      part_of_speech: "",
      difficulty: 5,
    };
    const card = document.createElement("article");
    card.className = "item-card special-card";
    card.innerHTML = `
      <div class="card-main">
        <div>
          <h3 class="term">${escapeHtml(item.expression)}</h3>
          <p class="reading">${escapeHtml(item.reading || "")}</p>
        </div>
        <a class="moji-link" href="${mojiUrl(item.expression)}" target="_blank" rel="noreferrer">MOJi</a>
      </div>
      <div class="word-meta">${escapeHtml(wordMetaText(vocab))}</div>
      <div class="special-example">
        <div class="jp">${escapeHtml(item.example_jp || "")}</div>
        <div class="reading">${escapeHtml(item.example_cn || "")}</div>
      </div>
      <div class="special-notes">${escapeHtml(item.notes || "")}</div>
      <div class="edit-row">
        <select class="pos-select"></select>
        <input class="meaning-input" type="text" placeholder="词义" value="${escapeHtml(vocab.meaning || "")}">
        <button class="save-word" type="button">保存</button>
      </div>
      <div class="card-actions">
        <div class="stars"></div>
      </div>
    `;
    fillPosSelect($(".pos-select", card), vocab.part_of_speech || "");
    $(".save-word", card).addEventListener("click", async () => {
      if (!vocab.id) return;
      const updated = await api(`/api/words/${vocab.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meaning: $(".meaning-input", card).value.trim(),
          part_of_speech: $(".pos-select", card).value,
        }),
      });
      Object.assign(vocab, updated);
      $(".word-meta", card).textContent = wordMetaText(vocab);
    });
    const updateSpecialStars = async (difficulty) => {
      if (!vocab.id) return;
      const updated = await api(`/api/words/${vocab.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ difficulty }),
      });
      Object.assign(vocab, updated);
      renderStars($(".stars", card), vocab.difficulty || 5, updateSpecialStars);
    };
    renderStars($(".stars", card), vocab.difficulty || 5, updateSpecialStars);
    container.append(card);
  });
  if (!data.items.length) showEmpty(container, "这个分类暂无内容");
}

function bindEvents() {
  $$(".nav-button").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
  $("#import-form").addEventListener("submit", importWords);
  $("#refresh-words").addEventListener("click", loadWords);
  $("#word-search").addEventListener("input", () => {
    clearTimeout(window.wordSearchTimer);
    window.wordSearchTimer = setTimeout(loadWords, 220);
  });
  $("#shuffle-verbs").addEventListener("click", loadVerbs);
}

async function boot() {
  bindEvents();
  const posData = await api("/api/pos-options");
  state.posOptions = posData.options;
  await loadGroups();
  await loadWords();
  await loadLessons();
  await loadVerbs();
  await loadSpecialCategories();
  await loadSpecialItems();
}

boot().catch((error) => {
  console.error(error);
  alert(error.message);
});
