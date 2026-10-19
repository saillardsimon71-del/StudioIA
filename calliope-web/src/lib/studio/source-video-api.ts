import { request } from './api';

export interface SourceShot {
  index: number;
  start_sec: number;
  end_sec: number;
  frame_url: string | null;
  transcript: string;
  notes: string;
  speech_reviewed: boolean;
}

export interface VideoSource {
  id: string;
  filename: string;
  source_language: string;
  status: 'analyzing' | 'ready' | 'failed';
  error: string | null;
  url: string;
  hash: string;
  approved: boolean;
  analysis: {
    duration_sec: number;
    visual_status: 'unverified';
    transcription: { status: string; reason?: string; text: string };
    shots: SourceShot[];
  };
}

const path = (seriesId: number, id?: string) =>
  `/series/${seriesId}/video-sources${id ? '/' + encodeURIComponent(id) : ''}`;

export const sourceVideo = {
  upload(seriesId: number, file: File, language: string) {
    const body = new FormData();
    body.append('file', file);
    body.append('source_language', language);
    return request<VideoSource>(path(seriesId), 'POST', body);
  },
  get: (seriesId: number, id: string) => request<VideoSource>(path(seriesId, id)),
  review: (seriesId: number, id: string, hash: string, shots: SourceShot[]) =>
    request<VideoSource>(path(seriesId, id), 'PATCH', { expected_hash: hash, shots }),
  approve: (seriesId: number, id: string, hash: string) =>
    request<VideoSource>(path(seriesId, id) + '/approve', 'POST', { expected_hash: hash }),
  retry: (seriesId: number, id: string) =>
    request<VideoSource>(path(seriesId, id) + '/retry', 'POST'),
  cuts: (seriesId: number, id: string, hash: string, cuts: number[]) =>
    request<VideoSource>(path(seriesId, id) + '/cuts', 'POST', { expected_hash: hash, cuts }),
  project: (projectId: number) =>
    request<{ source: VideoSource | null }>(`/projects/${projectId}/source-video`),
};
