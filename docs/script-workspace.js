import { byId, button, element, feedback, icon, setOptions, timecode } from "./studio-ui.js";

const PLATFORMS = { youtube: "YouTube", shorts: "YouTube Shorts", tiktok: "TikTok", reels: "Instagram Reels",
  rutube: "RuTube", vk: "VK", dzen: "Дзен", general: "Любая площадка" };
const STYLES = { sketch: "Скетч", realism: "Реализм" };

export function setupScripts(api) {
  const page = byId("scripts-main");
  const notice = byId("script-feedback");
  const dialog = byId("script-new-dialog");
  const frameDialog = byId("script-frame-dialog");
  const message = byId("script-message");
  const content = byId("script-content");
  const notes = byId("script-notes");
  const marked = byId("script-marked-text");
  const versions = byId("script-versions");
  const boardVersion = byId("script-board-version");
  const boards = byId("script-boards");
  let id = "";
  let project;
  let projects = [];
  let versionNumber;
  let boardId = "";
  let panel = "chat";
  let marking = false;
  let selection;
  let frame;
  let textDirty = false;
  let notesDirty = false;
  let submitting = false;
  let revision = 0;
  let poll;
  let outputFolder;
  let editingText = false;
  const fail = (error) => feedback(notice, error.message, true);
  const path = () => "scripts/" + id;
  const version = (number = versionNumber) => project?.versions.find((item) => item.number === Number(number));
  const board = () => project?.boards.find((item) => item.id === boardId);
  const imageSource = (name) => "/script-media/" + id + "/" + name;

  function section(next) {
    panel = next;
    for (const tab of page.querySelectorAll("[data-script-panel]")) {
      const selected = tab.dataset.scriptPanel === next;
      tab.setAttribute("aria-pressed", String(selected));
      byId("script-" + tab.dataset.scriptPanel).hidden = !selected;
    }
    if (next === "chat" && !project?.busy) message.focus({ preventScroll: true });
  }

  function list() {
    const root = byId("script-projects");
    const key = JSON.stringify([id, projects]);
    if (root.dataset.key === key) return;
    root.dataset.key = key;
    root.replaceChildren(...projects.map((item) => {
      const li = element("li");
      const card = button("", () => open(item.id).catch(fail), "secondary");
      card.append(icon("chat"), element("strong", item.title));
      card.title = item.title;
      card.setAttribute("aria-current", String(item.id === id));
      li.append(card);
      return li;
    }));
  }

  async function refresh() {
    clearTimeout(poll);
    if (page.hidden) return;
    const token = ++revision;
    try {
      const result = await api("scripts");
      if (token !== revision || page.hidden) return;
      projects = result.projects;
      if (!id && projects.length) id = projects[0].id;
      list();
      if (id) {
        const data = await api(path());
        if (token !== revision || page.hidden) return;
        render(data);
      }
    } catch (error) {
      if (token === revision && !page.hidden) fail(error);
    }
    if (!page.hidden && token === revision) poll = setTimeout(refresh, project?.busy ? 900 : 5500);
  }

  async function preserveEdits() {
    if (textDirty) await saveText();
    if (notesDirty) await saveNotes();
  }

  async function open(key) {
    if (id !== key) {
      if (id) await preserveEdits();
      id = key;
      project = undefined;
      versionNumber = undefined;
      boardId = "";
      marking = false;
      editingText = false;
      selection = undefined;
      message.value = "";
      content.value = "";
      notes.value = "";
      feedback(notice);
      section("chat");
    }
    await refresh();
  }

  async function openVersion(number, manual = false) {
    if (textDirty) await saveText();
    versionNumber = Number(number);
    marking = manual;
    editingText = false;
    selection = undefined;
    renderVersion(true);
    section("text");
  }

  function render(data) {
    const previous = project;
    const newest = data.versions.at(-1);
    const older = previous?.versions.at(-1);
    project = data;
    if (!version() || (!marking && !textDirty && panel === "chat" && versionNumber === older?.number && newest?.number !== older?.number)) {
      versionNumber = newest?.number;
    }
    if (!board()) boardId = project.boards.at(-1)?.id || "";
    byId("script-empty").hidden = true;
    byId("script-work").hidden = false;
    byId("script-title").textContent = project.title;
    byId("script-spec").textContent = [PLATFORMS[project.platform],
      project.mode === "short" ? "Короткое видео" : "Длинное видео", timecode(project.seconds)].join(" · ");
    byId("script-chat-progress").value = project.busy ? project.stage : "";
    byId("script-board-note").textContent = project.busy ? project.stage : "";
    if (project.error) feedback(notice, project.error, true);
    else if (previous?.busy) feedback(notice);
    byId("script-chat-form").querySelector("button").disabled = project.busy || submitting;
    byId("script-save-notes").disabled = project.busy;
    if (!notesDirty && (previous?.id !== project.id || previous.notes !== project.notes)) notes.value = project.notes;
    drawMessages();
    renderVersion();
    renderBoard();
    if (previous?.id !== project.id) {
      byId("script-source-list").replaceChildren(...project.sources.map((source) => {
        const li = element("li");
        const link = element("a", source.title + (source.year ? " · " + source.year : ""));
        link.href = source.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        li.append(link);
        return li;
      }));
    }
  }

  function drawMessages() {
    const messages = byId("script-messages");
    const key = JSON.stringify([project.id, project.messages.map(({ text, version, kind }) => [text, version, kind])]);
    if (messages.dataset.key === key) return;
    const atBottom = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 110;
    messages.dataset.key = key;
    messages.replaceChildren(...project.messages.map((item) => {
      const row = element("article", "", "script-message");
      row.dataset.role = item.role;
      if (item.role === "user") { row.append(element("p", item.text)); return row; }
      const body = element("div", "", "assistant-content");
      const author = element("span", "", "assistant-mark");
      author.append(icon("spark"));
      body.append(element("strong", item.author ? "Вы" : "Qwen"), element("p", item.text));
      if (item.version) {
        const versionCard = button("", () => openVersion(item.version).catch(fail), "version-card");
        const label = element("span");
        label.append(element("b", "Версия " + item.version), element("small", "Читать сценарий целиком"));
        versionCard.append(icon("file"), label, icon("chevron"));
        body.append(versionCard);
      } else if (item.legacy) {
        const old = element("details");
        old.append(element("summary", "Сообщение из старой версии"), element("p", item.legacy));
        body.append(old);
      }
      const copy = button("", () => navigator.clipboard.writeText(item.text).catch(fail));
      copy.classList.add("ui-tool");
      copy.setAttribute("aria-label", "Копировать ответ");
      copy.title = "Копировать ответ";
      copy.append(icon("copy"));
      body.append(copy);
      row.append(author, body);
      return row;
    }));
    if (!project.messages.length) {
      const starter = element("div", "", "script-empty-chat");
      starter.append(element("h3", "Что снимаем?"),
        element("p", "Опишите действие, героев и тон. Можно начать с темы проекта.", "studio-note"),
        button("Написать по теме проекта", () => send(true).catch(fail), "secondary"));
      messages.append(starter);
    }
    if (atBottom || submitting) messages.scrollTop = messages.scrollHeight;
  }

  function renderVersion(force = false) {
    const entries = [...project.versions].reverse().map((item) => [String(item.number), "Версия " + item.number]);
    setOptions(versions, entries, String(versionNumber));
    if (versionNumber) versions.value = String(versionNumber);
    byId("script-chat-base").value = versionNumber ? "Основа · версия " + versionNumber : "";
    setOptions(boardVersion, entries, boardVersion.value || String(versionNumber));
    const current = version();
    const attached = board();
    const manual = current && marking && attached?.mode === "manual" && attached.version === current.number;
    byId("script-edit-area").hidden = !current || manual || !editingText;
    byId("script-paper").hidden = !current || manual || editingText;
    byId("script-toggle-edit").hidden = !current || manual;
    byId("script-toggle-edit").textContent = editingText ? "Просмотр" : "Редактировать";
    byId("script-mark-area").hidden = !manual;
    byId("script-text-empty").hidden = Boolean(current);
    byId("script-copy").disabled = byId("script-export-text").disabled = !current;
    byId("script-save-text").disabled = project.busy || !textDirty;
    const editKey = project.id + "/" + current?.number;
    const paper = byId("script-paper");
    if (paper.dataset.version !== editKey) {
      paper.dataset.version = editKey;
      const heading = element("div", "", "document-heading");
      if (current) heading.append(element("span", "Версия " + current.number), element("h2", project.title));
      paper.replaceChildren(heading, ...((current?.text || "").trim().split(/\n\s*\n/).filter(Boolean)
        .map((paragraph) => element("p", paragraph))));
    }
    if ((force || content.dataset.version !== editKey) && !textDirty) {
      content.value = current?.text || "";
      content.dataset.version = editKey;
    }
    if (manual) drawMarked(current, attached);
  }

  function drawMarked(current, attached) {
    const key = JSON.stringify([current.number, attached.id, attached.shots.map(({ start, end }) => [start, end])]);
    if (marked.dataset.key === key) return;
    marked.dataset.key = key;
    const stream = Array.from(current.text);
    marked.replaceChildren();
    let offset = 0;
    attached.shots.forEach((shot, index) => {
      if (!Number.isInteger(shot.start) || !Number.isInteger(shot.end)) return;
      const block = element("mark", stream.slice(shot.start, shot.end).join(""));
      block.dataset.frame = index + 1;
      marked.append(document.createTextNode(stream.slice(offset, shot.start).join("")), block);
      offset = shot.end;
    });
    marked.append(document.createTextNode(stream.slice(offset).join("")));
  }

  function renderBoard() {
    const entries = [...project.boards].reverse().map((item) => [item.id,
      `Вариант ${item.number} · ${STYLES[item.style]}${item.version ? " · сценарий " + item.version : ""}`]);
    setOptions(boards, entries, boardId);
    if (boardId) boards.value = boardId;
    const active = board();
    const working = project.busy || submitting;
    byId("script-board-auto").disabled = byId("script-board-manual").disabled = working || !project.versions.length;
    byId("script-board-current").hidden = !active;
    if (!active) return;
    byId("script-board-info").textContent = `Вариант ${active.number} · ${active.mode === "manual" ? "Ручная" : "Автоматическая"} · ${STYLES[active.style]}`;
    byId("script-board-reference").disabled = working;
    byId("script-board-source").disabled = !active.version;
    byId("script-board-source").textContent = active.mode === "manual" ? "Открыть сценарий и разметку" : "Открыть сценарий";
    byId("script-board-render").disabled = working || (active.mode === "manual" && !active.shots.length) ||
      (active.shots.length > 0 && active.shots.every((shot) => Boolean(shot.image)));
    byId("script-board-empty").hidden = Boolean(active.shots.length);
    const root = byId("script-shots");
    root.dataset.mode = project.mode;
    const key = JSON.stringify([active.id, active.shots]);
    if (root.dataset.key === key) return;
    root.dataset.key = key;
    root.replaceChildren(...active.shots.map((shot, index) => {
      const tile = button("", () => openFrame(shot, index), "secondary");
      tile.classList.add("script-shot", "shot-card-button");
      const label = `Кадр ${index + 1}` + (Number.isFinite(shot.time) ? ` · ${timecode(shot.time)}` : "");
      const preview = element("div", shot.image ? "" : "Ожидает генерации", "shot-preview");
      if (shot.image) {
        const image = element("img");
        image.src = imageSource(shot.image);
        image.alt = label;
        image.loading = "lazy";
        preview.append(image);
      }
      tile.append(element("strong", label), preview,
        element("small", (shot.visual || shot.instruction || shot.source || "").slice(0, 160)));
      return tile;
    }));
  }

  async function send(usingTopic = false) {
    if (!id || project?.busy || submitting) return;
    const text = message.value.trim();
    if (!usingTopic && !text) {
      message.focus();
      return;
    }
    submitting = true;
    byId("script-chat-form").querySelector("button").disabled = true;
    try {
      await preserveEdits();
      await api(path(), "POST", { mode: !usingTopic && byId("script-discussion").checked ? "discussion" : "draft",
        text, version: versionNumber });
      message.value = "";
      await refresh();
    } finally {
      submitting = false;
      byId("script-chat-form").querySelector("button").disabled = Boolean(project?.busy);
    }
  }

  async function saveText() {
    if (!textDirty) return;
    const previous = content.value;
    const saved = await api(path() + "/edit", "POST", { content: previous, version: versionNumber });
    textDirty = content.value !== previous;
    versionNumber = saved.versions.at(-1)?.number;
    render(saved);
    feedback(notice, "Версия " + versionNumber + " сохранена.");
  }

  async function saveNotes() {
    const previous = notes.value;
    const saved = await api(path() + "/edit", "POST", { notes: previous });
    notesDirty = notes.value !== previous;
    render(saved);
  }

  async function action(data) {
    if (submitting) return;
    submitting = true;
    try {
      await preserveEdits();
      const saved = await api(path() + "/boards", "POST", { board: boardId, ...data });
      boardId = saved.board;
      render(saved.project);
      await refresh();
    } finally {
      submitting = false;
      if (project) render(project);
    }
  }

  async function createBoard(mode) {
    const source = Number(boardVersion.value || versionNumber);
    await action({ action: mode, style: byId("script-board-style").value, version: source });
    if (mode === "manual") await openVersion(source, true);
  }

  function markSelection() {
    if (byId("script-mark-area").hidden || panel !== "text") return;
    const current = document.getSelection();
    if (!current?.rangeCount || current.isCollapsed) return;
    const range = current.getRangeAt(0);
    if (!marked.contains(range.startContainer) || !marked.contains(range.endContainer)) return;
    const preceding = document.createRange();
    preceding.selectNodeContents(marked);
    preceding.setEnd(range.startContainer, range.startOffset);
    const start = Array.from(preceding.toString()).length;
    const length = Array.from(range.toString()).length;
    selection = { start, end: start + length, source: range.toString() };
    const add = byId("script-mark-add");
    const overlap = board()?.shots.some((shot) => start < shot.end && start + length > shot.start);
    add.disabled = !length || length > 2500 || overlap || Boolean(project?.busy);
    byId("script-mark-status").textContent = overlap ? "Этот фрагмент уже отмечен. Выделите другой."
      : length > 2500 ? "Выделите фрагмент длиной до 2500 символов." : `Выделено символов: ${length}`;
  }

  function openFrame(shot, index) {
    frame = shot ? { shot } : { selection };
    const source = shot?.image;
    const img = byId("script-frame-image");
    img.hidden = !source;
    if (source) img.src = imageSource(source);
    else img.removeAttribute("src");
    byId("script-frame-title").textContent = shot ? `Кадр ${index + 1}` : "Новый кадр";
    byId("script-frame-text").textContent = shot ? (shot.visual || shot.source || "") : (selection?.source || "");
    byId("script-frame-instruction").value = shot?.instruction || "";
    byId("script-frame-error").textContent = "";
    byId("script-frame-remove").hidden = !shot;
    byId("script-frame-remove").disabled = project.busy;
    byId("script-frame-save").hidden = !shot;
    byId("script-frame-save").disabled = project.busy;
    const submit = byId("script-frame-submit");
    submit.textContent = !shot ? "Добавить кадр" : source ? "Перегенерировать" : "Сгенерировать кадр";
    submit.disabled = project.busy;
    frameDialog.showModal();
  }

  async function changeFrame(onlySave) {
    const instruction = byId("script-frame-instruction").value;
    if (frame?.shot) {
      await action({ action: onlySave ? "edit" : "regenerate", shot: frame.shot.id, instruction });
    } else if (frame?.selection) {
      const { start, end } = frame.selection;
      await action({ action: "add", start, end, instruction });
      selection = undefined;
      byId("script-mark-add").disabled = true;
      byId("script-mark-status").textContent = "Кадр добавлен. Можно выделить следующий фрагмент.";
    }
    frameDialog.close();
  }

  async function exportText() {
    if (textDirty) await saveText();
    const folder = await window.chooseVideoportFolder(outputFolder || "");
    if (!folder) return;
    outputFolder = folder;
    const result = await api(path() + "/export", "POST", { folder, kind: "txt", version: versionNumber });
    feedback(notice, "Сохранён файл " + result.name);
    notice.append(" ", button("Открыть папку", () => window.openVideoportDownloads(folder).catch(fail)));
  }

  function newProject() {
    byId("script-new-status").textContent = "";
    dialog.showModal();
  }

  byId("script-new").addEventListener("click", newProject);
  byId("script-empty-new").addEventListener("click", newProject);
  byId("script-format").addEventListener("change", () => {
    const long = byId("script-format").value === "long";
    byId("script-platform").value = long ? "youtube" : "shorts";
    byId("script-seconds").value = long ? "480" : "45";
  });
  byId("script-new-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.submitter;
    submit.disabled = true;
    try {
      const saved = await api("scripts", "POST", { title: byId("script-topic").value,
        platform: byId("script-platform").value, mode: byId("script-format").value,
        seconds: byId("script-seconds").value, audience: byId("script-audience").value });
      dialog.close();
      await open(saved.id);
    } catch (error) {
      byId("script-new-status").textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });

  for (const tab of page.querySelectorAll("[data-script-panel]")) tab.addEventListener("click", () => section(tab.dataset.scriptPanel));
  byId("script-chat-form").addEventListener("submit", (event) => { event.preventDefault(); send().catch(fail); });
  byId("script-mode").addEventListener("change", (event) => {
    byId("script-discussion").checked = event.target.value === "discuss";
  });
  message.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    if (!project?.busy) send().catch(fail);
  });
  byId("script-toggle-edit").addEventListener("click", async () => {
    try {
      if (editingText && textDirty) await saveText();
      editingText = !editingText;
      renderVersion();
      if (editingText) content.focus();
    } catch (error) { fail(error); }
  });
  message.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send().catch(fail);
    }
  });
  versions.addEventListener("change", () => openVersion(versions.value).catch(fail));
  content.addEventListener("input", () => { textDirty = true; byId("script-save-text").disabled = project.busy; });
  notes.addEventListener("input", () => { notesDirty = true; });
  byId("script-save-text").addEventListener("click", () => saveText().catch(fail));
  byId("script-save-notes").addEventListener("click", () => saveNotes().then(() => feedback(notice, "Заметки сохранены.")).catch(fail));
  byId("script-copy").addEventListener("click", () => navigator.clipboard.writeText(textDirty ? content.value : version().text)
    .then(() => feedback(notice, "Сценарий скопирован.")).catch(fail));
  byId("script-export-text").addEventListener("click", () => exportText().catch(fail));
  boards.addEventListener("change", () => { boardId = boards.value; marking = false; renderBoard(); });
  byId("script-board-auto").addEventListener("click", () => createBoard("auto").catch(fail));
  byId("script-board-manual").addEventListener("click", () => createBoard("manual").catch(fail));
  byId("script-board-source").addEventListener("click", () => openVersion(board().version, board().mode === "manual").catch(fail));
  byId("script-board-render").addEventListener("click", () => action({ action: "render" }).catch(fail));
  document.addEventListener("selectionchange", markSelection);
  byId("script-mark-add").addEventListener("click", () => { if (selection) openFrame(); });
  const frameError = (error) => { byId("script-frame-error").textContent = error.message; };
  byId("script-frame-form").addEventListener("submit", (event) => { event.preventDefault(); changeFrame(false).catch(frameError); });
  byId("script-frame-save").addEventListener("click", () => changeFrame(true).catch(frameError));
  byId("script-frame-remove").addEventListener("click", () =>
    action({ action: "remove", shot: frame.shot.id }).then(() => frameDialog.close()).catch(frameError));
  byId("script-board-reference").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file || !board()) return;
    const form = new FormData();
    form.append("image", file);
    try {
      const response = await fetch("/api/" + path() + "/boards/" + boardId + "/reference",
        { method: "POST", body: form, headers: { "X-Videoport": "1" } });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Не удалось открыть референс.");
      render(result);
      feedback(notice, "Референс для варианта сохранён.");
    } catch (error) { fail(error); }
  });
  byId("script-open-notes").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      notes.value = (await file.slice(0, 50000).text()).slice(0, 12000);
      notesDirty = true;
    } catch (error) { fail(error); }
    finally { event.target.value = ""; }
  });
  byId("script-chat-attach").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      const appended = (await file.slice(0, 50000).text()).trim();
      notes.value = [notes.value, appended].filter(Boolean).join("\n\n").slice(0, 12000);
      notesDirty = true;
      message.focus();
      feedback(notice, "Заметки добавлены к сценарию.");
    } catch (error) { fail(error); }
    finally { event.target.value = ""; }
  });

  return { tab: byId("open-scripts"), page, show: refresh, hide() { ++revision; clearTimeout(poll); } };
}
