import { imagePreviewRoute, jobRoute, uploadedImagePath } from "./director_data.js";

export function uniqueUploadToken(cryptoSource = globalThis.crypto) {
  if (typeof cryptoSource?.randomUUID === "function") return cryptoSource.randomUUID();
  if (typeof cryptoSource?.getRandomValues !== "function") throw new Error("This browser cannot create a unique upload ID. Use a modern browser.");
  const bytes = cryptoSource.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

/** Small transport boundary: ComfyUI owns authentication; no credentials enter a plan. */
export function createDirectorClient(fetchApi) {
  async function responseError(response) {
    let message = `ComfyUI returned HTTP ${response.status}.`;
    try {
      const data = await response.json();
      if (typeof data.error === "string") message = data.error;
    } catch { /* The HTTP status remains useful for a non-JSON proxy error. */ }
    const error = new Error(message);
    error.httpStatus = response.status;
    return error;
  }

  async function json(route, options = {}) {
    const response = await fetchApi(route, options);
    if (!response.ok) throw await responseError(response);
    try {
      return await response.json();
    } catch {
      throw new Error("ComfyUI returned an unreadable JSON response. Refresh saved jobs before repeating a Start request.");
    }
  }

  function post(route, body) {
    return json(route, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
    });
  }

  return {
    validate: (request) => post("/alpha_scene_director/validate", request),
    start: (request) => post(jobRoute(), request),
    get: (jobId) => json(jobRoute(jobId)),
    pause: (jobId) => post(jobRoute(jobId, "pause")),
    resume: (jobId) => post(jobRoute(jobId, "resume")),
    async list() {
      const result = await json(jobRoute());
      const jobs = Array.isArray(result) ? result : result.jobs;
      if (!Array.isArray(jobs)) throw new Error("ComfyUI returned an invalid saved-job list.");
      return jobs;
    },
    async upload(file) {
      const form = new FormData();
      form.append("image", file, file.name);
      form.append("type", "input");
      form.append("subfolder", `alpha_scene_director/uploads/${uniqueUploadToken()}`);
      form.append("overwrite", "false");
      return uploadedImagePath(await json("/upload/image", { method: "POST", body: form }));
    },
    async preview(path, signal) {
      const route = imagePreviewRoute(path);
      if (!route) return null;
      const response = await fetchApi(route, { signal });
      if (!response.ok) throw await responseError(response);
      return response.blob();
    },
  };
}
