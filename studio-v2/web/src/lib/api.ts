import type { LibraryAsset, Project, ShotPrepReport } from './types';

async function parseResponse<T>(res: Response): Promise<T> {
	if (!res.ok) {
		let detail = `HTTP ${res.status}`;
		try {
			const payload = await res.json();
			if (typeof payload?.detail === 'string') {
				detail = payload.detail;
			} else if (payload?.detail?.message) {
				detail = payload.detail.message + (payload.detail.hint ? `\n${payload.detail.hint}` : '');
			} else {
				detail = JSON.stringify(payload);
			}
		} catch {
			detail = await res.text();
		}
		throw new Error(detail);
	}
	return (await res.json()) as T;
}

export function mediaUrl(filePath: string | null | undefined): string {
	if (!filePath) return '';
	return `/api/media?path=${encodeURIComponent(filePath)}`;
}

export const api = {
	health: () => fetch('/api/health').then((r) => parseResponse<Record<string, any>>(r)),
	modalStatus: () => fetch('/api/modal/status').then((r) => parseResponse<{ ok: boolean; message: string }>(r)),
	getSettings: () => fetch('/api/settings').then((r) => parseResponse<Record<string, any>>(r)),
	updateSettings: (patch: Record<string, any>) =>
		fetch('/api/settings', {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(patch)
		}).then((r) => parseResponse<Record<string, any>>(r)),

	listProjects: () => fetch('/api/projects').then((r) => parseResponse<Array<Record<string, any>>>(r)),
	createProject: (title: string, idea: string, target_duration_s: number) =>
		fetch('/api/projects', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ title, idea, target_duration_s })
		}).then((r) => parseResponse<Project>(r)),
	createPhase3Template: () =>
		fetch('/api/templates/phase3-fruit', { method: 'POST' }).then((r) => parseResponse<Project>(r)),
	getProject: (id: string) => fetch(`/api/projects/${id}`).then((r) => parseResponse<Project>(r)),
	saveProject: (project: Project) =>
		fetch(`/api/projects/${project.id}`, {
			method: 'PUT',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(project)
		}).then((r) => parseResponse<Project>(r)),

	generateScenario: (projectId: string, idea: string, target_duration_s: number) =>
		fetch(`/api/projects/${projectId}/generate-scenario`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ idea, target_duration_s })
		}).then((r) => parseResponse<{ project: Project; llm_meta: Record<string, any> }>(r)),

	listAssets: () => fetch('/api/assets').then((r) => parseResponse<LibraryAsset[]>(r)),
	uploadLibraryAsset: (file: File, kind: 'character' | 'decor' | 'frame', name: string, description: string) => {
		const fd = new FormData();
		fd.append('file', file);
		fd.append('kind', kind);
		fd.append('name', name);
		fd.append('description', description);
		return fetch('/api/assets/upload', { method: 'POST', body: fd }).then((r) => parseResponse<LibraryAsset>(r));
	},
	uploadCharacterImage: (projectId: string, charId: string, file: File) => {
		const fd = new FormData();
		fd.append('file', file);
		return fetch(`/api/projects/${projectId}/characters/${charId}/image`, {
			method: 'POST',
			body: fd
		}).then((r) => parseResponse<Project>(r));
	},
	uploadShotImage: (projectId: string, shotId: string, file: File) => {
		const fd = new FormData();
		fd.append('file', file);
		return fetch(`/api/projects/${projectId}/shots/${shotId}/image`, {
			method: 'POST',
			body: fd
		}).then((r) => parseResponse<Project>(r));
	},
	assignAssetToShot: (projectId: string, shotId: string, assetId: string) =>
		fetch(`/api/projects/${projectId}/shots/${shotId}/assign-asset`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ asset_id: assetId })
		}).then((r) => parseResponse<Project>(r)),
	assignAssetToCharacter: (projectId: string, charId: string, assetId: string) =>
		fetch(`/api/projects/${projectId}/characters/${charId}/assign-asset`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ asset_id: assetId })
		}).then((r) => parseResponse<Project>(r)),
	validateShotImage: (projectId: string, shotId: string, validated: boolean) =>
		fetch(`/api/projects/${projectId}/shots/${shotId}/validate-image`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ validated })
		}).then((r) => parseResponse<Project>(r)),

	prepareRender: (projectId: string, recompile = true) =>
		fetch(`/api/projects/${projectId}/prepare-render?recompile_prompts=${recompile}`, {
			method: 'POST'
		}).then((r) =>
			parseResponse<{
				project: Project;
				shots: ShotPrepReport[];
				total_estimated_cost_eur: number | null;
				max_budget_per_take_eur: number;
			}>(r)
		),
	renderShot: (projectId: string, shotId: string, authorized_by_human: boolean, max_budget_eur: number) =>
		fetch(`/api/projects/${projectId}/shots/${shotId}/render`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ authorized_by_human, max_budget_eur })
		}).then((r) => parseResponse<{ project: Project; take: Record<string, any>; qc: Record<string, any> }>(r)),
	uploadManualTake: (projectId: string, shotId: string, file: File) => {
		const fd = new FormData();
		fd.append('file', file);
		return fetch(`/api/projects/${projectId}/shots/${shotId}/upload-take`, {
			method: 'POST',
			body: fd
		}).then((r) => parseResponse<{ project: Project; take: Record<string, any>; qc: Record<string, any> }>(r));
	},
	listTakes: (projectId: string) =>
		fetch(`/api/projects/${projectId}/takes`).then((r) => parseResponse<Array<Record<string, any>>>(r)),
	reviewTake: (projectId: string, shotId: string, human_approved: boolean, human_notes: string) =>
		fetch(`/api/projects/${projectId}/shots/${shotId}/review`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ human_approved, human_notes })
		}).then((r) => parseResponse<{ project: Project; take: Record<string, any> }>(r)),

	exportProject: (projectId: string) =>
		fetch(`/api/projects/${projectId}/export`, { method: 'POST' }).then((r) =>
			parseResponse<Record<string, any>>(r)
		),
	listExports: (projectId: string) =>
		fetch(`/api/projects/${projectId}/exports`).then((r) => parseResponse<Array<Record<string, any>>>(r)),
	costLedger: () => fetch('/api/costs').then((r) => parseResponse<Record<string, any>>(r))
};
