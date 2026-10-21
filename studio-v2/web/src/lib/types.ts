export interface Character {
	id: string;
	name: string;
	description: string;
	image_ref: string | null;
	voice_hint?: string;
}

export interface DialogueLine {
	speaker_id: string;
	text: string;
	start_sec: number | null;
	delivery?: 'on_screen' | 'voice_over' | 'off_screen';
}

export interface Decor {
	id: string;
	name: string;
	description: string;
	asset_id: string | null;
}

export interface DisplayChange {
	at_sec: number;
	text: string;
}

export interface DisplayOverlay {
	initial_text: string;
	x: number;
	y: number;
	font_size: number;
	color: string;
	box_bg: string | null;
	changes: DisplayChange[];
}

export interface Shot {
	id: string;
	duration_s: number;
	action: string;
	dialogue: DialogueLine[];
	initial_state: string;
	final_state: string;
	image_prompt: string;
	image_ref: string | null;
	continue_from_previous: boolean;
	camera_setup?: string;
	time_transition?: 'continuous' | 'ellipse' | 'flashback';
	decor_id: string | null;
	decor_asset_id: string | null;
	ltx_prompt: string;
	seed: number;
	image_validated: boolean;
	display_overlay: DisplayOverlay | null;
	rendered_video_ref: string | null;
	human_validated: boolean;
}

export interface Project {
	id: string;
	title: string;
	idea: string;
	aspect_ratio: '9:16' | '16:9' | '1:1';
	target_duration_s: number;
	narration_mode?: 'dialogue' | 'voice_over';
	visual_style?: string;
	characters: Character[];
	decors: Decor[];
	shots: Shot[];
}

export interface LibraryAsset {
	id: string;
	kind: 'character' | 'decor' | 'frame';
	name: string;
	description: string;
	file_path: string;
	sha256: string;
	width: number;
	height: number;
	created_at: string;
}

export interface CostBreakdown {
	estimated_cost_eur: number | null;
	warm_min_eur: number | null;
	cold_expected_eur: number | null;
	worst_case_eur: number | null;
	estimated_cost_usd: number | null;
	frames: number;
	engine_duration_s: number;
	warm_compute_s: number;
	expected_compute_s: number;
	worst_compute_s: number;
	gpu_label: string;
	eur_per_sec: number | null;
	usd_per_sec: number | null;
	calibration_source: string;
	uncertainties: string[];
}

export interface ShotPrepReport {
	shot_id: string;
	video_available?: boolean;
	render_pending?: boolean;
	ltx_prompt: string;
	word_count: number;
	max_words: number;
	ready_for_gpu: boolean;
	blocking_reason: string | null;
	warnings?: string[];
	cost: CostBreakdown;
}
