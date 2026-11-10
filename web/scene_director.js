import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import {
  createDefaultPlan, parsePlan, serializePlan, inspectPlan, sceneAdvancedJSON,
  editScene, buildSubmission, workflowChoices, formatDuration, activeJobsRemain, serializeDraftForNode,
  CINEMATIC_CHOICES,
} from "./director_data.js";
import { createDirectorClient } from "./director_client.js";

const client = createDirectorClient((route, options) => api.fetchApi(route, options));
const editors = new WeakMap();
const ACTIVE_STATUSES = new Set(["running", "pausing", "queued", "pending", "starting", "resuming"]);

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function input(value = "", type = "text") {
  const node = element("input");
  node.type = type;
  node.value = value ?? "";
  return node;
}

function textarea(value = "", rows = 3) {
  const node = element("textarea");
  node.value = value ?? "";
  node.rows = rows;
  return node;
}

function field(label, control, help) {
  const container = element("label", "asd-field");
  container.append(element("span", "asd-label", label), control);
  if (help) container.append(element("span", "asd-help", help));
  return container;
}

function numeric(value, min, max, step = 1) {
  const node = input(value, "number");
  node.min = min;
  node.max = max;
  node.step = step;
  return node;
}

function choice(value, options, emptyLabel = "No added direction") {
  const node = element("select");
  const empty = element("option", "", emptyLabel);
  empty.value = "";
  node.append(empty);
  for (const item of options) {
    const option = element("option", "", item);
    option.value = item;
    node.append(option);
  }
  node.value = value || "";
  return node;
}

function widget(node, name) {
  return node.widgets?.find((candidate) => candidate.name === name);
}

