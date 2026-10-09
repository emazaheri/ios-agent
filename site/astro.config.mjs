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
	// Pages that moved when the sidebar was regrouped by what the reader is
	// doing. The old URLs were already indexed, so they forward rather than 404.
	redirects: {
		'/evals/results/': '/ios-agent/built/results/',
		'/concepts/platform/': '/ios-agent/concepts/design/',
		'/start/physical-device/': '/ios-agent/guides/physical-device/',
		'/reference/ios-tui/': '/ios-agent/project/ios-tui/',
	},
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
				// Agent discovery. The catalog, the MCP server card and robots.txt
				// live in the emazaheri.github.io repository, because agents look
				// for them at the domain root, which this project site is not.
				{
					tag: 'link',
					attrs: { rel: 'ai-catalog', href: 'https://emazaheri.github.io/.well-known/ai-catalog.json' },
				},
				// WebMCP tools: search, list and read these pages from the browser.
				{ tag: 'script', attrs: { type: 'module', src: '/ios-agent/webmcp.js' } },
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
				{
					label: 'How it was built',
					items: [
						{ autogenerate: { directory: 'built' } },
						{ label: 'iOS realities', collapsed: true, items: [{ autogenerate: { directory: 'realities' } }] },
						{ label: 'Decision records', collapsed: true, items: [{ autogenerate: { directory: 'decisions' } }] },
					],
				},
				{ label: 'Project', items: [{ autogenerate: { directory: 'project' } }] },
			],
		}),
	],
});
