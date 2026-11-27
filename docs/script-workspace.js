import { byId, button, element, feedback, timecode } from "./studio-ui.js";

const PLATFORMS = { youtube: "YouTube", shorts: "YouTube Shorts", tiktok: "TikTok", reels: "Instagram Reels",
  rutube: "RuTube", vk: "VK", dzen: "Дзен", general: "Любая площадка" };
const SHOTS = { wide: "Общий", medium: "Средний", close: "Крупный", detail: "Деталь", screen: "Запись экрана" };
const FIELDS = [["visual", "Что в кадре", 800], ["camera", "Ракурс и движение", 200], ["voice", "Реплика / голос", 1000],
  ["sound", "Звук", 300], ["overlay", "Надпись", 300], ["prompt", "Промпт для изображения", 550]];

export function setupScripts(api) {
  const page = byId("scripts-main");
  const notice = byId("script-feedback");
  const dialog = byId("script-new-dialog");
  const message = byId("script-message");
  const content = byId("script-content");
  const notes = byId("script-notes");
  let id = "";
  let project;
  let projects = [];
  let shots = [];
  let pending;
  let boardDirty = false;
  let textDirty = false;
  let notesDirty = false;
  let revision = 0;
  let poll;
  let outputFolder;
  const fail = (error) => feedback(notice, error.message, true);
  const path = () => "scripts/" + id;

  function section(panel) {
    for (const tab of page.querySelectorAll("[data-script-panel]")) {
      const selected = tab.dataset.scriptPanel === panel;
      tab.setAttribute("aria-pressed", String(selected));
      byId("script-" + tab.dataset.scriptPanel).hidden = !selected;
    }
  }

  function list() {
    const root = byId("script-projects");
    const key = JSON.stringify([id, projects]);
    if (root.dataset.key === key) return;
    root.dataset.key = key;
    root.replaceChildren(...projects.map((item) => {
      const li = element("li");
      const card = button("", () => open(item.id).catch(fail), "secondary");
      card.append(element("strong", item.title));
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
      list();
      if (id) {
        const data = await api(path());
        if (token !== revision || page.hidden) return;
        render(data);
        if (pending && !data.busy) {
          if (pending === "board" && !data.error) section("board");
          else if (pending === "draft" && !data.error) section("text");
          pending = null;
        }
      }
    } catch (error) {
      if (token === revision && !page.hidden) fail(error);
    }
    if (!page.hidden && token === revision) poll = setTimeout(refresh, project?.busy ? 950 : 5500);
  }

  async function open(key) {
    if (id !== key) {
      if (id) {
        if (textDirty) await saveText();
        if (notesDirty) await saveNotes();
        if (boardDirty) await saveBoard();
      }
      id = key;
      project = null;
      pending = null;
      shots = [];
      message.value = "";
      section("chat");
      feedback(notice);
    }
    await refresh();
  }

  function render(result) {
    const previous = project;
    project = result;
    const busy = Boolean(project.busy);
    byId("script-empty").hidden = true;
    byId("script-work").hidden = false;
    byId("script-title").textContent = project.title;
    byId("script-spec").textContent = [PLATFORMS[project.platform],
      project.mode === "short" ? "Короткое видео" : "Длинное видео", timecode(project.seconds)].join(" · ");
    if (busy || project.error) feedback(notice, project.stage || project.error, Boolean(project.error));
    else if (previous?.busy) feedback(notice);
    byId("script-draft").disabled = busy;
    byId("script-board-generate").disabled = busy || (!project.latest && !textDirty);
    byId("script-chat-form").querySelector("button").disabled = busy;
    for (const name of ["script-save-text", "script-save-notes", "script-save-board", "script-add-shot"]) byId(name).disabled = busy;
    if (!textDirty && previous?.latest !== project.latest) content.value = project.latest;
    if (!notesDirty && previous?.notes !== project.notes) notes.value = project.notes;
    if (!boardDirty && JSON.stringify(previous?.shots) !== JSON.stringify(project.shots)) {
      shots = structuredClone(project.shots);
      paintShots();
    }
    const messages = byId("script-messages");
    const key = JSON.stringify([project.id, project.messages]);
    if (messages.dataset.key !== key) {
      messages.dataset.key = key;
      messages.replaceChildren(...project.messages.map(({ role, text, kind }) => {
        const row = element("article", "", "script-message");
        row.dataset.role = role;
        row.append(element("strong", role === "assistant" ? "Qwen" : "Вы"), document.createTextNode(text));
        if (role === "assistant" && kind === "script") {
          const actions = element("div", "", "studio-row");
          actions.append(button("В текст сценария", async () => {
            if (project.busy) return;
            content.value = text;
            textDirty = true;
            try { await saveText(); section("text"); } catch (error) { fail(error); }
          }));
          row.append(actions);
        }
        return row;
      }));
      if (!project.messages.length) {
        messages.append(element("p", "Начни с черновика или попроси Qwen предложить идеи, структуру и варианты хука.", "studio-note"));
      }
      messages.scrollTop = messages.scrollHeight;
    }
    if (previous?.id !== project.id) {
      byId("script-source-list").replaceChildren(...project.sources.map((source) => {
        const li = element("li");
        const link = element("a", source.title);
        link.href = source.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        li.append(link);
        return li;
      }));
    }
  }

  async function send(mode) {
    if (!id || project?.busy) return;
    if (mode === "chat" && !message.value.trim()) {
      message.focus();
      return;
    }
    if (textDirty) await saveText();
    if (notesDirty) await saveNotes();
    if (boardDirty) await saveBoard();
    if (mode === "board" && shots.length && !window.confirm("Заменить текущую раскадровку новой?")) return;
    await api(path(), "POST", { mode, text: mode === "board" ? "" : message.value.trim() });
    pending = mode;
    if (mode !== "board") message.value = "";
    await refresh();
  }

  function paintShots(activeIndex) {
    const root = byId("script-shots");
    root.dataset.mode = project.mode;
    byId("script-board-empty").hidden = Boolean(shots.length);
    let elapsed = 0;
    root.replaceChildren(...shots.map((shot, index) => {
      const panel = element("details", "", "script-shot");
      panel.open = activeIndex === index;
      const title = element("summary", `${index + 1}. ${timecode(elapsed)}–${timecode(elapsed + shot.seconds)} · ${SHOTS[shot.size]}`);
      elapsed += shot.seconds;
      const preview = element("div", shot.image ? "" : (shot.visual || "Добавить референс"), "shot-preview");
      if (shot.image) {
        const image = element("img");
        image.src = "/script-media/" + id + "/" + shot.image;
        image.alt = "Референс кадра " + (index + 1);
        preview.append(image);
      }
      panel.append(title, preview);
      const length = element("label", "Длительность, сек");
      const duration = element("input");
      duration.type = "number";
      duration.min = "1";
      duration.max = "7200";
      duration.value = shot.seconds;
      duration.addEventListener("change", () => {
        if (duration.checkValidity()) {
          shot.seconds = Number(duration.value);
          boardDirty = true;
          paintShots(index);
        }
      });
      length.append(duration);
      const size = element("label", "Крупность");
      const choices = element("select");
      choices.replaceChildren(...Object.entries(SHOTS).map(([key, label]) => new Option(label, key, false, shot.size === key)));
      choices.addEventListener("change", () => { shot.size = choices.value; boardDirty = true; paintShots(index); });
      size.append(choices);
      const pair = element("div", "", "studio-fields");
      pair.append(length, size);
      panel.append(pair);
      for (const [key, label, limit] of FIELDS) {
        const wrap = element("label", label);
        const input = element("textarea");
        input.rows = 2;
        input.maxLength = limit;
        input.value = shot[key] || "";
        input.addEventListener("input", () => { shot[key] = input.value; boardDirty = true; });
        wrap.append(input);
        panel.append(wrap);
      }
      const row = element("div", "", "studio-row");
      const move = (delta) => {
        [shots[index], shots[index + delta]] = [shots[index + delta], shots[index]];
        boardDirty = true;
        paintShots(index + delta);
      };
      const up = button("Раньше", () => move(-1));
      const down = button("Позже", () => move(1));
      up.disabled = index === 0;
      down.disabled = index === shots.length - 1;
      row.append(up, down, button("Удалить кадр", () => {
        shots.splice(index, 1);
        boardDirty = true;
        paintShots();
      }));
      const ref = element("label", "Добавить изображение", "button secondary script-notes-file");
      const picker = element("input");
      picker.type = "file";
      picker.accept = "image/png,image/jpeg,image/webp";
      picker.addEventListener("change", () => upload(picker.files[0], index).catch(fail));
      ref.append(picker);
      row.append(ref);
      panel.append(row);
      return panel;
    }));
  }

  async function upload(image, index) {
    if (!image) return;
    if (boardDirty) await saveBoard();
    const identifier = shots[index]?.id;
    if (!identifier) return;
    const body = new FormData();
    body.append("image", image);
    const response = await fetch("/api/" + path() + "/shot/" + identifier, {
      method: "POST", headers: { "X-Videoport": "1" }, body,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Не удалось загрузить изображение.");
    shots[index].image = data.image;
    paintShots(index);
    await refresh();
  }

  async function saveText() {
    const previous = content.value;
    const result = await api(path() + "/edit", "POST", { latest: previous });
    textDirty = content.value !== previous;
    render(result);
  }

  async function saveNotes() {
    const previous = notes.value;
    const result = await api(path() + "/edit", "POST", { notes: previous });
    notesDirty = notes.value !== previous;
    render(result);
  }

  async function saveBoard() {
    const result = await api(path() + "/edit", "POST", { shots });
    boardDirty = false;
    render(result);
  }

  async function exportFile(kind) {
    if (kind === "txt" && textDirty) await saveText();
    if (kind === "html" && boardDirty) await saveBoard();
    const folder = await window.chooseVideoportFolder(outputFolder || "");
    if (!folder) return;
    outputFolder = folder;
    const result = await api(path() + "/export", "POST", { folder, kind });
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
      const data = await api("scripts", "POST", { title: byId("script-topic").value,
        platform: byId("script-platform").value, mode: byId("script-format").value,
        seconds: byId("script-seconds").value, audience: byId("script-audience").value });
      dialog.close();
      await open(data.id);
    } catch (error) {
      byId("script-new-status").textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });

  for (const tab of page.querySelectorAll("[data-script-panel]")) {
    tab.addEventListener("click", () => section(tab.dataset.scriptPanel));
  }
  byId("script-chat-form").addEventListener("submit", (event) => { event.preventDefault(); send("chat").catch(fail); });
  byId("script-draft").addEventListener("click", () => send("draft").catch(fail));
  byId("script-board-generate").addEventListener("click", () => send("board").catch(fail));
  content.addEventListener("input", () => { textDirty = true; byId("script-board-generate").disabled = project.busy || !content.value.trim(); });
  notes.addEventListener("input", () => { notesDirty = true; });
  byId("script-save-text").addEventListener("click", () => saveText().then(() => feedback(notice, "Сценарий сохранён.")).catch(fail));
  byId("script-save-notes").addEventListener("click", () => saveNotes().then(() => feedback(notice, "Заметки сохранены.")).catch(fail));
  byId("script-save-board").addEventListener("click", () => saveBoard().then(() => feedback(notice, "Раскадровка сохранена.")).catch(fail));
  byId("script-open-notes").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file) return;
    try {
      const text = await file.slice(0, 50000).text();
      notes.value = text.slice(0, 12000);
      notesDirty = true;
    } catch (error) {
      fail(error);
    }
  });
  byId("script-copy").addEventListener("click", () => navigator.clipboard.writeText(content.value)
    .then(() => feedback(notice, "Сценарий скопирован.")).catch(fail));
  byId("script-export-text").addEventListener("click", () => exportFile("txt").catch(fail));
  byId("script-export-board").addEventListener("click", () => exportFile("html").catch(fail));
  byId("script-add-shot").addEventListener("click", () => {
    if (shots.length >= 80) return;
    shots.push({ seconds: 5, size: "medium", visual: "", camera: "", voice: "", sound: "", overlay: "", prompt: "" });
    boardDirty = true;
    paintShots(shots.length - 1);
  });

  return { tab: byId("open-scripts"), page, show: refresh, hide() { ++revision; clearTimeout(poll); } };
}
