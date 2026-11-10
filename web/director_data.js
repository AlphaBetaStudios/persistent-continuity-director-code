/** Browser-independent draft handling. The server remains the final preflight authority. */
const BASIC_SCENE_FIELDS = new Set(["id", "title", "prompt", "direction", "first_image", "last_image", "clips"]);
const FALLBACKS = { frames: 81, fps: 16, seed: 1 };
export const CINEMATIC_CHOICES = {
  shot: ["Wide establishing shot", "Medium shot", "Close-up portrait", "Low-angle shot", "Overhead shot"],
  camera: ["35mm lens, shallow depth of field", "Wide-angle lens, deep focus", "85mm portrait lens, soft background", "Handheld documentary camera"],
  light: ["Soft window light", "Golden-hour backlight", "Hard noon sunlight", "Blue-hour practical lights"],
  look: ["Warm cinematic film", "Cool science-fiction film", "Natural documentary color", "High-contrast noir"],
};

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function jsonObject(raw, label) {
  let value;
  try {
    value = typeof raw === "string" ? JSON.parse(raw) : clone(raw);
  } catch (error) {
    throw new Error(`${label} must be valid JSON: ${error.message}`);
  }
  if (!isObject(value)) throw new Error(`${label} must be a JSON object.`);
  return value;
}

export function createDefaultPlan(sceneCount = 10) {
  if (!Number.isInteger(sceneCount) || sceneCount < 1 || sceneCount > 12) {
    throw new Error("Choose 1–12 scenes; 8–12 is typical for a film plan.");
  }
  return {
    version: 1,
    project: "Untitled film",
    global_prompt: "",
    negative_prompt: "",
    defaults: { first_image: "", last_image: "", ...FALLBACKS },
    scenes: Array.from({ length: sceneCount }, (_, index) => ({
      id: `scene-${String(index + 1).padStart(2, "0")}`,
      title: `Scene ${index + 1}`,
      prompt: "",
      clips: 6,
    })),
  };
}

/** Drafts may be incomplete, so importing does not demand render-ready endpoints. */
export function parsePlan(raw) {
  const plan = jsonObject(raw, "Plan");
  if (plan.version !== 1) throw new Error("Unsupported plan version. Expected version 1.");
  if (!Array.isArray(plan.scenes)) throw new Error("Plan scenes must be an array.");
  if (plan.scenes.length > 12) throw new Error("Choose 1–12 scenes; this plan has too many.");
  if (plan.scenes.some((scene) => !isObject(scene))) throw new Error("Every scene must be an object.");
  if (plan.defaults !== undefined && !isObject(plan.defaults)) throw new Error("Plan defaults must be an object.");
  return plan;
}

export function serializePlan(plan) {
  return JSON.stringify(parsePlan(plan), null, 2);
}

export function serializeDraftForNode(plan, { protectExisting = false, explicitSave = false } = {}) {
  return protectExisting && !explicitSave ? null : serializePlan(plan);
}

function inputPath(value) {
  if (typeof value !== "string" || !value.trim()) throw new Error("Image path is required.");
  const path = value.trim();
  if (path.startsWith("/") || path.includes("\\") || path.includes(":") || /[\u0000-\u001f\u007f]/.test(path)) {
    throw new Error("Image path must be relative to ComfyUI input, using forward slashes.");
  }
  if (path.split("/").some((part) => !part || part === "." || part === "..")) {
    throw new Error("Image path must not contain empty segments, '.' or '..'.");
  }
  if (path.startsWith("@")) throw new Error("Image path must name an input image.");
  return path;
}

function imageValue(...values) {
  return values.find((value) => value !== undefined && value !== null && value !== "");
}

function inspectOverrides(overrides, label, errors) {
  if (overrides === undefined || overrides === null) return;
  if (!isObject(overrides)) {
    errors.push(`${label}: input_overrides must map node IDs to scalar widget inputs.`);
    return;
  }
  for (const [nodeId, inputs] of Object.entries(overrides)) {
    if (!nodeId.trim() || !isObject(inputs)) {
      errors.push(`${label}: input_overrides must map node IDs to scalar widget inputs.`);
      continue;
    }
    for (const [name, value] of Object.entries(inputs)) {
      if (!name.trim() || !(value === null || typeof value === "string" || typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value)))) {
        errors.push(`${label}: override ${nodeId}.${name} must be a scalar string, number, boolean or null; graph links cannot be overridden.`);
      }
    }
  }
}

function inspectDirection(value, label, errors) {
  if (value === undefined || value === null) return {};
  if (!isObject(value) || Object.keys(value).some((key) => !Object.hasOwn(CINEMATIC_CHOICES, key))) {
    errors.push(`${label}: direction may contain only shot, camera, light and look.`);
    return {};
  }
  const result = {};
  for (const [key, selected] of Object.entries(value)) {
    if (selected === "" || selected === null) continue;
    if (typeof selected !== "string" || !CINEMATIC_CHOICES[key].includes(selected)) {
      errors.push(`${label}: ${key} must be one of the supported prompt choices.`);
    } else result[key] = selected;
  }
  return result;
}

