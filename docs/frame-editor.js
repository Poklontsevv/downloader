function trajectorySignature(clip) {
  const ranges = [];
  const snap = (time) => Math.round(time * 1000) / 1000;
  for (const part of [...clip.segments].sort((a, b) => a.start - b.start)) {
    const start = snap(part.start);
    const end = snap(part.end);
    const previous = ranges.at(-1);
    if (previous && start <= previous[1]) previous[1] = Math.max(previous[1], end);
    else ranges.push([start, end]);
  }
  const { target, anchor } = clip.tracking;
  return JSON.stringify([target, target === "object" ? anchor : null, ranges]);
}

export function mountFrameEditor(root, clip, player, { save, request, refresh, busy, isOpen }) {
  const find = (selector) => root.querySelector(selector);
  const aspect = find(".clip-aspect");
  const layout = find(".clip-mode");
  const toggle = find(".track-enabled");
  const target = find(".track-target");
  const pick = find(".track-pick");
  const retry = find(".track-retry");
  const status = find(".track-status");
  const tracking = clip.tracking = { enabled: false, target: "face", ...clip.tracking };
  const trajectories = new Map();
  let pending;
  let checking = false;
  let failure;
  let picking = false;
  let stage = "";

  aspect.value = clip.aspect || "9:16";
  toggle.checked = tracking.enabled;
  target.value = tracking.target;

  function draw() {
    const signature = trajectorySignature(clip);
    const eligible = tracking.enabled && layout.value === "crop";
    const result = eligible ? trajectories.get(signature) : null;
    player.framing(aspect.value, layout.value, result?.points);
    target.disabled = !tracking.enabled;
    pick.hidden = !eligible || tracking.target !== "object";
    pick.textContent = picking ? "Отменить выбор" : tracking.anchor ? "Указать другой объект" : "Указать объект в кадре";
    const issue = eligible && failure?.signature === signature ? failure.message : "";
    retry.hidden = !issue;
    if (picking) status.textContent = "Нажмите на объект в предпросмотре. Исходник показан целиком.";
    else if (!tracking.enabled) status.textContent = "";
    else if (!eligible) status.textContent = "Слежение применяется в режиме «Заполнить кадр».";
    else if (tracking.target === "object" && !tracking.anchor) status.textContent = "Укажите объект на кадре, чтобы начать слежение.";
    else if (result) status.textContent = result.found ? "Траектория сохранена в проекте."
      : "Цель не найдена. Использовано кадрирование по центру.";
    else if (issue) status.textContent = issue;
    else if (pending) status.textContent = stage || "Подготовка траектории…";
    else if (busy() && !checking) status.textContent = "Автослежение начнётся после текущей операции.";
    else status.textContent = "Подготовка траектории…";
  }

  async function sync() {
    draw();
    const signature = trajectorySignature(clip);
    if (!tracking.enabled || layout.value !== "crop" || (tracking.target === "object" && !tracking.anchor)
        || trajectories.has(signature) || failure?.signature === signature || pending || checking || busy()) return;
    checking = true;
    try {
      const result = await request(clip);
      if (!isOpen()) return;
      if (result.points) trajectories.set(signature, result);
      else {
        pending = { key: result.key, signature };
        refresh();
      }
    } catch (error) {
      failure = { signature, message: error.message };
    } finally {
      checking = false;
      if (isOpen()) {
        draw();
        if (signature !== trajectorySignature(clip)) sync();
      }
    }
  }

  function endPick() {
    picking = false;
    pick.setAttribute("aria-pressed", "false");
    player.pickObject(null);
  }

  aspect.addEventListener("change", () => {
    clip.aspect = aspect.value;
    save();
    draw();
  });
  layout.addEventListener("change", () => { endPick(); save(); sync(); });
  toggle.addEventListener("change", () => {
    tracking.enabled = toggle.checked;
    if (tracking.enabled) layout.value = "crop";
    endPick();
    save();
    sync();
  });
  target.addEventListener("change", () => {
    tracking.target = target.value;
    endPick();
    save();
    sync();
  });
  pick.addEventListener("click", () => {
    if (picking) endPick();
    else {
      picking = true;
      pick.setAttribute("aria-pressed", "true");
      player.pickObject((anchor) => {
        tracking.anchor = anchor;
        endPick();
        save();
        sync();
      });
    }
    draw();
  });
  retry.addEventListener("click", () => { failure = null; sync(); });
  draw();
  return {
    sync,
    update(state) {
      if (pending && state.trackKey === pending.key) {
        stage = state.stage + (Number.isFinite(state.percent) ? ` · ${state.percent}%` : "");
      } else if (pending && !state.busy) {
        if (state.error) failure = { signature: pending.signature, message: state.error };
        pending = null;
        stage = "";
      }
      sync();
    },
  };
}
