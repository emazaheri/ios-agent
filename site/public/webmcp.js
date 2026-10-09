// Exposes the documentation to an agent in the browser through WebMCP
// (https://webmachinelearning.github.io/webmcp/). A browser without the API
// skips all of it. The tools only read: the site is static and has nothing
// else to offer.

const modelContext = document.modelContext ?? navigator.modelContext;

if (modelContext?.registerTool) {
	// Served from the site's base, so this file's own URL is the base.
	const base = new URL('.', import.meta.url);
	const controller = new AbortController();
	addEventListener('pagehide', () => controller.abort(), { once: true });

	const text = (value) => ({ content: [{ type: 'text', text: JSON.stringify(value, null, 2) }] });

	// Only pages of this site, so a tool cannot be pointed at another origin.
	const pageUrl = (path) => {
		const url = new URL(String(path).replace(/^\//, ''), base);
		if (url.origin !== base.origin || !url.pathname.startsWith(base.pathname)) {
			throw new Error(`Not a page of this site: ${path}`);
		}
		return url;
	};

	const register = (tool) => modelContext.registerTool(tool, { signal: controller.signal });

	register({
		name: 'search_docs',
		description:
			'Search the ios-agent documentation: setup, the MCP tools, the terminal app, safety, and the measured results. Returns matching pages with an excerpt.',
		inputSchema: {
			type: 'object',
			properties: {
				query: { type: 'string', description: 'What to look for, in plain words.' },
				limit: { type: 'integer', minimum: 1, maximum: 20, description: 'How many pages to return. Default 5.' },
			},
			required: ['query'],
		},
		annotations: { readOnlyHint: true },
		async execute({ query, limit = 5 }) {
			const pagefind = await import(new URL('pagefind/pagefind.js', base).href);
			const search = await pagefind.search(query);
			const hits = await Promise.all(search.results.slice(0, limit).map((r) => r.data()));
			return text(
				hits.map((hit) => ({
					title: hit.meta.title,
					url: new URL(hit.url, base.origin).href,
					excerpt: hit.excerpt.replace(/<[^>]+>/g, ''),
				})),
			);
		},
	});

	register({
		name: 'list_pages',
		description: 'List every page of the ios-agent documentation by URL.',
		inputSchema: { type: 'object', properties: {} },
		annotations: { readOnlyHint: true },
		async execute() {
			const xml = await (await fetch(new URL('sitemap-0.xml', base))).text();
			const doc = new DOMParser().parseFromString(xml, 'application/xml');
			return text([...doc.querySelectorAll('loc')].map((loc) => loc.textContent));
		},
	});

	register({
		name: 'read_page',
		description:
			'Read one page of the ios-agent documentation as plain text, without navigation. Takes a URL or a path such as "start/quickstart/".',
		inputSchema: {
			type: 'object',
			properties: { path: { type: 'string', description: 'The page URL, or its path below the site root.' } },
			required: ['path'],
		},
		annotations: { readOnlyHint: true },
		async execute({ path }) {
			const url = pageUrl(path);
			const response = await fetch(url);
			if (!response.ok) throw new Error(`${url.href} returned ${response.status}`);
			const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
			const main = doc.querySelector('main') ?? doc.body;
			return text({
				title: doc.title,
				url: url.href,
				text: main.textContent.replace(/\n\s*\n+/g, '\n\n').trim(),
			});
		},
	});
}
