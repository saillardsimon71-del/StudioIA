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
			detail = `HTTP ${res.status} : réponse du serveur illisible.`;
		}
		throw Object.assign(new Error(detail), { status: res.status });
	}
	return (await res.json()) as T;
}

function imageServiceUnavailable(error: any): boolean {
	return error instanceof TypeError || [401, 403, 429].includes(error.status) || error.status >= 500;
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
	replaceScenario: (projectId: string, text: string) =>
		fetch(`/api/projects/${projectId}/replace-scenario`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(JSON.parse(text.trim().replace(/^```(?:json)?\s*\n([\s\S]*?)\n```$/i, '$1')))
		}).then((r) => parseResponse<Project>(r)),

	generateScenario: (projectId: string, idea: string, target_duration_s: number) =>
		fetch(`/api/projects/${projectId}/generate-scenario`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ idea, target_duration_s })
		}).then((r) => parseResponse<{ project: Project; llm_meta: Record<string, any> }>(r)),

	listAssets: () => fetch('/api/assets').then((r) => parseResponse<LibraryAsset[]>(r)),
	generateDecor: (name: string, description: string) =>
		fetch('/api/assets/generate-decor', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ name, description })
		}).then((r) => parseResponse<LibraryAsset>(r)),
	generateShotImage: (projectId: string, shotId: string) =>
		fetch(`/api/projects/${projectId}/shots/${shotId}/generate-image`, { method: 'POST' })
			.then((r) => parseResponse<Project>(r)),
	getShotImagePrompt: (projectId: string, shotId: string) =>
		fetch(`/api/projects/${projectId}/shots/${shotId}/image-prompt`)
			.then((r) => parseResponse<{ prompt: string }>(r)),
	saveShotImagePrompt: (projectId: string, shotId: string, prompt: string) =>
		fetch(`/api/projects/${projectId}/shots/${shotId}/image-prompt`, {
			method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt })
		}).then((r) => parseResponse<Project>(r)),
	generateReferenceImage: (projectId: string, kind: 'character' | 'decor', id: string) =>
		fetch(`/api/projects/${projectId}/${kind === 'character' ? 'characters' : 'decors'}/${id}/generate-image`, { method: 'POST' })
			.then((r) => parseResponse<Project>(r)),
	assignAssetToDecor: (projectId: string, decorId: string, assetId: string) =>
		fetch(`/api/projects/${projectId}/decors/${decorId}/assign-asset`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ asset_id: assetId })
		}).then((r) => parseResponse<Project>(r)),
	generateProjectImages: async (
		project: Project,
		onUpdate: (updated: Project, key: string, error: string) => void,
		onStart: (label: string) => void,
		shouldStop: () => boolean = () => false,
		missingImages: string[] = []
	): Promise<Project> => {
		let current = project;
		const references = [
			...project.characters.filter((char) => !char.image_ref || missingImages.includes(`character:${char.id}`)).map((char) => ({ kind: 'character' as const, id: char.id, name: char.name })),
			...project.decors.filter((decor) => !decor.asset_id || missingImages.includes(`decor:${decor.id}`)).map((decor) => ({ kind: 'decor' as const, id: decor.id, name: decor.name }))
		];
		for (const item of references) {
			if (shouldStop()) return current;
			onStart(`${item.kind === 'character' ? 'Personnage' : 'Décor'} : ${item.name || item.id}`);
			let error = '';
			try {
				current = await api.generateReferenceImage(project.id, item.kind, item.id);
			} catch (e: any) {
				error = e.message;
				if (imageServiceUnavailable(e)) {
					onUpdate(current, `${item.kind}:${item.id}`, error);
					return current;
				}
			}
			onUpdate(current, `${item.kind}:${item.id}`, error);
		}
		onStart('Images des plans');
		return api.generateShotImages(current, (updated, id, error) => onUpdate(updated, id, error), undefined, shouldStop, missingImages);
	},
	generateShotImages: async (
		project: Project,
		onUpdate: (updated: Project, shotId: string, error: string) => void,
		shotIds?: string[],
		shouldStop: () => boolean = () => false,
		missingImages: string[] = []
	): Promise<Project> => {
		let current = project;
		for (const shot of project.shots) {
			if (shouldStop()) break;
			if ((shot.image_ref && !missingImages.includes(shot.id)) || (shotIds && !shotIds.includes(shot.id))) continue;
			let error = '';
			try {
				current = await api.generateShotImage(project.id, shot.id);
			} catch (e: any) {
				error = e.message;
				if (imageServiceUnavailable(e)) {
					onUpdate(current, shot.id, error);
					break;
				}
			}
			onUpdate(current, shot.id, error);
		}
		return current;
	},
	generateLibraryAsset: (prompt: string, kind: string, name: string, description: string) =>
		fetch('/api/assets/generate', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ prompt, kind, name, description })
		}).then((r) => parseResponse<LibraryAsset>(r)),
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
				missing_images: string[];
				total_estimated_cost_eur: number | null;
				max_budget_per_take_eur: number;
			}>(r)
		),
	renderShot: (projectId: string, shotId: string, authorized_by_human: boolean) =>
		fetch(`/api/projects/${projectId}/shots/${shotId}/render`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ authorized_by_human })
		}).then((r) => parseResponse<{ project: Project; take: Record<string, any>; qc: Record<string, any> }>(r)),
	renderMissingShots: async (project: Project, options: {
		authorizedByHuman: boolean;
		onUpdate: (updated: Project, shotId: string, error: string) => void;
		onStart?: (shotId: string) => void;
		shouldStop?: () => boolean;
	}): Promise<Project> => {
		if (!options.authorizedByHuman) throw new Error('Une autorisation du lot est nécessaire avant le rendu GPU.');
		const shotIds = project.shots.map((shot) => shot.id);
		let current = project;
		for (const id of shotIds) {
			if (options.shouldStop?.()) break;
			try {
				const prepared = await api.prepareRender(project.id, false);
				current = prepared.project;
				if (options.shouldStop?.()) break;
				const shot = current.shots.find((item) => item.id === id);
				if (!shot) throw new Error(`Plan ${id} introuvable dans le projet.`);
				const report = prepared.shots.find((item) => item.shot_id === id);
				if (report?.video_available ?? Boolean(shot.rendered_video_ref)) continue;
				if (!report?.ready_for_gpu) throw new Error(report?.blocking_reason || 'Prérequis du plan non satisfaits.');
				options.onStart?.(id);
				const result = await api.renderShot(project.id, id, true);
				current = result.project;
				options.onUpdate(current, id, '');
			} catch (e: any) {
				options.onUpdate(current, id, e.message);
				break;
			}
		}
		return current;
	},
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
