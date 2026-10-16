import { request } from './api';

export interface ShotContract {
  summary: string;
  casting: string[];
  location: string;
  first_frame: string;
  positions: Record<string, string>;
  eyelines: Record<string, string>;
  framing: string;
  camera: string;
  action: string;
  initial_state: Record<string, string>;
  final_state: Record<string, string>;
  chronology: { at_sec: number; action: string }[];
  dialogue: { speaker: string; text: string; start_sec: number; end_sec: number }[];
  sound: string;
  duration_sec: number;
  references: string[];
  continuity: string[];
  qc: string[];
}

export interface DirectorShot {
  id: number;
  description: string;
  contract: ShotContract;
  hash: string;
  filename: string;
  purpose: string;
  continue_from_previous: boolean;
  approved: boolean;
  image: { take_id: number; url: string; approved: boolean } | null;
}

export interface DirectorBudget {
  max_new_takes: number;
  max_video_seconds: number;
  max_cost_eur: number;
  estimated_eur_per_video_second: number | null;
}

export interface DirectorState {
  enabled: boolean;
  approved: boolean;
  storyboard_hash: string;
  shots: DirectorShot[];
  budget: DirectorBudget;
}

export interface DirectorPreflight {
  ok: boolean;
  storyboard_hash: string;
  errors: string[];
  warnings: string[];
  cost: { estimated_eur: number | null; reserved_eur: number; new_takes: number; video_seconds: number; notice: string };
}

const path = (id: number) => `/projects/${id}/director`;
export const director = {
  state: (id: number) => request<DirectorState>(path(id)),
  prepare: (id: number) => request<DirectorState>(`${path(id)}/prepare`, 'POST'),
  approve: (id: number, expected_hash: string) => request<DirectorState>(`${path(id)}/approve`, 'POST', { expected_hash }),
  revise: (id: number, expected_hash: string, shot_ids: number[], instruction: string) =>
    request<DirectorState>(`${path(id)}/revise`, 'POST', { expected_hash, shot_ids, instruction }),
  edit: (id: number, expected_hash: string, shots: { id: number; contract: ShotContract }[]) =>
    request<DirectorState>(`${path(id)}/contracts`, 'PATCH', { expected_hash, shots }),
  images: (id: number, expected_hash: string, files: File[]) => {
    const form = new FormData();
    form.append('expected_hash', expected_hash);
    files.forEach((file) => form.append('files', file));
    return request<DirectorState>(`${path(id)}/images`, 'POST', form);
  },
  reviewImage: (id: number, take: number, expected_hash: string, approved: boolean) =>
    request<DirectorState>(`${path(id)}/images/${take}/review`, 'POST', { expected_hash, approved }),
  preflight: (id: number) => request<DirectorPreflight>(`${path(id)}/preflight`),
  budget: (id: number, budget: DirectorBudget) => request<DirectorState>(`${path(id)}/budget`, 'PATCH', budget),
  verify: (id: number, take: number) => request<{ audio_label: string; dialogue: { status: string; reason: string } }>(`${path(id)}/takes/${take}/verify`, 'POST'),
  review: (id: number, take: number, expected_hash: string, approved: boolean, checks: Record<string, boolean>) =>
    request(`${path(id)}/takes/${take}/review`, 'POST', { expected_hash, approved, ...checks }),
};