function cinematicPrompt(scenePrompt, direction) {
  return [direction.shot, scenePrompt, direction.camera, direction.light, direction.look]
    .filter((part) => typeof part === "string" && part.trim())
    .join(". ");
}

/** Expand only enough to explain the draft and its duration, never to queue a render. */
export function inspectPlan(plan) {
  const result = { errors: [], clips: [], totalClips: 0, estimatedSeconds: 0 };
  const { errors, clips } = result;
  if (!isObject(plan) || plan.version !== 1) {
    errors.push("Plan must be a version 1 object.");
    return result;
  }
  if (!Array.isArray(plan.scenes) || plan.scenes.length < 1 || plan.scenes.length > 12) {
    errors.push("Plan must contain 1–12 scenes.");
    return result;
  }
  const defaults = isObject(plan.defaults) ? plan.defaults : {};
  if (plan.defaults !== undefined && !isObject(plan.defaults)) errors.push("Plan defaults must be an object.");
  for (const key of ["project", "global_prompt", "negative_prompt"]) {
    if (plan[key] !== undefined && typeof plan[key] !== "string") errors.push(`${key} must be text.`);
  }
  for (const [sceneIndex, scene] of plan.scenes.entries()) {
    const sceneLabel = `Scene ${sceneIndex + 1}`;
    if (!isObject(scene)) {
      errors.push(`${sceneLabel} must be an object.`);
      continue;
    }
    const sceneDirection = inspectDirection(scene.direction, sceneLabel, errors);
    const count = Array.isArray(scene.clips) ? scene.clips.length : (scene.clips ?? 1);
    if (!Number.isInteger(count) || count < 1 || count > 1000) {
      errors.push(`${sceneLabel}: clips must be an integer from 1 to 1000 or a nonempty array with at most 1000 entries.`);
      continue;
    }
    result.totalClips += count;
    if (result.totalClips > 1000) {
      errors.push("A plan can contain at most 1000 expanded clips.");
      break;
    }
    for (const key of ["id", "title", "prompt", "negative_prompt"]) {
      if (scene[key] !== undefined && !(key === "negative_prompt" && scene[key] === null) && typeof scene[key] !== "string") errors.push(`${sceneLabel}: ${key} must be text.`);
    }
    inspectOverrides(scene.input_overrides, sceneLabel, errors);
    for (let clipIndex = 0; clipIndex < count; clipIndex += 1) {
      const source = Array.isArray(scene.clips) ? scene.clips[clipIndex] : {};
      const label = `${sceneLabel}, clip ${clipIndex + 1}`;
      if (!isObject(source)) {
        errors.push(`${label} must be an object.`);
        continue;
      }
      const direction = { ...sceneDirection, ...inspectDirection(source.direction, label, errors) };
      const promptParts = [plan.global_prompt, cinematicPrompt(scene.prompt, direction), source.prompt];
      const prompt = promptParts.filter((part) => typeof part === "string").map((part) => part.trim()).filter(Boolean).join("\n\n");
      if (!prompt) errors.push(`${label}: enter a global, scene or clip prompt.`);
      if (source.prompt !== undefined && typeof source.prompt !== "string") errors.push(`${label}: prompt must be text.`);
      if (source.negative_prompt !== undefined && source.negative_prompt !== null && typeof source.negative_prompt !== "string") errors.push(`${label}: negative_prompt must be text.`);
      const firstImage = imageValue(source.first_image, clipIndex === 0 ? scene.first_image : undefined, clips.length === 0 ? defaults.first_image : "@previous");
      const lastImage = imageValue(source.last_image, scene.last_image, defaults.last_image);
      for (const [kind, value] of [["first", firstImage], ["last", lastImage]]) {
        if (value === "@previous" && kind === "first" && clips.length > 0) continue;
        if (value === "@previous") {
          errors.push(`${label}: ${kind} image cannot use @previous ${kind === "first" ? "before the first completed clip" : "as a last target"}.`);
          continue;
        }
        try {
          inputPath(value);
        } catch (error) {
          errors.push(`${label}: ${kind} image — ${error.message}`);
        }
      }
      const values = {};
      for (const key of ["frames", "fps", "seed"]) values[key] = source[key] ?? scene[key] ?? defaults[key] ?? FALLBACKS[key];
      if (!Number.isInteger(values.frames) || values.frames < 2 || values.frames > 4097) errors.push(`${label}: frames must be an integer from 2 to 4097.`);
      if (typeof values.fps !== "number" || !Number.isFinite(values.fps) || values.fps < 0.01 || values.fps > 240) errors.push(`${label}: fps must be from 0.01 to 240.`);
      if (!Number.isSafeInteger(values.seed) || values.seed < 0) errors.push(`${label}: seed must be an integer from 0 to ${Number.MAX_SAFE_INTEGER}.`);
      inspectOverrides(source.input_overrides, label, errors);
      if (Number.isFinite(values.frames) && Number.isFinite(values.fps) && values.frames > 0 && values.fps > 0) result.estimatedSeconds += values.frames / values.fps;
      clips.push({ sceneIndex, clipIndex, prompt, first_image: firstImage, last_image: lastImage, ...values });
    }
  }
  return result;
}

