import { byId } from "./studio-ui.js";

export function setupUpdates(api) {
  const modal = byId("update-dialog");
  const status = byId("update-state");
  const progress = byId("update-progress");
  const command = byId("update-command");
  let state;
  let timer;
  let revision = 0;
  const showError = (error) => { status.textContent = error.message; command.hidden = false; command.textContent = "Повторить"; };

  async function refresh() {
    clearTimeout(timer);
    if (!modal.open) return;
    const token = ++revision;
    try {
      const next = await api("update");
      if (!modal.open || token !== revision) return;
      state = next;
      const messages = {
        idle: "Проверяем версию…", checking: "Проверяем версию…",
        current: `Установлена актуальная версия ${state.current}.`,
        available: `Доступна версия ${state.available?.version || ""}.`,
        downloading: "Загружаем и проверяем установщик…",
        ready: "Установщик проверен. Videoport закроется перед обновлением.",
        error: state.error,
      };
      status.textContent = messages[state.state];
      progress.hidden = state.state !== "downloading";
      if (state.percent !== null) progress.value = state.percent;
      else progress.removeAttribute("value");
      const labels = { available: "Скачать", ready: "Установить обновление", error: "Повторить" };
      command.hidden = !labels[state.state];
      command.textContent = labels[state.state] || "";
      if (["checking", "downloading"].includes(state.state)) timer = setTimeout(refresh, 750);
    } catch (error) {
      showError(error);
    }
  }

  async function act(action) {
    command.disabled = true;
    try {
      await api("update", "POST", { action });
      await refresh();
    } catch (error) {
      showError(error);
    } finally {
      command.disabled = false;
    }
  }

  byId("open-update").addEventListener("click", async () => {
    modal.showModal();
    status.textContent = "Проверяем версию…";
    progress.hidden = true;
    command.hidden = true;
    try {
      const next = await api("update");
      if (["downloading", "ready"].includes(next.state)) await refresh();
      else await act("check");
    } catch (error) {
      showError(error);
    }
  });
  command.addEventListener("click", async () => {
    if (state?.state === "ready") {
      command.disabled = true;
      try {
        await window.installVideoportUpdate(state.available.name, state.available.sha256);
      } catch (error) {
        showError(error);
        command.disabled = false;
      }
    } else await act(state?.state === "available" ? "download" : "check");
  });
  modal.addEventListener("close", () => { clearTimeout(timer); ++revision; });
}