function installStyle() {
  if (document.getElementById("alpha-scene-director-style")) return;
  const style = element("style");
  style.id = "alpha-scene-director-style";
  style.textContent = `
    dialog.alpha-scene-director { --asd-bg:#111318; --asd-panel:#1b2029; --asd-border:#394354; --asd-muted:#acb7c7; --asd-accent:#7fc6ff; width:100vw; max-width:100vw; height:100vh; max-height:100vh; margin:0; padding:0; border:0; border-radius:0; color:#edf2f8; background:radial-gradient(circle at 82% 0%,#1b3040 0,#111318 34%); font:14px/1.5 system-ui,sans-serif; }
    dialog.alpha-scene-director::backdrop { background:#000a; }
    .alpha-scene-director * { box-sizing:border-box; }
    .alpha-scene-director .asd-shell { display:flex; flex-direction:column; height:100vh; }
    .alpha-scene-director .asd-header { display:flex; justify-content:space-between; gap:18px; align-items:flex-start; padding:20px 24px 16px; border-bottom:1px solid var(--asd-border); }
    .alpha-scene-director h2,.alpha-scene-director h3,.alpha-scene-director p { margin:0; }
    .alpha-scene-director h2 { font:700 clamp(24px,3vw,42px)/1.05 Georgia,serif; letter-spacing:-.025em; }
    .alpha-scene-director h3 { font-size:16px; margin-bottom:10px; }
    .alpha-scene-director .asd-header p { margin-top:5px; color:var(--asd-muted); }
    .alpha-scene-director .asd-body { overflow:auto; padding:20px 24px; display:grid; gap:22px; }
    .alpha-scene-director .asd-section { border:1px solid var(--asd-border); background:var(--asd-panel); border-radius:10px; padding:16px; }
    .alpha-scene-director .asd-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }
    .alpha-scene-director .asd-three { grid-template-columns:repeat(3,minmax(0,1fr)); }
    .alpha-scene-director .asd-field { display:flex; flex-direction:column; gap:6px; min-width:0; }
    .alpha-scene-director .asd-label { font-weight:600; }
    .alpha-scene-director .asd-help { color:var(--asd-muted); font-size:12px; font-weight:400; }
    .alpha-scene-director .asd-stack { display:grid; gap:14px; }
    .alpha-scene-director input,.alpha-scene-director textarea,.alpha-scene-director select { width:100%; min-width:0; padding:9px 10px; border:1px solid #566279; border-radius:6px; color:#f1f5fa; background:#11161e; font:inherit; }
    .alpha-scene-director textarea { resize:vertical; }
    .alpha-scene-director input:focus,.alpha-scene-director textarea:focus,.alpha-scene-director select:focus { outline:2px solid var(--asd-accent); outline-offset:1px; }
    .alpha-scene-director button { width:auto; padding:8px 12px; border:1px solid #69788e; border-radius:6px; background:#2c3748; color:#f4f7fc; font:600 13px/1.35 system-ui,sans-serif; cursor:pointer; }
    .alpha-scene-director button:hover:enabled { background:#3b4a61; }
    .alpha-scene-director button:focus-visible { outline:2px solid var(--asd-accent); outline-offset:2px; }
    .alpha-scene-director button:disabled { opacity:.48; cursor:default; }
    .alpha-scene-director button.asd-primary { background:#c8dfff; color:#102238; border-color:#c8dfff; }
    .alpha-scene-director button.asd-primary:hover:enabled { background:#e0edff; }
    .alpha-scene-director .asd-actions { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
    .alpha-scene-director .asd-between { justify-content:space-between; }
    .alpha-scene-director .asd-image-row { display:flex; gap:8px; align-items:center; }
    .alpha-scene-director .asd-image-row input { flex:1; }
    .alpha-scene-director .asd-preview { display:flex; gap:10px; align-items:center; min-height:38px; }
    .alpha-scene-director .asd-preview img { width:112px; height:72px; object-fit:contain; border:1px solid var(--asd-border); border-radius:4px; background:#10141a; }
    .alpha-scene-director .asd-preview [hidden] { display:none; }
    .alpha-scene-director .asd-scene { border:1px solid var(--asd-border); border-radius:8px; overflow:hidden; background:var(--asd-panel); }
    .alpha-scene-director .asd-scene>summary { cursor:pointer; padding:12px 14px; font-weight:650; }
    .alpha-scene-director .asd-scene>summary:focus-visible { outline:2px solid var(--asd-accent); outline-offset:-3px; }
    .alpha-scene-director .asd-scene-body { border-top:1px solid var(--asd-border); padding:14px; display:grid; gap:14px; }
    .alpha-scene-director .asd-scene-count { color:var(--asd-muted); font-weight:400; margin-left:10px; }
    .alpha-scene-director .asd-advanced { display:grid; gap:10px; }
    .alpha-scene-director .asd-advanced summary { cursor:pointer; color:var(--asd-accent); padding:4px 0; }
    .alpha-scene-director .asd-advanced textarea { font:12px/1.5 ui-monospace,monospace; margin-top:10px; }
    .alpha-scene-director .asd-advanced pre { white-space:pre-wrap; font:12px/1.5 ui-monospace,monospace; color:var(--asd-muted); }
    .alpha-scene-director .asd-plan-summary { font-weight:600; }
    .alpha-scene-director .asd-message { white-space:pre-wrap; overflow-wrap:anywhere; color:#dbe9ff; font-size:13px; }
    .alpha-scene-director .asd-message[data-kind=error] { color:#ffc0b5; }
    .alpha-scene-director .asd-message:empty { display:none; }
    .alpha-scene-director .asd-footer { padding:14px 24px; border-top:1px solid var(--asd-border); display:grid; gap:8px; background:var(--asd-bg); }
    .alpha-scene-director .asd-job-status { margin-top:12px; white-space:pre-wrap; }
    .alpha-scene-director progress { width:100%; height:14px; accent-color:#a8d0ff; }
    .alpha-scene-director .asd-clips { max-height:260px; overflow:auto; border-top:1px solid var(--asd-border); margin-top:12px; }
    .alpha-scene-director table { border-collapse:collapse; width:100%; text-align:left; font-size:12px; }
    .alpha-scene-director th,.alpha-scene-director td { padding:8px; border-bottom:1px solid var(--asd-border); vertical-align:top; overflow-wrap:anywhere; }
    .alpha-scene-director th { position:sticky; top:0; background:var(--asd-panel); }
    .alpha-scene-director .asd-file { display:block; color:var(--asd-muted); }
    .alpha-scene-director .asd-job-id { font-family:ui-monospace,monospace; }
    .alpha-scene-director .asd-hidden { display:none; }
    .alpha-scene-director .asd-eyebrow { color:var(--asd-accent); font-size:11px; font-weight:800; letter-spacing:.18em; text-transform:uppercase; }
    .alpha-scene-director .asd-direction { grid-template-columns:repeat(4,minmax(0,1fr)); padding:12px; border:1px solid #303a49; border-radius:8px; background:#141922; }
    @media(max-width:900px) { .alpha-scene-director .asd-direction { grid-template-columns:repeat(2,minmax(0,1fr)); } }
    @media(max-width:720px) { .alpha-scene-director .asd-grid,.alpha-scene-director .asd-three,.alpha-scene-director .asd-direction { grid-template-columns:1fr; } .alpha-scene-director .asd-header,.alpha-scene-director .asd-body,.alpha-scene-director .asd-footer { padding:14px; } }
  `;
  document.head.append(style);
}

class SceneDirectorDialog {
  constructor(node) {
    installStyle();
    this.node = node;
    this.previewDisposers = new Set();
    this.job = null;
    this.jobId = String(node.properties?.alphaDirectorJobId || "");
    this.pollTimer = null;
    this.pollGeneration = 0;
    this.draftTimer = null;
    this.busy = false;
    this.uncertainStart = false;
    this.jobs = [];
    this.readError = "";
    this.protectExistingPlan = false;
    this.plan = this.initialPlan();
    this.dialog = element("dialog", "alpha-scene-director");
    this.dialog.setAttribute("aria-label", "Alpha Scene Director");
    this.build();
    this.dialog.addEventListener("cancel", () => this.saveOnClose());
    this.dialog.addEventListener("close", () => {
      clearTimeout(this.pollTimer);
      this.pollGeneration += 1;
      clearTimeout(this.draftTimer);
    });
    document.body.append(this.dialog);
  }

  initialPlan() {
    const raw = widget(this.node, "scene_plan")?.value;
    if (typeof raw === "string" && raw.trim()) {
      try {
        const plan = parsePlan(raw);
        if (plan.scenes.length) return plan;
      } catch (error) {
        this.protectExistingPlan = true;
        this.readError = `The existing scene_plan could not be read: ${error.message}\nIt has not been changed. Import a corrected plan or save a new draft below.`;
      }
    }
    const plan = createDefaultPlan();
    plan.global_prompt = String(widget(this.node, "positive")?.value || "");
    plan.negative_prompt = String(widget(this.node, "negative")?.value || "");
    for (const name of ["first_image", "last_image", "frames", "fps", "seed"]) {
      const value = widget(this.node, name)?.value;
      if (value !== undefined && value !== null) plan.defaults[name] = value;
    }
    return plan;
  }

