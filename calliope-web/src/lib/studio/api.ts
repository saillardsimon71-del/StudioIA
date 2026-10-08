export type AspectRatio = "16:9" | "9:16" | "1:1";
export type MediaKind = "image" | "video";

export interface StudioProject {
  id: number;
  title: string;
  idea: string | null;
  aspect_ratio: AspectRatio;
  scene_count?: number;
  shot_count?: number;
  approved_count?: number;
  created_at: string;
  updated_at: string;
  cover_url?: string | null;
  script_source?: string | null;
}

export interface Take {
  id: number;
  clip_id: number;
  kind: MediaKind;
  status: string;
  url: string | null;
  poster_url?: string | null;
  prompt: string;
  seed: number | null;
  error: string | null;
  job_id: number | null;
  source_take_id: number | null;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface Shot {
  id: number;
  scene_id: number;
  order_index: number;
  description: string;
  prompt: string;
  image_prompt: string;
  dialogue: string;
  duration_sec: number;
  trim_start_sec: number | null;
  trim_end_sec: number | null;
  audio_mode: "native" | "silent";
  continue_from_previous: boolean;
  selected_image_id: number | null;
  selected_video_id: number | null;
  approved: boolean;
  takes: Take[];
}

export type ShotDraft = Pick<
  Shot,
  | "description"
  | "prompt"
  | "image_prompt"
  | "dialogue"
  | "duration_sec"
  | "audio_mode"
  | "continue_from_previous"
>;

export interface Scene {
  id: number;
  heading: string;
  action: string;
  dialog: string;
  order_index: number;
  shots: Shot[];
}

export interface StudioJob {
  id: number;
  status: string;
  job_type?: string;
  type?: string;
  kind?: string;
  clip_id?: number | null;
  error?: string | null;
  error_message?: string | null;
  progress?: number | null;
  created_at?: string;
}

export interface StudioExport {
  id: number;
  job_id: number | null;
  status: string;
  url: string | null;
  created_at: string;
  error?: string | null;
  stale: boolean;
  metadata: {
    duration_sec?: number;
    width?: number;
    height?: number;
    fps?: number;
    has_audio?: boolean;
  };
}

export interface Board {
  project: StudioProject;
  scenes: Scene[];
  jobs: StudioJob[];
  exports: StudioExport[];
  episode?: {
    series_id: number;
    number: number;
    series_title: string;
    summary: string;
    ongoing_plot: string;
  } | null;
  production?: {
    status: string;
    stage: string;
    progress: number;
    message: string;
    error: string | null;
    pause_requested: number;
  } | null;
  audio: AudioSettings;
}

export interface AudioCue {
  effect: string;
  shot_index: number;
  at_sec: number;
  gain_db: number;
  asset_id?: number | null;
}
export interface AudioHold {
  shot_index: number;
  at_sec: number;
  duration_sec: number;
  drop_db: number;
}
export interface AudioAsset {
  id: number;
  name: string;
  kind: string;
  url: string;
  duration_sec: number;
}
export interface AudioSettings {
  enabled: boolean;
  music: string;
  music_gain_db: number;
  voice_gain_db: number;
  tempo: number;
  ducking: boolean;
  ambience: string;
  ambience_gain_db: number;
  music_asset_id: number | null;
  sfx: AudioCue[];
  holds: AudioHold[];
  assets?: AudioAsset[];
}
export interface SeriesEntity {
  id: number;
  key: string;
  name: string;
  kind: string;
  description: string;
  url: string | null;
}
export interface Episode {
  project_id: number;
  number: number;
  title: string;
  idea: string;
  status: string;
  stage: string;
  progress: number;
  message: string;
  error: string | null;
  url: string | null;
  summary: string;
  events: string[];
  ongoing_plot: string;
  canon_revision: number | null;
  continuity_stale: boolean;
}
export interface Series {
  id: number;
  title: string;
  concept: string;
  bible: Record<string, string>;
  aspect_ratio: AspectRatio;
  language: string;
  target_seconds: number;
  entities: SeriesEntity[];
  episodes: Episode[];
}

export interface StudioConfig {
  modal_app: string;
  modal_function: string;
  llm_base_url: string;
  llm_model: string;
  llm_configured: boolean;
  image_base_url: string;
  image_model: string;
  image_configured: boolean;
  dry_run: boolean;
}

export interface Preflight {
  modal: { ok: boolean; message: string };
  ffmpeg: { ok: boolean; message: string };
  llm: { ok: boolean; message: string };
  image: { ok: boolean; message: string };
  engine: string;
}

async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const multipart = body instanceof FormData;
  let response: Response;
  try {
    response = await fetch(`/api/studio${path}`, {
      method,
      ...(body === undefined
        ? {}
        : { body: multipart ? body : JSON.stringify(body) }),
      ...(multipart || body === undefined
        ? {}
        : { headers: { "Content-Type": "application/json" } }),
    });
  } catch {
    throw new Error(
      "Le service Studio est injoignable. Vérifiez que le logiciel est lancé, puis réessayez.",
    );
  }
  if (!response.ok) {
    const raw = await response.text();
    let message = raw;
    try {
      const value = JSON.parse(raw);
      const detail = value.detail ?? value.message ?? value.error;
      message =
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail
                .map((item: { msg?: string }) => item.msg ?? "Champ invalide")
                .join(". ")
            : raw;
    } catch {
      /* Une erreur serveur peut être du texte. */
    }
    if (response.status === 404 && !message)
      message = "Cette ressource est introuvable.";
    throw new Error(
      message || `Le service a répondu avec une erreur (${response.status}).`,
    );
  }
  return response.json() as Promise<T>;
}

