const fontFaces = new Map();
const builtin = { blocks: "VP Blocks", manrope: "VP Manrope", oswald: "VP Oswald" };

function family(font) {
  if (builtin[font]) return `"${builtin[font]}", "VP Manrope", sans-serif`;
  const name = "VP Custom " + font;
  if (!fontFaces.has(font)) {
    const face = new FontFace(name, `url("/clip-font/${font}")`);
    const load = face.load().then((loaded) => document.fonts.add(loaded)).catch(() => fontFaces.delete(font));
    fontFaces.set(font, load);
  }
  return `"${name}", "VP Manrope", sans-serif`;
}

export function liveClipPlayer(root, source, onPosition) {
  const video = root.querySelector(".editor-video");
  const stage = root.querySelector(".editor-stage");
  const background = root.querySelector(".editor-blur");
  const paintBackground = background.getContext("2d");
  const play = root.querySelector(".editor-play");
  const status = root.querySelector(".editor-video-status");
  const caption = root.querySelector(".editor-overlay span");
  const volume = root.querySelector(".editor-volume");
  let parts = [];
  let index = 0;
  let position = 0;
  let total = 0;
  let seeking = false;
  let playing = false;
  let frame;
  let events = [];
  let captionStyle;
  let displayed;
  let disposed = false;
  let frameFor = () => ({ layout: "crop", zoom: 100, x: 0, y: 0, blur: 0 });
  let pick;
  let designWidth = 1080;

  function showStatus(text) {
    status.textContent = text;
    status.hidden = !text;
  }

  function positionFrame() {
    const part = parts[index];
    const width = stage.clientWidth, height = stage.clientHeight;
    if (!part || !video.videoWidth || !width || !height) return;
    const view = pick ? { layout: "fit", zoom: 100, x: 0, y: 0, blur: 0 } : frameFor(part);
    const dx = view.x * width / 100, dy = view.y * height / 100;
    const ratioX = width / video.videoWidth, ratioY = height / video.videoHeight;
    const cover = Math.max(ratioX, ratioY);
    const zoom = view.zoom / 100;
    const points = view.layout === "crop" ? view.points : null;
    const scaled = (view.layout === "fit" ? Math.min(ratioX, ratioY) : cover) * zoom;
    if (points?.length) {
      const time = part.start + position - part.offset;
      let low = 0, high = points.length;
      while (low < high) {
        const middle = (low + high) >>> 1;
        if (points[middle][0] <= time) low = middle + 1;
        else high = middle;
      }
      const before = points[Math.max(0, low - 1)], after = points[Math.min(low, points.length - 1)];
      const weight = after[0] > before[0] ? Math.max(0, Math.min(1, (time - before[0]) / (after[0] - before[0]))) : 0;
      const align = (center, full, visible) => full - visible > .1
        ? Math.max(0, Math.min(1, (center * full - visible / 2) / (full - visible))) * 100 : 50;
      const x = before[1] + (after[1] - before[1]) * weight;
      const y = before[2] + (after[2] - before[2]) * weight;
      Object.assign(video.style, { width: "100%", height: "100%", left: "0", top: "0", objectFit: "cover",
        objectPosition: `${align(x, video.videoWidth * cover, width)}% ${align(y, video.videoHeight * cover, height)}%`,
        transform: `translate(${dx}px,${dy}px) scale(${zoom})` });
    } else {
      const sourceWidth = video.videoWidth * scaled, sourceHeight = video.videoHeight * scaled;
      Object.assign(video.style, { width: sourceWidth + "px", height: sourceHeight + "px",
        left: ((width - sourceWidth) / 2 + dx) + "px", top: ((height - sourceHeight) / 2 + dy) + "px",
        objectPosition: "50% 50%", objectFit: "contain", transform: "none" });
    }
    background.hidden = !view.blur;
    if (view.blur && video.readyState >= 2) {
      const smallWidth = 160, smallHeight = Math.round(smallWidth * height / width);
      if (background.width !== smallWidth || background.height !== smallHeight) {
        background.width = smallWidth;
        background.height = smallHeight;
      }
      const sw = width / cover, sh = height / cover;
      paintBackground.drawImage(video, (video.videoWidth - sw) / 2, (video.videoHeight - sh) / 2,
        sw, sh, 0, 0, smallWidth, smallHeight);
      background.style.filter = `blur(${view.blur * width / 1080}px)`;
    }
  }

  function frameStyle() {
    stage.toggleAttribute("data-picking", Boolean(pick));
    positionFrame();
  }

  const resize = new ResizeObserver(positionFrame);
  resize.observe(stage);


  function overlay(time) {
    const visible = events.findLast((cue) => cue.start <= time && time < cue.end);
    if (visible === displayed) return;
    displayed = visible;
    caption.textContent = visible?.text || "";
    caption.hidden = !visible;
    if (visible) {
      const style = visible.style || captionStyle;
      paintCaptionStyle(style);
      const longest = Math.max(1, ...visible.text.split("\n").map((line) => [...line].length));
      const size = style.mode === "phrase" ? style.size
        : Math.min(style.size, Math.max(1, Math.round(980 / (longest * .96))));
      caption.style.fontSize = (size * 100 / designWidth) + "cqw";
    }
  }

  function announce(time) {
    position = time;
    onPosition(time);
    overlay(time);
    positionFrame();
  }

  function stop() {
    playing = false;
    video.pause();
    cancelAnimationFrame(frame);
    play.textContent = "Пуск";
  }

  function jump(time) {
    if (!parts.length) return;
    time = Math.min(Math.max(0, time), total);
    index = parts.findIndex((part) => time < part.offset + part.end - part.start);
    if (index < 0) index = parts.length - 1;
    const part = parts[index];
    announce(time);
    if (video.readyState) {
      const target = Math.min(part.end - .001, part.start + time - part.offset);
      if (Math.abs(video.currentTime - target) > .01) {
        seeking = true;
        video.pause();
        video.currentTime = target;
      }
    }
  }

  function tick() {
    if (!playing || disposed) return;
    if (!seeking && !video.seeking && parts[index]) {
      const part = parts[index];
      if (video.currentTime >= part.end - .025 || video.ended) {
        if (index + 1 >= parts.length) {
          stop();
          announce(total);
          return;
        }
        jump(parts[index + 1].offset + .001);
        if (!seeking && video.paused) playVideo();
      } else {
        announce(part.offset + Math.max(0, video.currentTime - part.start));
      }
    }
    frame = requestAnimationFrame(tick);
  }

  function playVideo() {
    video.play().catch((error) => {
      if (error.name !== "AbortError" && playing) {
        stop();
        showStatus("Не удалось начать воспроизведение.");
      }
    });
  }

  play.addEventListener("click", () => {
    if (pick) return;
    if (playing) {
      stop();
      return;
    }
    if (position >= total - .025) jump(0);
    playing = true;
    play.textContent = "Пауза";
    if (!seeking) playVideo();
    frame = requestAnimationFrame(tick);
  });
  video.addEventListener("seeked", () => {
    seeking = false;
    positionFrame();
    if (playing) playVideo();
  });
  video.addEventListener("loadedmetadata", () => {
    showStatus("");
    play.disabled = !parts.length;
    jump(position);
  });
  video.addEventListener("error", () => {
    if (disposed) return;
    stop();
    play.disabled = true;
    showStatus("Не удалось открыть предпросмотр. Проверьте исходный файл.");
  });
  volume.addEventListener("input", () => { video.volume = Number(volume.value); });
  video.addEventListener("click", (event) => {
    if (!pick) {
      if (!play.disabled) play.click();
      return;
    }
    if (!video.videoWidth || video.seeking) return;
    const bounds = video.getBoundingClientRect();
    const scale = Math.min(bounds.width / video.videoWidth, bounds.height / video.videoHeight);
    const width = video.videoWidth * scale;
    const height = video.videoHeight * scale;
    const x = (event.clientX - bounds.left - (bounds.width - width) / 2) / width;
    const y = (event.clientY - bounds.top - (bounds.height - height) / 2) / height;
    if (x >= 0 && x <= 1 && y >= 0 && y <= 1) pick({ time: video.currentTime, x, y });
  });
  play.disabled = true;
  showStatus("Открываем видео…");
  video.src = source;

  function paintCaptionStyle(style) {
      const overlayBox = caption.parentElement;
      overlayBox.style.top = style.position + "%";
      caption.style.fontFamily = family(style.font);
      caption.style.color = style.color;
      caption.style.backgroundColor = style.background ? style.stroke + "c7" : "transparent";
      caption.style.whiteSpace = style.mode === "phrase" ? "pre-wrap" : "pre";
      caption.style.webkitTextStroke = (style.background ? 0 : style.outline * 100 / designWidth) + "cqw " + style.stroke;
      caption.style.textShadow = style.shadow ? "0 " + (style.shadow * 100 / designWidth) + "cqw .15em #0009" : "none";
  }

  function paintCaptions() {
    displayed = null;
    overlay(position);
  }

  return {
    track(segments, time) {
      stop();
      let next = 0;
      parts = segments.filter((part) => part.start !== "" && part.end !== "" && Number(part.end) > Number(part.start))
        .map((part) => {
          const result = { ...part, start: Number(part.start), end: Number(part.end), offset: next };
          next += result.end - result.start;
          return result;
        });
      total = next;
      play.disabled = !video.readyState || !parts.length;
      jump(time);
    },
    framing(aspect, resolveFrame) {
      frameFor = resolveFrame;
      const nextWidth = aspect === "16:9" ? 1920 : 1080;
      if (nextWidth !== designWidth) {
        designWidth = nextWidth;
        paintCaptions();
      }
      stage.dataset.aspect = aspect;
      frameStyle();
    },
    pickObject(callback) {
      if (callback) stop();
      pick = callback;
      frameStyle();
    },
    captions(nextEvents, style) {
      events = nextEvents;
      captionStyle = style;
      paintCaptions();
    },
    seek: jump,
    close() {
      disposed = true;
      resize.disconnect();
      stop();
      video.removeAttribute("src");
      video.load();
    },
  };
}