  button(label, action, className) {
    const node = element("button", className, label);
    node.type = "button";
    node.addEventListener("click", async (event) => {
      event.preventDefault();
      try { await action(event); } catch (error) { this.message(error.message, "error"); }
    });
    return node;
  }

  build() {
    const shell = element("div", "asd-shell");
    const header = element("header", "asd-header");
    const heading = element("div");
    heading.append(element("p", "asd-eyebrow", "ALPHABETA · MOVIE CONTROL"), element("h2", "", "Cinematic Studio"), element("p", "", "Write the film, direct each scene, choose its endpoints, then run your connected ComfyUI video workflow."));
    header.append(heading, this.button("Close", () => { this.saveOnClose(); this.dialog.close(); }));
    const body = element("main", "asd-body");
    const intro = element("div", "asd-stack");
    intro.append(element("p", "asd-help", "Plan 8–12 scenes, or use one scene for a short test. Each clip is a separate render. The first clip starts at its chosen image; later starts default to @previous, the actual last frame of the preceding completed clip."));
    const fileActions = element("div", "asd-actions");
    this.importFile = input("", "file");
    this.importFile.accept = ".json,application/json";
    this.importFile.className = "asd-hidden";
    this.importFile.addEventListener("change", async () => {
      const file = this.importFile.files?.[0];
      if (!file) return;
      try {
        if (file.size > 16 * 1024 * 1024) throw new Error("Plan JSON must be smaller than 16 MiB.");
        const imported = parsePlan(await file.text());
        if (!imported.scenes.length) throw new Error("Import at least one scene.");
        this.plan = imported;
        this.renderPlan();
        this.saveDraft(true);
        this.message("Plan imported. Validate the draft and the connected graph before starting.");
      } catch (error) { this.message(error.message, "error"); }
      this.importFile.value = "";
    });
    fileActions.append(this.button("Import plan JSON", () => this.importFile.click()), this.button("Export plan JSON", () => this.exportPlan()), this.importFile);
    intro.append(fileActions);
    this.planHost = element("div", "asd-stack");
    this.planHost.addEventListener("input", () => {
      clearTimeout(this.draftTimer);
      this.draftTimer = setTimeout(() => {
        try { this.saveDraft(); } catch (error) { this.draftStatus.textContent = `Draft not saved: ${error.message}`; }
      }, 250);
    });
    this.renderPlan();
    this.graphSection = this.buildGraphSection();
    const jobSection = this.buildJobSection();
    body.append(intro, this.planHost, this.graphSection, jobSection);
    const footer = element("footer", "asd-footer");
    this.messageNode = element("div", "asd-message");
    this.messageNode.setAttribute("role", "status");
    this.messageNode.setAttribute("aria-live", "polite");
    this.draftStatus = element("div", "asd-help", "Drafts save into this node as you edit. Save the ComfyUI workflow to keep your draft after a browser refresh.");
    const actions = element("div", "asd-actions asd-between");
    this.planSummary = element("span", "asd-plan-summary");
    const runActions = element("div", "asd-actions");
    this.validateButton = this.button("Validate plan & graph", () => this.submit(true));
    this.startButton = this.button("Start new job", () => this.submit(false), "asd-primary");
    runActions.append(this.button("Save draft", () => { this.saveDraft(true); this.message("Draft saved into this node. Save your ComfyUI workflow to keep it after a browser refresh."); }), this.validateButton, this.startButton);
    actions.append(this.planSummary, runActions);
    footer.append(this.messageNode, this.draftStatus, actions);
    shell.append(header, body, footer);
    this.dialog.append(shell);
    this.updateSummary();
    this.updateJobButtons();
    if (this.readError) {
      this.message(this.readError, "error");
      this.draftStatus.textContent = "Existing scene_plan preserved. Choose Save draft to replace it, or import corrected JSON.";
    }
  }

