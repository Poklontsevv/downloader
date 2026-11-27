import { mountClipEditor, clock } from "./clip-editor.js";
import { mountCaptions } from "./captions-editor.js";
import { liveClipPlayer } from "./clip-player.js";
import { mountFrameEditor } from "./frame-editor.js";

export function setupClips(api) {
  const byId = (id) => document.getElementById(id);
  const open = byId("open-clips");
  const page = byId("clip-main");
  const items = byId("clip-items");
  const downloads = byId("clip-downloads");
  const projects = byId("clip-projects");
  const semantic = byId("clip-semantic");
  const assignment = byId("clip-assignment");
  const dialog = byId("clip-dialog");
  const dialogBody = byId("clip-dialog-body");
  const drafts = new Map();
  const pending = new Map();
  let state = {};
  let source = "";
  let projectKey;
  let signature;
  let active;
  let transcript;
  let copyFolder;
  let poll;
  let revision = 0;
  let saveTimer;
  let saving;
  const controls = ["clip-pick", "clip-downloads", "clip-length", "clip-count", "clip-language", "clip-quality", "clip-device",
    "clip-semantic", "clip-analyse", "clip-folder", "clip-export", "clip-projects", "clip-resume"];

  function showField(id, value) {
    const element = byId(id);
    const text = value || "";
    if (element.textContent !== text) element.textContent = text;
    element.hidden = !text;
  }

  function showError(message) {
    showField("clip-error", message);
    if (active && message) setSaveStatus(message, true);
  }

  function setSaveStatus(message, error = false) {
    if (!active) return;
    const status = active.root.querySelector(".editor-save-status");
    if (status.textContent !== message) status.textContent = message;
    status.classList.toggle("error", error);
  }

  function setControls() {
    const busy = Boolean(state.busy);
    for (const id of controls) byId(id).disabled = busy;
    assignment.disabled = busy || !semantic.checked;
    byId("clip-analyse").disabled = busy || (!source && !downloads.value);
    byId("clip-export").disabled = busy || ![...drafts.values()].some((clip) => clip.selected);
    for (const button of items.querySelectorAll("button")) button.disabled = busy;
    if (active) {
      for (const control of active.root.querySelectorAll(".editor-export, .caption-retry, .caption-quality, .caption-device")) {
        control.disabled = busy;
      }
    }
  }

  function showSpeech() {
    const speech = state.speech;
    const summary = speech ? `Распознавание · ${speech.label} · ${speech.seconds.toLocaleString("ru-RU")} с` : "";
    showField("clip-speech", summary);
    if (active) {
      const message = active.root.querySelector(".caption-speech");
      message.textContent = summary;
      message.hidden = !summary;
    }
  }

  function updateOptions(select, options, value) {
    const key = JSON.stringify(options);
    if (key !== select.dataset.options) {
      select.replaceChildren(...options.map(([label, id]) => new Option(label, id)));
      select.dataset.options = key;
    }
    if (select.value !== value) select.value = value;
  }

  function getTranscript() {
    transcript ||= api("clip/transcript").catch((error) => {
      transcript = null;
      throw error;
    });
    return transcript;
  }

  function updateCard(clip) {
    const card = items.querySelector('[data-clip-id="' + clip.id + '"]');
    if (!card) return;
    card.querySelector(".clip-card-name").textContent = clip.title;
    const duration = clip.segments.reduce((sum, part) => sum + Math.max(0, part.end - part.start), 0);
    card.querySelector(".clip-total").textContent = clip.segments.length + " отр. · " + clock(duration) + " · " + (clip.aspect || "9:16");
  }

  function queueSave(clip) {
    pending.set(clip.id, { project: state.project, clip: structuredClone(clip) });
    updateCard(clip);
    setSaveStatus("Сохранение…");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => flush().catch((error) => showError(error.message)), 180);
  }

  function flush() {
    clearTimeout(saveTimer);
    if (saving) return saving;
    saving = (async () => {
      while (pending.size) {
        const [id, item] = pending.entries().next().value;
        await api("clip/draft", "POST", item);
        if (pending.get(id) === item) pending.delete(id);
      }
      setSaveStatus("Сохранено");
    })().finally(() => { saving = null; });
    return saving;
  }

  window.addEventListener("pagehide", () => {
    if (saving) return;
    for (const item of pending.values()) {
      fetch("/api/clip/draft", { method: "POST", keepalive: true,
        headers: { "X-Videoport": "1", "Content-Type": "application/json" }, body: JSON.stringify(item),
      }).catch(() => {});
    }
  });

  async function submit(operation, data) {
    if (state.busy) return;
    ++revision;
    clearTimeout(poll);
    try {
      await flush();
      state.busy = true;
      setControls();
      showError("");
      await api("clip/" + operation, "POST", data);
    } catch (error) {
      state.busy = false;
      showError(error.message);
    }
    await refresh();
  }

  function exportClips(clips) {
    submit("export", { clips, folder: copyFolder || state.folder });
  }

  function edit(clip) {
    if (active || state.busy || !state.project) return;
    const root = byId("clip-editor-template").content.firstElementChild.cloneNode(true);
    const title = root.querySelector(".clip-title-input");
    title.value = clip.title;
    dialogBody.replaceChildren(root);
    dialog.showModal();
    for (const tab of root.querySelectorAll(".editor-tab")) {
      tab.addEventListener("click", () => {
        for (const button of root.querySelectorAll(".editor-tab")) {
          const selected = button === tab;
          button.setAttribute("aria-pressed", String(selected));
          root.querySelector(".editor-" + button.dataset.editorPanel).hidden = !selected;
        }
      });
    }
    const captionTabs = root.querySelectorAll("[data-caption-panel]");
    for (const tab of captionTabs) {
      tab.addEventListener("click", () => {
        for (const button of captionTabs) {
          const selected = button === tab;
          button.setAttribute("aria-pressed", String(selected));
          root.querySelector(".caption-page-" + button.dataset.captionPanel).hidden = !selected;
        }
      });
    }
    let editor;
    let captions;
    let frame;

    function apply() {
      Object.assign(clip, editor.value(), { title: title.value.trim() || "Клип", style: captions.style });
      queueSave(clip);
    }

    const player = liveClipPlayer(root, "/clip-source/" + state.project, (time) => {
      editor?.playback(time);
      captions?.position();
    });
    editor = mountClipEditor(root, clip, {
      duration: state.duration, getTranscript,
      onSeek: player.seek, onEdit: () => {
        const { segments } = editor.value();
        player.track(segments, editor.cursor());
        captions.paint();
        apply();
        frame.sync();
      },
    });
    captions = mountCaptions(root, editor, [], clip.style, state.fonts, apply, player.captions);
    function refreshCaptions() {
      getTranscript().then((rows) => {
        if (active?.root !== root) return;
        captions.transcript(rows);
        editor.refreshTranscript(rows);
      }).catch((error) => { if (active?.root === root) setSaveStatus(error.message, true); });
    }
    const project = state.project;
    frame = mountFrameEditor(root, clip, player, {
      save: apply,
      request: (value) => api("clip/track", "POST", { project, clip: value }),
      refresh,
      busy: () => state.busy,
      isOpen: () => active?.root === root,
    });
    active = { root, player, refreshCaptions, frame };
    const quality = root.querySelector(".caption-quality");
    const device = root.querySelector(".caption-device");
    quality.value = state.speechOptions?.quality || "medium";
    device.value = state.speechOptions?.device || "auto";
    showSpeech();
    player.track(clip.segments, 0);
    frame.sync();
    refreshCaptions();
    title.addEventListener("input", apply);
    root.querySelector(".editor-close").addEventListener("click", closeEditor);
    root.querySelector(".editor-export").addEventListener("click", () => exportClips([clip]));
    root.querySelector(".caption-retry").addEventListener("click", () =>
      submit("captions", { project: state.project, clip, quality: quality.value, device: device.value }));
    const srt = root.querySelector(".caption-srt");
    const srtFolder = root.querySelector(".caption-srt-folder");
    let savedFolder;
    srt.addEventListener("click", async () => {
      srt.disabled = true;
      try {
        const result = await api("clip/srt", "POST", { project, clip });
        if (active?.root === root) {
          savedFolder = result.folder;
          srtFolder.hidden = false;
          setSaveStatus("SRT сохранён");
        }
      } catch (error) {
        if (active?.root === root) setSaveStatus(error.message, true);
      } finally {
        srt.disabled = false;
      }
    });
    srtFolder.addEventListener("click", () =>
      window.openVideoportDownloads(savedFolder).catch((error) => setSaveStatus(error.message, true)));
    const fontInput = root.querySelector(".caption-font-input");
    fontInput.addEventListener("change", async () => {
      const file = fontInput.files[0];
      fontInput.value = "";
      if (!file) return;
      if (file.size > 5_000_000) {
        setSaveStatus("Файл шрифта должен быть меньше 5 МБ.", true);
        return;
      }
      const body = new FormData();
      body.append("font", file);
      try {
        const response = await fetch("/api/clip/font", { method: "POST", headers: { "X-Videoport": "1" }, body });
        const item = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(item.error || "Не удалось добавить шрифт.");
        state.fonts.push(item);
        if (active?.root === root) captions.addFont(item);
      } catch (error) {
        if (active?.root === root) setSaveStatus(error.message, true);
      }
    });
    root.querySelector(".editor-play").focus();
  }

  async function closeEditor() {
    if (!active) return;
    try {
      await flush();
      dialog.close();
    } catch (error) {
      setSaveStatus(error.message, true);
    }
  }

  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeEditor();
  });
  dialog.addEventListener("close", () => {
    active?.player.close();
    active = null;
    dialogBody.replaceChildren();
  });

  function addCard(entry) {
    const clip = structuredClone(entry);
    const card = byId("clip-template").content.firstElementChild.cloneNode(true);
    card.dataset.clipId = clip.id;
    card.querySelector(".clip-reason").textContent = clip.reason;
    const checkbox = card.querySelector(".include-clip");
    checkbox.checked = clip.selected;
    checkbox.addEventListener("change", () => {
      clip.selected = checkbox.checked;
      queueSave(clip);
      setControls();
    });
    card.querySelector(".clip-watch").addEventListener("click", () => edit(clip));
    card.querySelector(".clip-save").addEventListener("click", () => exportClips([clip]));
    items.append(card);
    drafts.set(clip.id, clip);
    updateCard(clip);
  }

  function showFiles(files) {
    const list = byId("clip-files");
    const key = JSON.stringify(files);
    if (key === list.dataset.files) return;
    list.dataset.files = key;
    byId("clip-files-empty").hidden = files.length > 0;
    list.replaceChildren(...files.map((file) => {
      const row = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button text-button";
      button.textContent = file.split(/[\\/]/).pop();
      button.title = file;
      button.addEventListener("click", () => window.openVideoportFile(file).catch((error) => showError(String(error))));
      row.append(button);
      return row;
    }));
  }

  async function refresh() {
    clearTimeout(poll);
    if (page.hidden) return;
    const current = ++revision;
    try {
      const next = await api("clip/state");
      if (current !== revision || page.hidden) return;
      const newTranscript = state.project === next.project && state.transcriptRevision !== next.transcriptRevision;
      state = next;
      if (projectKey !== state.project) {
        projectKey = state.project;
        transcript = null;
        copyFolder = null;
      }
      if (newTranscript) {
        transcript = null;
        active?.refreshCaptions();
      }
      const progress = byId("clip-progress");
      const step = Number.isFinite(state.percent) && state.busy ? " · " + state.percent + "%" : "";
      showField("clip-status", (state.stage || "") + step);
      showSpeech();
      showField("clip-warning", state.warning);
      if (state.error) showError(state.error);
      else if (!pending.size) showField("clip-error", "");
      if (active && state.busy) setSaveStatus((state.stage || "") + step);
      else if (active && ["Экспорт завершён", "Автосубтитры обновлены"].includes(state.stage)) setSaveStatus(state.stage);
      progress.hidden = !state.busy;
      if (Number.isFinite(state.percent)) progress.value = state.percent;
      else progress.removeAttribute("value");
      updateOptions(downloads, [["Выберите готовый файл", ""], ...[...state.downloads].reverse().map((file) => [file.name, file.id])],
        downloads.value);
      const labels = { analysis: "анализ", ready: "готов", interrupted: "прерван", error: "ошибка" };
      updateOptions(projects, [["Выберите проект", ""], ...state.projects.map((project) =>
        [`${project.name} · ${labels[project.status] || project.status}`, project.id])], state.project || "");
      byId("clip-project-folder").hidden = !state.projectFolder;
      byId("clip-resume").hidden = !["interrupted", "error"].includes(state.projectStatus);
      showField("clip-project-source", state.source);
      showField("clip-project-assignment", state.assignment ? "Задание проекта · " + state.assignment : "");
      active?.frame.update(state);
      showField("clip-location", copyFolder ? "Копии сохранятся в " + copyFolder : state.folder);
      const clips = state.clips || [];
      const key = state.project + ":" + clips.map((clip) => clip.id).join(":");
      if (key !== signature) {
        signature = key;
        drafts.clear();
        items.replaceChildren();
        clips.forEach(addCard);
      }
      byId("clip-results").hidden = !clips.length;
      showFiles(state.files || []);
      setControls();
      if (!page.hidden) poll = setTimeout(refresh, state.busy ? 700 : 5000);
    } catch (error) {
      if (current !== revision) return;
      showError(error.message);
      state.busy = false;
      setControls();
      if (!page.hidden) poll = setTimeout(refresh, 5000);
    }
  }

  open.addEventListener("click", () => {
    if (!page.hidden) return;
    page.hidden = false;
    open.setAttribute("aria-pressed", "true");
    for (const tab of document.querySelectorAll("[data-platform]")) tab.setAttribute("aria-pressed", "false");
    byId("media-main").hidden = true;
    document.querySelector(".download-footer").hidden = true;
    document.body.classList.add("clip-active");
    window.scrollTo(0, 0);
    refresh();
  });
  for (const tab of document.querySelectorAll("[data-platform], #open-transcript, #open-scripts")) {
    tab.addEventListener("click", () => {
      if (page.hidden) return;
      page.hidden = true;
      clearTimeout(poll);
      open.setAttribute("aria-pressed", "false");
      tab.setAttribute("aria-pressed", "true");
      byId("media-main").hidden = false;
      document.querySelector(".download-footer").hidden = false;
      document.body.classList.remove("clip-active");
    });
  }
  projects.addEventListener("change", () => {
    if (projects.value) submit("project", { project: projects.value });
  });
  byId("clip-project-folder").addEventListener("click", () =>
    window.openVideoportDownloads(state.projectFolder).catch((error) => showError(String(error))));
  byId("clip-resume").addEventListener("click", () => submit("analyse", { project: state.project }));
  byId("clip-pick").addEventListener("click", async () => {
    try {
      const path = await window.chooseVideoportVideo();
      if (!path) return;
      downloads.value = "";
      source = path;
      showField("clip-name", source);
      setControls();
    } catch (error) {
      showError(String(error));
    }
  });
  downloads.addEventListener("change", () => {
    source = "";
    showField("clip-name", downloads.value ? downloads.selectedOptions[0].textContent : "");
    setControls();
  });
  semantic.addEventListener("change", setControls);
  byId("clip-analyse").addEventListener("click", () => {
    const number = byId("clip-count");
    if (!number.reportValidity()) return;
    submit("analyse", { file: source, id: downloads.value, length: byId("clip-length").value,
      number: number.value, language: byId("clip-language").value, quality: byId("clip-quality").value,
      device: byId("clip-device").value,
      semantic: semantic.checked, assignment: assignment.value });
  });
  byId("clip-folder").addEventListener("click", async () => {
    try {
      copyFolder = await window.chooseVideoportFolder(copyFolder || state.folder) || copyFolder;
      if (copyFolder) showField("clip-location", "Копии сохранятся в " + copyFolder);
    } catch (error) {
      showError(String(error));
    }
  });
  byId("clip-export").addEventListener("click", () => exportClips([...drafts.values()].filter((clip) => clip.selected)));
  setControls();
}
