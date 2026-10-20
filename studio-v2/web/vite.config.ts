import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [sveltekit()],
	server: {
		host: '0.0.0.0',
		port: 5179,
		allowedHosts: true,
		proxy: {
			'/api': 'http://127.0.0.1:8270'
		}
	}
});