  renderPlan() {
    for (const dispose of this.previewDisposers) dispose();
    this.previewDisposers.clear();
    this.planHost.replaceChildren();
    const defaults = this.plan.defaults || {};
    const global = element("section", "asd-section asd-stack");
    global.append(element("h3", "", "1. Film defaults"));
    this.globalControls = {
      project: input(this.plan.project || ""),
      prompt: textarea(this.plan.global_prompt || "", 3),
      negative: textarea(this.plan.negative_prompt || "", 2),
      frames: numeric(defaults.frames ?? 81, 2, 4097),
      fps: numeric(defaults.fps ?? 16, 0.01, 240, "any"),
      seed: numeric(defaults.seed ?? 1, 0, Number.MAX_SAFE_INTEGER),
    };
    global.append(field("Project name", this.globalControls.project));
    const prompts = element("div", "asd-grid");
    prompts.append(field("Global prompt", this.globalControls.prompt, "Appended before every scene and clip prompt. At least one of those prompts must be nonempty for every clip."), field("Global negative prompt", this.globalControls.negative));
    const endpoints = element("div", "asd-grid");
    this.globalControls.first = this.imageField("Global first image", defaults.first_image || "", "The first clip's start image. Upload or enter an image path relative to ComfyUI input.");
    this.globalControls.last = this.imageField("Global last target", defaults.last_image || "", "Fallback last target for every clip without a scene or clip-specific target.");
    endpoints.append(this.globalControls.first.container, this.globalControls.last.container);
    const settings = element("div", "asd-grid asd-three");
    settings.append(field("Frames per clip", this.globalControls.frames, "Use a frame count supported by your model."), field("Frames per second", this.globalControls.fps), field("Default seed", this.globalControls.seed, "Scene and clip overrides can set explicit seeds."));
    global.append(prompts, endpoints, settings, element("p", "asd-help", "Duration estimates sum frames ÷ fps before trimming. Overlaps, trimming and model constraints can change the final film length."));
    const sceneHeader = element("div", "asd-actions asd-between");
    sceneHeader.append(element("h3", "", `2. Scenes (${this.plan.scenes.length}/12)`));
    const sceneActions = element("div", "asd-actions");
    this.addSceneButton = this.button("Add scene", () => this.changeScenes("add"));
    this.addSceneButton.disabled = this.plan.scenes.length >= 12;
    sceneActions.append(this.button("Expand scenes", () => this.sceneControls.forEach((item) => { item.details.open = true; })), this.button("Collapse scenes", () => this.sceneControls.forEach((item) => { item.details.open = false; })), this.addSceneButton);
    sceneHeader.append(sceneActions);
    const sceneHelp = element("p", "asd-help", "Repeating a scene prompt is supported. Same scene target applies to every clip unless you enter clip-specific endpoints. Use advanced JSON for written prompt points and intermediate targets; clip prompts are appended to global + scene text.");
    const sceneList = element("div", "asd-stack");
    this.sceneControls = this.plan.scenes.map((scene, index) => {
      const controls = this.buildScene(scene, index);
      sceneList.append(controls.details);
      return controls;
    });
    this.planHost.append(global, sceneHeader, sceneHelp, sceneList);
    if (this.planSummary) this.updateSummary();
  }

