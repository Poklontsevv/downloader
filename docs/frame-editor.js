function trajectorySignature(part) {
  const { target, anchor } = part.frame.tracking;
  return JSON.stringify([target, target === "object" ? anchor : null, Number(part.start), Number(part.end)]);
}

export function mountFrameEditor(root, clip, editor, player, { save, request, refresh, busy, isOpen }) {
  const find = (selector) => root.querySelector(selector);
  const aspect = find(".clip-aspect");
  const layout = find(".clip-mode");
  const toggle = find(".track-enabled");
  const target = find(".track-target");
  const pick = find(".track-pick");
  const retry = find(".track-retry");
  const status = find(".track-status");
  const fields = [...root.querySelectorAll("[data-frame-field]")];
  const trajectories = new Map();
  const failures = new Map();
  const eligible = (part) => part?.frame.layout === "crop" && part.frame.tracking.enabled;
  let pending;
  let checking = false;
  let picking = false;
  let stage = "";
  let lastPlayback;
  aspect.value = clip.aspect || "9:16";

  function frameFor(part) {
    const frame = part.frame;
    const result = eligible(part) ? trajectories.get(trajectorySignature(part)) : null;
    return { ...frame, points: result?.points };
  }

  function draw() {
    const part = editor.selectedPart();
    if (!part) return;
    const { tracking } = part.frame;
    const signature = trajectorySignature(part);
    const result = trajectories.get(signature);
    const issue = eligible(part) && failures.get(signature);
    player.framing(aspect.value, frameFor);
    target.disabled = !tracking.enabled;
    pick.hidden = !eligible(part) || tracking.target !== "object";
    pick.textContent = picking ? "Отменить выбор" : tracking.anchor ? "Указать другой объект" : "Указать объект в кадре";
    retry.hidden = !issue;
    if (picking) status.textContent = "Нажмите на объект в предпросмотре.";
    else if (!tracking.enabled) status.textContent = "";
    else if (!eligible(part)) status.textContent = "Слежение применяется в режиме «Заполнить кадр».";
    else if (tracking.target === "object" && !tracking.anchor) status.textContent = "Укажите объект на кадре.";
    else if (result) status.textContent = result.found ? "Траектория сохранена." : "Цель не найдена. Кадр по центру.";
    else if (issue) status.textContent = issue;
    else if (pending?.signature === signature) status.textContent = stage || "Подготовка траектории…";
    else status.textContent = "Подготовка автослежения…";
  }

  async function sync() {
    draw();
    if (pending || checking || busy()) return;
    const part = [editor.selectedPart(), editor.currentPart()].find((candidate) => {
      if (!eligible(candidate)) return false;
      const { tracking } = candidate.frame;
      const signature = trajectorySignature(candidate);
      return (tracking.target !== "object" || tracking.anchor) && !trajectories.has(signature) && !failures.has(signature);
    });
    if (!part) return;
    const signature = trajectorySignature(part);
    checking = true;
    try {
      const { frame } = part;
      const segment = { ...part, start: Number(part.start), end: Number(part.end) };
      const result = await request({ ...clip, segments: [segment], tracking: frame.tracking, layout: frame.layout });
      if (!isOpen()) return;
      if (result.points) trajectories.set(signature, result);
      else {
        pending = { key: result.key, signature };
        refresh();
      }
    } catch (error) {
      failures.set(signature, error.message);
    } finally {
      checking = false;
      if (isOpen()) sync();
    }
  }

  function endPick() {
    if (!picking) return;
    picking = false;
    pick.setAttribute("aria-pressed", "false");
    player.pickObject(null);
  }

  function selection() {
    endPick();
    const part = editor.selectedPart();
    if (!part) return;
    const { frame } = part;
    find(".frame-selection").textContent = "Отрезок " + (editor.selectedIndex() + 1);
    layout.value = frame.layout;
    toggle.checked = frame.tracking.enabled;
    target.value = frame.tracking.target;
    for (const field of fields) field.value = frame[field.dataset.frameField];
    sync();
  }

  function amend(change) {
    endPick();
    const { frame } = editor.selectedPart();
    change(frame);
    editor.revealSelected();
    save();
    selection();
  }

  aspect.addEventListener("change", () => { clip.aspect = aspect.value; save(); draw(); });
  layout.addEventListener("change", () => amend((frame) => { frame.layout = layout.value; }));
  toggle.addEventListener("change", () => amend((frame) => {
    frame.tracking.enabled = toggle.checked;
    if (toggle.checked) frame.layout = "crop";
  }));
  target.addEventListener("change", () => amend((frame) => { frame.tracking.target = target.value; }));
  for (const field of fields) field.addEventListener("change", () => {
    if (!field.checkValidity() || field.value === "") {
      field.value = editor.selectedPart().frame[field.dataset.frameField];
      return;
    }
    amend((frame) => { frame[field.dataset.frameField] = Number(field.value); });
  });
  pick.addEventListener("click", () => {
    if (picking) endPick();
    else {
      editor.revealSelected();
      picking = true;
      pick.setAttribute("aria-pressed", "true");
      player.pickObject((anchor) => amend((frame) => { frame.tracking.anchor = anchor; }));
    }
    draw();
  });
  retry.addEventListener("click", () => { failures.delete(trajectorySignature(editor.selectedPart())); sync(); });
  return {
    selection,
    position() {
      const part = editor.currentPart();
      if (part === lastPlayback) return;
      lastPlayback = part;
      sync();
    },
    update(state) {
      if (pending && state.trackKey === pending.key) {
        stage = state.stage + (Number.isFinite(state.percent) ? ` · ${state.percent}%` : "");
      } else if (pending && !state.busy) {
        if (state.error) failures.set(pending.signature, state.error);
        pending = null;
        stage = "";
      }
      sync();
    },
  };
}
