import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),
	kit: {
		adapter: adapter({
			pages: 'ui_bundle',
			assets: 'ui_bundle',
			fallback: 'index.html',
			precompress: false,
			strict: false
		})
	}
};

export default config;