  imageField(label, value, help) {
    const container = element("div", "asd-field");
    const path = input(value);
    path.placeholder = "relative/path.png";
    path.setAttribute("aria-label", label);
    const row = element("div", "asd-image-row");
    const file = input("", "file");
    file.accept = "image/png,image/jpeg,image/webp";
    file.className = "asd-hidden";
    const upload = this.button("Upload", () => file.click());
    upload.setAttribute("aria-label", `Upload ${label.toLowerCase()}`);
    row.append(path, upload, file);
    const preview = element("div", "asd-preview");
    const image = element("img");
    image.alt = `${label} preview`;
    image.hidden = true;
    const note = element("span", "asd-help");
    preview.append(image, note);
    container.append(element("span", "asd-label", label), row, element("span", "asd-help", help), preview);
    let objectURL = null;
    let controller = null;
    let generation = 0;
    let timer;
    const dispose = () => {
      generation += 1;
      controller?.abort();
      clearTimeout(timer);
      if (objectURL) URL.revokeObjectURL(objectURL);
      objectURL = null;
    };
    const refresh = async () => {
      dispose();
      const current = generation;
      image.hidden = true;
      image.removeAttribute("src");
      const value = path.value.trim();
      if (!value || value === "@previous") {
        note.textContent = value === "@previous" ? "Uses the previous clip's actual last frame." : "No explicit image. Inheritance rules apply.";
        return;
      }
      note.textContent = "Loading preview…";
      controller = new AbortController();
      try {
        const blob = await client.preview(value, controller.signal);
        if (current !== generation || !blob) return;
        objectURL = URL.createObjectURL(blob);
        image.src = objectURL;
        image.hidden = false;
        note.textContent = "Input image";
      } catch (error) {
        if (current === generation && error.name !== "AbortError") note.textContent = `Preview unavailable: ${error.message}`;
      }
    };
    path.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(refresh, 550); });
    file.addEventListener("change", async () => {
      const selected = file.files?.[0];
      if (!selected) return;
      upload.disabled = true;
      note.textContent = "Uploading…";
      try {
        path.value = await client.upload(selected);
        path.dispatchEvent(new Event("input", { bubbles: true }));
        await refresh();
      } catch (error) { note.textContent = `Upload failed: ${error.message}`; }
      upload.disabled = false;
      file.value = "";
    });
    this.previewDisposers.add(dispose);
    if (value) void refresh();
    else note.textContent = "No explicit image. Inheritance rules apply.";
    return { container, path };
  }

  buildScene(scene, index) {
    const details = element("details", "asd-scene");
    details.open = index === 0;
    const summary = element("summary");
    const summaryTitle = element("span", "", `${index + 1}. ${scene.title || "Untitled scene"}`);
    const summaryCount = element("span", "asd-scene-count", `${Array.isArray(scene.clips) ? scene.clips.length : scene.clips ?? 1} clips`);
    summary.append(summaryTitle, summaryCount);
    const body = element("div", "asd-scene-body");
    const sceneActions = element("div", "asd-actions");
    const up = this.button("Move up", () => this.changeScenes("up", index));
    const down = this.button("Move down", () => this.changeScenes("down", index));
    const remove = this.button("Remove scene", () => this.changeScenes("remove", index));
    up.disabled = index === 0;
    down.disabled = index === this.plan.scenes.length - 1;
    remove.disabled = this.plan.scenes.length <= 1;
    sceneActions.append(up, down, remove);
    const title = input(scene.title || "");
    const prompt = textarea(scene.prompt || "", 3);
    const direction = scene.direction || {};
    const directionControls = {
      shot: choice(direction.shot, CINEMATIC_CHOICES.shot),
      camera: choice(direction.camera, CINEMATIC_CHOICES.camera),
      light: choice(direction.light, CINEMATIC_CHOICES.light),
      look: choice(direction.look, CINEMATIC_CHOICES.look),
    };
    const clipCount = numeric(Array.isArray(scene.clips) ? scene.clips.length : scene.clips ?? 1, 1, 1000);
    const top = element("div", "asd-grid");
    top.append(field("Scene title", title), field("Repeated clip count", clipCount, "Repeats the scene prompt. A clips array in advanced JSON sets its own length."));
    const first = this.imageField("Scene first image", scene.first_image || "", "Optional: used only for this scene's first clip. Leave blank for continuity; later starts default to @previous.");
    const last = this.imageField("Scene last target", scene.last_image || "", "Optional: leave blank to inherit the global last target. This target repeats unless a clip overrides it.");
    const endpoints = element("div", "asd-grid");
    endpoints.append(first.container, last.container);
    const advanced = element("details", "asd-advanced");
    advanced.append(element("summary", "", "Advanced: per-clip prompts, targets and model settings"));
    const advancedText = textarea(sceneAdvancedJSON(scene), 8);
    advancedText.setAttribute("aria-label", `Advanced JSON for scene ${index + 1}`);
    advancedText.spellcheck = false;
    const advancedNote = element("p", "asd-help");
    const updateAdvanced = () => {
      try {
        const parsed = JSON.parse(advancedText.value.trim() || "{}");
        const explicit = Array.isArray(parsed.clips);
        clipCount.disabled = explicit;
        if (explicit) clipCount.value = parsed.clips.length;
        advancedNote.textContent = explicit ? `${parsed.clips.length} explicit clips. Remove the clips array to use the repeated clip count.` : "Optional scene keys: clips (an array), frames, fps, seed, negative_prompt, input_overrides. Keep basic scene fields above.";
      } catch {
        advancedNote.textContent = "Advanced JSON is incomplete. Fix it to save or run this draft.";
      }
    };
    advancedText.addEventListener("input", updateAdvanced);
    updateAdvanced();
    const example = element("details");
    example.append(element("summary", "asd-help", "Show JSON example"), element("pre", "", '{\n  "clips": [\n    {"prompt": "First written beat", "last_image": "targets/beat-01.png"},\n    {"prompt": "Next written beat", "last_image": "targets/beat-02.png"}\n  ],\n  "input_overrides": {"42": {"cfg": 4.5}}\n}'));
    advanced.append(advancedText, advancedNote, element("p", "asd-help", "Overrides address existing node IDs and scalar widget inputs only. They cannot replace graph links or director/checkpoint controls. Use settings that your connected model supports."), example);
    const directionGrid = element("div", "asd-grid asd-direction");
    directionGrid.append(
      field("Shot", directionControls.shot), field("Camera", directionControls.camera),
      field("Light", directionControls.light), field("Look", directionControls.look),
    );
    body.append(sceneActions, top, field("Scene prompt", prompt), directionGrid,
      element("p", "asd-help", "These choices add controlled words to the prompt; they do not physically simulate a lens, light source or film stock."), endpoints, advanced);
    details.append(summary, body);
    return { details, title, prompt, direction: directionControls, clipCount, first, last, advancedText, summaryTitle, summaryCount };
  }

  collectPlan() {
    const controls = this.globalControls;
    return {
      ...this.plan,
      project: controls.project.value,
      global_prompt: controls.prompt.value,
      negative_prompt: controls.negative.value,
      defaults: {
        ...this.plan.defaults,
        first_image: controls.first.path.value.trim(),
        last_image: controls.last.path.value.trim(),
        frames: Number(controls.frames.value),
        fps: Number(controls.fps.value),
        seed: Number(controls.seed.value),
      },
      scenes: this.sceneControls.map((controls, index) => {
        try {
          return editScene(this.plan.scenes[index], {
            title: controls.title.value,
            prompt: controls.prompt.value,
            direction: Object.fromEntries(Object.entries(controls.direction).map(([key, control]) => [key, control.value])),
            first_image: controls.first.path.value,
            last_image: controls.last.path.value,
            clipCount: controls.clipCount.value,
            advancedJSON: controls.advancedText.value,
          });
        } catch (error) { throw new Error(`Scene ${index + 1}: ${error.message}`); }
      }),
    };
  }

  saveDraft(explicitSave = false) {
    clearTimeout(this.draftTimer);
    const plan = this.collectPlan();
    const serialized = serializeDraftForNode(plan, { protectExisting: this.protectExistingPlan, explicitSave });
    this.plan = plan;
    this.updateSummary();
    if (serialized === null) {
      this.draftStatus.textContent = "Existing scene_plan preserved. Choose Save draft to replace it, or import corrected JSON.";
      return parsePlan(plan);
    }
    const destination = widget(this.node, "scene_plan");
    if (!destination) throw new Error("The AlphaSceneDirector node has no scene_plan widget. Reload the custom nodes and frontend.");
    destination.value = serialized;
    this.protectExistingPlan = false;
    this.readError = "";
    this.node.graph?.setDirtyCanvas?.(true, true);
    this.draftStatus.textContent = "Draft saved into this node. Save the ComfyUI workflow to keep it after a browser refresh.";
    return parsePlan(plan);
  }

  saveOnClose() {
    try { this.saveDraft(); } catch (error) { this.message(`Unsaved draft: ${error.message}. Reopen this dialog to correct it.`, "error"); }
  }

  updateSummary() {
    const report = inspectPlan(this.plan);
    this.planSummary.textContent = `${report.totalClips} clips · Film estimate before trimming: ${formatDuration(report.estimatedSeconds)}`;
    this.planSummary.title = `${report.errors.length} draft preflight issue(s). Validate to see details. Render time is shown separately under job progress.`;
    this.sceneControls.forEach((controls, index) => {
      const scene = this.plan.scenes[index];
      controls.summaryTitle.textContent = `${index + 1}. ${scene.title || "Untitled scene"}`;
      controls.summaryCount.textContent = `${Array.isArray(scene.clips) ? scene.clips.length : scene.clips ?? 1} clips`;
    });
  }

  changeScenes(action, index) {
    this.saveDraft();
    const scenes = this.plan.scenes;
    if (action === "add" && scenes.length < 12) {
      const next = createDefaultPlan(1).scenes[0];
      next.id = `scene-${Date.now().toString(36)}-${scenes.length + 1}`;
      next.title = `Scene ${scenes.length + 1}`;
      scenes.push(next);
    } else if (action === "remove" && scenes.length > 1) scenes.splice(index, 1);
    else if (action === "up" && index > 0) [scenes[index - 1], scenes[index]] = [scenes[index], scenes[index - 1]];
    else if (action === "down" && index < scenes.length - 1) [scenes[index], scenes[index + 1]] = [scenes[index + 1], scenes[index]];
    this.renderPlan();
    this.saveDraft();
    if (action === "add") this.sceneControls.at(-1).details.open = true;
  }

  exportPlan() {
    const plan = this.saveDraft();
    const blob = new Blob([serializePlan(plan)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = element("a");
    link.href = url;
    link.download = `${(plan.project || "scene-plan").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 80) || "scene-plan"}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  buildGraphSection() {
    const section = element("section", "asd-section asd-stack");
    section.append(element("h3", "", "3. Connected workflow"), element("p", "asd-help", "Wire this Director through your existing first/last-frame video workflow. Select the checkpoint that receives the decoded frames and the output that saves the video. Start freezes the current API graph and plan; edits after Start apply only to a future job."));
    this.checkpoint = element("select");
    this.videoOutput = element("select");
    this.manualOutput = input("");
    this.manualOutput.placeholder = "Custom output node ID";
    const fields = element("div", "asd-grid");
    fields.append(field("AlphaSceneCheckpoint node", this.checkpoint), field("Video output node", this.videoOutput, "Recognizes SaveVideo and VHS_VideoCombine."));
    section.append(fields, field("Custom video output ID (optional)", this.manualOutput, "An explicit ID takes precedence over the selector. Use an output whose history returns saved video files; server validation checks the graph."), this.button("Refresh node choices", () => this.refreshGraph()));
    for (const control of [this.checkpoint, this.videoOutput, this.manualOutput]) control.addEventListener("change", () => this.rememberSelections());
    return section;
  }

  populateSelector(select, choices, selected, placeholder) {
    select.replaceChildren();
    const empty = element("option", "", placeholder);
    empty.value = "";
    select.append(empty);
    for (const choice of choices) {
      const option = element("option", "", `${choice.id} · ${choice.title} (${choice.type})`);
      option.value = choice.id;
      select.append(option);
    }
    if (choices.some((choice) => choice.id === selected)) select.value = selected;
    else if (choices.length === 1) select.value = choices[0].id;
  }

  async refreshGraph() {
    const graph = await app.graphToPrompt();
    const choices = workflowChoices(graph.output);
    const saved = this.node.properties || {};
    const checkpoint = this.checkpoint.value || String(saved.alphaDirectorCheckpointId || "");
    const video = this.videoOutput.value || String(saved.alphaDirectorVideoOutputId || "");
    this.populateSelector(this.checkpoint, choices.checkpoints, checkpoint, "Select a checkpoint…");
    this.populateSelector(this.videoOutput, choices.videos, video, "Select a video output…");
    if (video && !choices.videos.some((choice) => choice.id === video) && !this.manualOutput.value) this.manualOutput.value = video;
    this.rememberSelections();
  }

  rememberSelections() {
    this.node.properties ||= {};
    this.node.properties.alphaDirectorCheckpointId = this.checkpoint.value;
    this.node.properties.alphaDirectorVideoOutputId = this.manualOutput.value.trim() || this.videoOutput.value;
    this.node.graph?.setDirtyCanvas?.(true, true);
  }

  async submit(validateOnly) {
    if (this.busy) return;
    if (this.protectExistingPlan) throw new Error("The original scene_plan is unreadable and has been preserved. Choose Save draft to replace it, or import corrected JSON, before validation or Start.");
    if (!validateOnly && this.uncertainStart) throw new Error("Refresh saved jobs and review the list before starting another job.");
    this.busy = true;
    this.updateJobButtons();
    let attemptedStart = false;
    try {
      const plan = this.saveDraft();
      this.rememberSelections();
      const checkpointId = this.checkpoint.value;
      const videoOutputId = this.manualOutput.value.trim() || this.videoOutput.value;
      this.message("Capturing the workflow and validating all static endpoints…");
      const graph = await app.graphToPrompt();
      const request = buildSubmission({ plan, graph, directorId: this.node.id, checkpointId, videoOutputId });
      await client.validate(request);
      if (validateOnly) {
        this.message("Plan, static images and graph passed server preflight. Model compatibility and a live render still depend on your connected workflow.");
        return;
      }
      this.message("Starting the saved job…");
      attemptedStart = true;
      const job = await client.start(request);
      if (!job?.job_id) throw new Error("The Start response did not contain a job ID.");
      this.setJob(job);
      this.message(`Job ${job.job_id} started. You can close the browser; use Saved jobs to return.`);
      void this.refreshJobs(false).catch((error) => this.message(`Job started, but the saved-job list could not refresh: ${error.message}`, "error"));
      this.schedulePoll();
    } catch (error) {
      if (attemptedStart && (!error.httpStatus || error.httpStatus >= 500)) {
        this.uncertainStart = true;
        this.message(`Start did not return a confirmed result: ${error.message}\nThe server may have accepted it. Refresh saved jobs and review the list before starting again.`, "error");
      } else this.message(error.message, "error");
    } finally {
      this.busy = false;
      this.updateJobButtons();
    }
  }

  buildJobSection() {
    const section = element("section", "asd-section asd-stack");
    section.append(element("h3", "", "4. Saved jobs and progress"), element("p", "asd-help", "Jobs are saved on the ComfyUI server. A browser refresh does not stop a run. After a server restart, restore the job and choose Resume. Pause finishes the current clip first. Resume uses the job's frozen plan and workflow, regardless of draft edits above."));
    this.savedJobs = element("select");
    this.savedJobs.append(element("option", "", "Refresh to load saved jobs…"));
    this.savedJobs.addEventListener("change", () => { if (this.savedJobs.value) this.jobInput.value = this.savedJobs.value; });
    this.jobInput = input(this.jobId);
    this.jobInput.className = "asd-job-id";
    this.jobInput.placeholder = "Paste a saved job ID";
    const fields = element("div", "asd-grid");
    fields.append(field("Saved jobs", this.savedJobs), field("Job ID", this.jobInput));
    const actions = element("div", "asd-actions");
    this.restoreButton = this.button("Restore job", () => this.restoreJob());
    this.refreshJobButton = this.button("Refresh progress", () => this.refreshJob());
    this.pauseButton = this.button("Pause after current clip", () => this.jobAction("pause"));
    this.resumeButton = this.button("Resume saved job", () => this.jobAction("resume"));
    actions.append(this.button("Refresh saved jobs", () => this.refreshJobs(true)), this.restoreButton, this.refreshJobButton, this.pauseButton, this.resumeButton);
    this.jobStatus = element("div", "asd-job-status", "No saved job selected.");
    this.jobStatus.setAttribute("role", "status");
    this.progress = element("progress");
    this.progress.max = 1;
    this.progress.value = 0;
    this.progress.setAttribute("aria-label", "Completed clips");
    this.clipList = element("div", "asd-clips");
    section.append(fields, actions, this.jobStatus, this.progress, this.clipList);
    return section;
  }

  async refreshJobs(clearUncertainty = false) {
    const jobs = await client.list();
    const selected = this.savedJobs.value || this.jobId;
    this.savedJobs.replaceChildren();
    const placeholder = element("option", "", jobs.length ? "Choose a saved job…" : "No saved jobs yet");
    placeholder.value = "";
    this.savedJobs.append(placeholder);
    for (const job of jobs) {
      const option = element("option", "", `${job.project || "Untitled film"} · ${job.status} · ${job.completed}/${job.total} clips · ${job.job_id}`);
      option.value = job.job_id;
      this.savedJobs.append(option);
    }
    this.savedJobs.value = jobs.some((job) => job.job_id === selected) ? selected : "";
    this.jobs = jobs;
    const selectedSnapshot = jobs.find((job) => job.job_id === this.jobId);
    if (selectedSnapshot) this.setJob(selectedSnapshot);
    if (clearUncertainty) {
      const wasUncertain = this.uncertainStart;
      this.uncertainStart = false;
      if (wasUncertain) this.message("Saved jobs refreshed. Review and restore any recently created job before starting another.");
    }
    this.updateJobButtons();
  }

  setJob(job) {
    if (!job || typeof job.job_id !== "string") throw new Error("ComfyUI returned an invalid job response.");
    if (job.job_id === this.job?.job_id && Number(job.updated_at) < Number(this.job.updated_at)) return;
    this.job = job;
    this.jobId = job.job_id;
    this.jobInput.value = job.job_id;
    this.node.properties ||= {};
    this.node.properties.alphaDirectorJobId = job.job_id;
    this.node.graph?.setDirtyCanvas?.(true, true);
    const current = typeof job.current === "string" ? `\nCurrent / next clip: ${job.current}` : "";
    const remaining = typeof job.estimated_seconds === "number" ? `\nEstimated render time remaining: ${formatDuration(job.estimated_seconds)}` : "\nRender-time estimate: waiting for completed-clip timings.";
    this.jobStatus.textContent = `Job ${job.job_id}\n${job.status} · ${job.completed}/${job.total} clips completed${current}${remaining}${job.error ? `\n${typeof job.error === "string" ? job.error : JSON.stringify(job.error)}` : ""}`;
    this.progress.max = Number(job.total) > 0 ? Number(job.total) : 1;
    this.progress.value = Number(job.completed) || 0;
    this.renderClips(Array.isArray(job.clips) ? job.clips : []);
    this.updateJobButtons();
  }

  renderClips(clips) {
    const table = element("table");
    const head = element("thead");
    const heading = element("tr");
    for (const name of ["Clip", "Scene", "Status", "Saved video / error"]) heading.append(element("th", "", name));
    head.append(heading);
    const body = element("tbody");
    for (const clip of clips) {
      const row = element("tr");
      row.append(element("td", "", String(clip.id || "")), element("td", "", String(clip.scene_title || "")), element("td", "", String(clip.status || "")));
      const output = element("td");
      for (const file of Array.isArray(clip.video_files) ? clip.video_files : []) {
        const name = typeof file === "string" ? file : (file.relative_path || file.path || file.filename || JSON.stringify(file));
        output.append(element("span", "asd-file", name));
      }
      if (clip.error) output.append(element("span", "", typeof clip.error === "string" ? clip.error : JSON.stringify(clip.error)));
      row.append(output);
      body.append(row);
    }
    table.append(head, body);
    this.clipList.replaceChildren(table);
  }

  async restoreJob() {
    const id = this.jobInput.value.trim();
    if (!id) throw new Error("Choose a saved job or paste its job ID.");
    clearTimeout(this.pollTimer);
    const generation = ++this.pollGeneration;
    const job = await client.get(id);
    if (generation !== this.pollGeneration) return;
    this.setJob(job);
    this.message(`Restored job ${id}. Resume continues its saved plan and workflow.`);
    this.schedulePoll();
  }

  async refreshJob() {
    if (!this.jobId) return this.restoreJob();
    const generation = this.pollGeneration;
    const id = this.jobId;
    const job = await client.get(id);
    if (generation !== this.pollGeneration || id !== this.jobId) return;
    this.setJob(job);
    this.schedulePoll();
  }

  schedulePoll(delay = 2500) {
    clearTimeout(this.pollTimer);
    if (!this.dialog.open || !this.jobId || !ACTIVE_STATUSES.has(this.job?.status)) return;
    this.pollTimer = setTimeout(async () => {
      try { await this.refreshJob(); }
      catch (error) {
        this.message(`Progress connection interrupted: ${error.message}\nThe server job may still be running. Refresh progress to reconnect.`, "error");
        this.schedulePoll(7000);
      }
    }, delay);
  }

  async jobAction(action) {
    if (this.busy || !this.jobId) return;
    this.busy = true;
    clearTimeout(this.pollTimer);
    ++this.pollGeneration;
    this.updateJobButtons();
    try {
      const job = await client[action](this.jobId);
      this.setJob(job);
      this.message(action === "pause" ? "Pause requested. Any current clip finishes before the job stops." : "Resume requested for the saved job. Completed clips are retained.");
      this.schedulePoll();
    } catch (error) { this.message(error.message, "error"); }
    finally { this.busy = false; this.updateJobButtons(); }
  }

  updateJobButtons() {
    if (!this.startButton) return;
    const active = ACTIVE_STATUSES.has(this.job?.status);
    const anyActive = activeJobsRemain(this.jobs, this.job);
    this.startButton.disabled = this.busy || this.uncertainStart || anyActive;
    this.startButton.title = this.uncertainStart ? "Refresh saved jobs after an uncertain Start response." : (anyActive ? "A job is active. Restore it to review or pause it." : "Validate and start a new saved job from this draft.");
    this.validateButton.disabled = this.busy;
    this.pauseButton.disabled = this.busy || !this.jobId || !active;
    this.resumeButton.disabled = this.busy || !this.jobId || active || ["completed", "complete"].includes(this.job?.status);
    this.refreshJobButton.disabled = this.busy || !this.jobId;
    this.restoreButton.disabled = this.busy;
  }

  message(text, kind = "info") {
    if (!this.messageNode) return;
    this.messageNode.textContent = text;
    this.messageNode.dataset.kind = kind;
  }

  async open() {
    if (!this.dialog.open) this.dialog.showModal();
    const results = await Promise.allSettled([this.refreshGraph(), this.refreshJobs()]);
    const failures = results.filter((result) => result.status === "rejected").map((result) => result.reason.message);
    if (failures.length) this.message(failures.join("\n"), "error");
    if (this.jobId) {
      try { await this.restoreJob(); } catch (error) { this.message(`Saved job could not be restored: ${error.message}`, "error"); }
    }
  }

  dispose() {
    clearTimeout(this.pollTimer);
    clearTimeout(this.draftTimer);
    this.pollGeneration += 1;
    for (const dispose of this.previewDisposers) dispose();
    this.previewDisposers.clear();
    this.dialog.remove();
  }
}

app.registerExtension({
  name: "AlphaSceneDirector.SceneEditor",
  nodeCreated(node) {
    if (node.comfyClass !== "AlphaSceneDirector" && node.type !== "AlphaSceneDirector") return;
    const button = node.addWidget("button", "Open Scene Director", null, () => {
      let editor = editors.get(node);
      if (!editor) {
        editor = new SceneDirectorDialog(node);
        editors.set(node, editor);
      }
      void editor.open().catch((error) => editor.message(error.message, "error"));
    }, { serialize: false });
    button.serialize = false;
    button.options = { ...button.options, serialize: false };
    const previous = node.onRemoved;
    node.onRemoved = function (...args) {
      editors.get(this)?.dispose();
      editors.delete(this);
      return previous?.apply(this, args);
    };
  },
});
