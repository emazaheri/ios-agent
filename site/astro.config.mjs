// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import mermaid from 'astro-mermaid';
import starlightLinksValidator from 'starlight-links-validator';

// Served as a GitHub Pages project site, so every URL lives under the
// repository's name. `BASE` in scripts/sync-docs.mjs must match `base` here.
export default defineConfig({
	site: 'https://emazaheri.github.io',
	base: '/ios-agent',
	trailingSlash: 'always',
	integrations: [
		// Before Starlight, so the diagram in ARCHITECTURE.md is claimed
		// before Expressive Code renders it as a code block.
		mermaid({ autoTheme: true }),
		starlight({
			title: 'ios-agent',
			description:
				'Drive an iPhone or an iOS Simulator with an AI agent. A terminal app, an MCP server, and the library beneath both.',
			logo: { src: './src/assets/logo.svg', replacesTitle: false },
			favicon: '/favicon.svg',
			head: [
				// Google Search Console ownership, for submitting the sitemap.
				{
					tag: 'meta',
					attrs: { name: 'google-site-verification', content: 'QcZyAv8-qn0GQfXy1VHzlGJBy2cx1DjYuxPUb7Byqp4' },
				},
			],
			customCss: ['./src/styles/theme.css'],
			social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/emazaheri/ios-agent' }],
			plugins: [starlightLinksValidator({ errorOnLocalLinks: false })],
			expressiveCode: { themes: ['github-dark', 'github-light'] },
			sidebar: [
				{ label: 'Start here', items: [{ autogenerate: { directory: 'start' } }] },
				{ label: 'Guides', items: [{ autogenerate: { directory: 'guides' } }] },
				{ label: 'Concepts', items: [{ autogenerate: { directory: 'concepts' } }] },
				{ label: 'Reference', items: [{ autogenerate: { directory: 'reference' } }] },
				{ label: 'Measurements', items: [{ autogenerate: { directory: 'evals' } }] },
				{ label: 'iOS realities', collapsed: true, items: [{ autogenerate: { directory: 'realities' } }] },
				{ label: 'Decision records', collapsed: true, items: [{ autogenerate: { directory: 'decisions' } }] },
				{ label: 'Project', items: [{ autogenerate: { directory: 'project' } }] },
			],
		}),
	],
});