export const studio = {
  config: () => request<StudioConfig>("/config"),
  saveConfig: (
    payload: Partial<StudioConfig> & {
      llm_api_key?: string;
      image_api_key?: string;
      use_agnes_for_text?: boolean;
    },
  ) => request<StudioConfig>("/config", "PATCH", payload),
  preflight: () => request<Preflight>("/preflight"),
  series: () => request<{ series: Series[] }>("/series"),
  getSeries: (id: number) => request<Series>(`/series/${id}`),
  createSeries: (payload: {
    title: string;
    concept: string;
    aspect_ratio: AspectRatio;
    language: string;
    target_seconds: number;
  }) => request<Series>("/series", "POST", payload),
  saveSeries: (id: number, payload: Partial<Series>) =>
    request<Series>(`/series/${id}`, "PATCH", payload),
  createEpisode: (
    id: number,
    payload: { idea: string; objective: string; request_id: string },
  ) =>
    request<{ project_id: number; job_id: number }>(
      `/series/${id}/episodes`,
      "POST",
      payload,
    ),
  pauseProduction: (id: number) =>
    request<Board>(`/projects/${id}/production/pause`, "POST"),
  resumeProduction: (id: number) =>
    request<Board>(`/projects/${id}/production/resume`, "POST"),
  saveAudio: (id: number, payload: Partial<AudioSettings>) =>
    request<AudioSettings>(`/projects/${id}/audio`, "PATCH", payload),
  saveScript: (
    id: number,
    shots: {
      id: number;
      description: string;
      dialogue: string;
      prompt: string;
      image_prompt: string;
    }[],
  ) => request<Board>(`/projects/${id}/script`, "PATCH", { shots }),
  previewAudio: (id: number) =>
    request<{ music_url: string; effects_url: string }>(
      `/projects/${id}/audio/preview`,
      "POST",
    ),
  uploadAudio: (id: number, kind: string, file: File) => {
    const body = new FormData();
    body.append("kind", kind);
    body.append("file", file);
    return request<AudioAsset>(`/projects/${id}/audio/upload`, "POST", body);
  },
  uploadSeriesReference: (id: number, entityId: number, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<Series>(
      `/series/${id}/entities/${entityId}/reference`,
      "POST",
      body,
    );
  },
  projects: () => request<{ projects: StudioProject[] }>("/projects"),
  create: (payload: {
    title: string;
    idea: string;
    aspect_ratio: AspectRatio;
  }) => request<StudioProject>("/projects", "POST", payload),
  example: () => request<StudioProject>("/examples", "POST"),
  board: (id: number) => request<Board>(`/projects/${id}`),
  plan: (
    id: number,
    payload: { text: string; mode: "script" | "idea"; use_llm: boolean },
  ) => request<Board>(`/projects/${id}/plan`, "POST", payload),
  addScene: (
    id: number,
    payload: { heading: string; action?: string; dialog?: string },
  ) => request<Scene>(`/projects/${id}/scenes`, "POST", payload),
  updateScene: (
    id: number,
    sceneId: number,
    payload: Partial<Pick<Scene, "heading" | "action" | "dialog">>,
  ) => request<Scene>(`/projects/${id}/scenes/${sceneId}`, "PATCH", payload),
  addShot: (
    id: number,
    sceneId: number,
    payload: { description: string; prompt: string; duration_sec: number },
  ) =>
    request<Shot>(`/projects/${id}/scenes/${sceneId}/shots`, "POST", payload),
  saveShot: (id: number, shotId: number, payload: ShotDraft) =>
    request<Shot>(`/projects/${id}/shots/${shotId}`, "PATCH", payload),
  deleteShot: (id: number, shotId: number) =>
    request<{ ok: boolean }>(`/projects/${id}/shots/${shotId}`, "DELETE"),
  reorder: (
    id: number,
    payload: { scene_ids?: number[]; shot_ids?: number[]; scene_id?: number },
  ) => request<Board>(`/projects/${id}/reorder`, "POST", payload),
  generate: (
    id: number,
    payload: {
      kind: MediaKind;
      request_id: string;
      n_takes: number;
      seed?: number;
    },
  ) =>
    request<{ takes: Take[]; jobs: StudioJob[] }>(
      `/shots/${id}/generate`,
      "POST",
      payload,
    ),
  upload: (id: number, kind: MediaKind, file: File) => {
    const body = new FormData();
    body.append("kind", kind);
    body.append("file", file);
    return request<Take>(`/shots/${id}/upload`, "POST", body);
  },
  select: (id: number) => request<Shot>(`/takes/${id}/select`, "POST"),
  approve: (id: number, approved: boolean) =>
    request<Shot>(`/shots/${id}/approve`, "POST", { approved }),
  cancel: (id: number) => request<StudioJob>(`/jobs/${id}/cancel`, "POST"),
  resume: (id: number) => request<StudioJob>(`/jobs/${id}/resume`, "POST"),
  export: (id: number, requestId: string) =>
    request<{ job_id: number }>(`/projects/${id}/export`, "POST", {
      request_id: requestId,
    }),
};

export function isActive(status: string) {
  return [
    "pending",
    "queued",
    "running",
    "submitted",
    "polling",
    "processing",
    "rendering",
  ].includes(status);
}

export function statusLabel(status: string) {
  return (
    (
      {
        pending: "En attente",
        queued: "En attente",
        running: "En cours",
        submitted: "En cours",
        polling: "En cours",
        processing: "En cours",
        rendering: "En cours",
        completed: "Terminé",
        done: "Terminé",
        ready: "Disponible",
        succeeded: "Terminé",
        failed: "Échec",
        error: "Échec",
        cancelled: "Annulé",
        canceled: "Annulé",
        paused: "En pause",
      } as Record<string, string>
    )[status] ?? status
  );
}

export function selectedTake(shot: Shot, kind: MediaKind) {
  return shot.takes.find(
    (take) =>
      take.id ===
      (kind === "video" ? shot.selected_video_id : shot.selected_image_id),
  );
}

export function newRequestId() {
  return crypto.randomUUID();
}

export function errorMessage(error: unknown) {
  return error instanceof Error
    ? error.message
    : "Une erreur est survenue. Réessayez.";
}