export function sceneAdvancedJSON(scene) {
  const advanced = Object.fromEntries(Object.entries(scene).filter(([key]) => !BASIC_SCENE_FIELDS.has(key)));
  if (Array.isArray(scene.clips)) advanced.clips = scene.clips;
  return JSON.stringify(advanced, null, 2);
}

export function editScene(scene, fields) {
  const advanced = jsonObject(fields.advancedJSON?.trim() || "{}", "Advanced scene JSON");
  for (const key of BASIC_SCENE_FIELDS) {
    if (key !== "clips" && Object.hasOwn(advanced, key)) throw new Error(`Edit ${key} in the scene fields, not in advanced JSON.`);
  }
  if (Object.hasOwn(advanced, "clips") && !Array.isArray(advanced.clips)) throw new Error("Advanced clips must be an array of clip objects.");
  const updated = {
    id: scene.id,
    title: fields.title ?? "",
    prompt: fields.prompt ?? "",
    clips: Number(fields.clipCount),
    ...advanced,
  };
  const direction = isObject(fields.direction)
    ? Object.fromEntries(Object.entries(fields.direction).filter(([, value]) => value))
    : {};
  if (Object.keys(direction).length) updated.direction = direction;
  for (const key of ["first_image", "last_image"]) {
    const value = fields[key]?.trim();
    if (value) updated[key] = value;
  }
  return updated;
}

export function buildSubmission({ plan, graph, directorId, checkpointId, videoOutputId }) {
  const report = inspectPlan(plan);
  if (report.errors.length) throw new Error(report.errors.slice(0, 12).join("\n") + (report.errors.length > 12 ? `\n…and ${report.errors.length - 12} more issues.` : ""));
  if (!isObject(graph?.output) || !Object.keys(graph.output).length) throw new Error("ComfyUI did not return an API output graph. Use app.graphToPrompt().output.");
  for (const [label, value] of [["director", directorId], ["checkpoint", checkpointId], ["video output", videoOutputId]]) {
    if (value === undefined || value === null || !String(value).trim()) throw new Error(`Select a ${label} node.`);
  }
  return clone({
    plan,
    workflow: graph.output,
    director_id: String(directorId).trim(),
    checkpoint_id: String(checkpointId).trim(),
    video_output_id: String(videoOutputId).trim(),
  });
}

export function workflowChoices(output) {
  const choices = { checkpoints: [], videos: [] };
  for (const [id, node] of Object.entries(output || {})) {
    const choice = { id, title: String(node._meta?.title || node.class_type || id), type: node.class_type };
    if (node.class_type === "AlphaSceneCheckpoint") choices.checkpoints.push(choice);
    if (["SaveVideo", "VHS_VideoCombine"].includes(node.class_type)) choices.videos.push(choice);
  }
  return choices;
}

export function uploadedImagePath(response) {
  if (!isObject(response) || response.type !== "input") throw new Error("The upload must return an input image.");
  if (typeof response.name !== "string" || response.name.includes("/") || response.name.includes("\\")) throw new Error("The upload returned an unsafe filename.");
  if (response.subfolder !== undefined && typeof response.subfolder !== "string") throw new Error("The upload returned an invalid subfolder path.");
  return inputPath(response.subfolder ? `${response.subfolder}/${response.name}` : response.name);
}

export function imagePreviewRoute(path) {
  if (!path || path === "@previous") return null;
  const safe = inputPath(path);
  const segments = safe.split("/");
  const filename = segments.pop();
  return `/view?${new URLSearchParams({ filename, subfolder: segments.join("/"), type: "input" })}`;
}

export function jobRoute(jobId, action) {
  const base = "/alpha_scene_director/jobs";
  if (jobId === undefined) return base;
  if (typeof jobId !== "string" || !/^[a-zA-Z0-9_-]{1,128}$/.test(jobId)) throw new Error("Enter a valid job ID.");
  if (action !== undefined && !["pause", "resume"].includes(action)) throw new Error("Unknown job action.");
  return `${base}/${encodeURIComponent(jobId)}${action ? `/${action}` : ""}`;
}

export function formatDuration(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "—";
  const rounded = Math.round(seconds);
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const remainder = String(rounded % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${remainder}` : `${minutes}:${remainder}`;
}

export function activeJobsRemain(savedJobs, latestJob) {
  const active = new Set(["running", "pausing", "queued", "pending", "starting", "resuming"]);
  const jobs = savedJobs.filter((job) => job.job_id !== latestJob?.job_id);
  if (latestJob) jobs.push(latestJob);
  return jobs.some((job) => active.has(job.status));
}
