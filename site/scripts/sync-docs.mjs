// Copies the repository's Markdown into Starlight's content collection.
//
// The Markdown under docs/ and at the repository root stays the source of
// truth, because GitHub renders it and CLAUDE.md, the ADRs and the code all
// link into it by path. This script is the only thing that knows the site's
// shape: which file, or which section of the README, becomes which page, and
// how a link that works on GitHub becomes one that works here.
//
// Everything it writes is generated and ignored by git. Run by `npm run dev`
// and `npm run build` before Astro starts.

import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, posix, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const SITE = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const REPO = resolve(SITE, '..');
const CONTENT = join(SITE, 'src/content/docs');
const PUBLIC_IMAGES = join(SITE, 'public/images');

export const BASE = '/ios-agent';
const GITHUB = 'https://github.com/emazaheri/ios-agent';

// Directories under src/content/docs that this script owns. Anything else
// there (the landing page) is written by hand and left alone.
const GENERATED_DIRS = ['start', 'guides', 'concepts', 'reference', 'evals', 'realities', 'decisions', 'project'];

// README.md is one long page on GitHub. Here it is split by its `## `
// headings, and each section is sent to the page that answers its question.
// `intro` is everything above the first heading.
const README_SECTIONS = {
	intro: 'start/introduction',
	'Contents': null,
	'Why it is built this way': 'start/introduction',
	"Why only Apple's public APIs": 'concepts/platform',
	'Requirements': 'start/installation',
	'Setup': 'start/installation',
	'The terminal app': 'guides/terminal-app',
	'Connecting your own agent over MCP': 'guides/mcp',
	'What the model sees': 'guides/mcp',
	'Safety': null,
	'Measured on real hardware': 'evals/results',
	'Development': 'project/development',
	'Why the automation runs on a host, not on the phone': 'concepts/platform',
	'Contributing': null,
	'License': null,
};

const README_PAGES = {
	'start/introduction': {
		title: 'Introduction',
		description: 'Drive an iPhone or an iOS Simulator with an AI agent: a terminal app, an MCP server, and the library beneath both.',
		order: 1,
	},
	'start/installation': {
		title: 'Installation',
		description: 'What a Mac needs to drive a simulator or a phone, and the three commands that set it up.',
		order: 2,
	},
	'guides/terminal-app': {
		title: 'The terminal app',
		description: 'Run a goal, drive the device by hand, switch phones mid-session, and choose the model.',
		order: 1,
	},
	'guides/mcp': {
		title: 'Connecting over MCP',
		description: 'Point Claude Code, Cursor or any MCP client at the server, and see the screen exactly as the model does.',
		order: 2,
	},
	'concepts/platform': {
		title: 'Why only public APIs',
		description: 'Why everything goes through XCTest, why two faster private routes were measured and turned down, and why the engine runs on a Mac.',
		order: 2,
	},
	'evals/results': {
		title: 'Measured results',
		description: 'Success, observations, actions, turns and cost across the agent tasks, and the same goal verified on a physical iPhone.',
		order: 1,
	},
	'project/development': {
		title: 'Development',
		description: 'The test suites, the eval harness that gates quality, and the committed trend CI checks.',
		order: 1,
	},
};

// Whole files, each one page. `title` overrides the file's own heading where
// that heading names a package rather than saying what the page is.
const FILES = [
	{ src: 'docs/real-device-setup.md', slug: 'start/physical-device', title: 'Physical iPhone', order: 3 },
	{ src: 'agent/README.md', slug: 'guides/agent', title: 'The agent and its model', order: 3 },
	{ src: 'ARCHITECTURE.md', slug: 'concepts/architecture', order: 1 },
	{ src: 'SAFETY.md', slug: 'concepts/safety', order: 3 },
	{ src: 'docs/threat-model.md', slug: 'concepts/threat-model', order: 4 },
	{ src: 'docs/tool-reference.md', slug: 'reference/tools', title: 'MCP tools', order: 1 },
	{ src: 'tui/README.md', slug: 'reference/ios-tui', title: 'The ios-tui package', order: 2 },
	{ src: 'CONTRIBUTING.md', slug: 'project/contributing', order: 2 },
	{ src: 'SECURITY.md', slug: 'project/security', order: 3 },
	{ src: 'docs/realities/README.md', slug: 'realities', label: 'Overview', order: 0 },
	{ src: 'docs/adr/README.md', slug: 'decisions', label: 'Overview', order: 0 },
];

for (const name of readdirSync(join(REPO, 'docs/realities')).sort()) {
	if (name.endsWith('.md') && name !== 'README.md') {
		FILES.push({ src: `docs/realities/${name}`, slug: `realities/${name.slice(0, -3)}`, order: 1 });
	}
}

for (const name of readdirSync(join(REPO, 'docs/adr')).sort()) {
	const match = /^(\d{4})-(.+)\.md$/.exec(name);
	if (match) {
		FILES.push({ src: `docs/adr/${name}`, slug: `decisions/${match[1]}`, adr: Number(match[1]), order: Number(match[1]) });
	}
}

// Repository paths that resolve to a page, so a link to any of them can be
// rewritten. A link to README.md lands on the introduction; a link to a
// directory lands on its index page.
const PAGES = new Map(FILES.map((f) => [f.src, f.slug]));
PAGES.set('README.md', 'start/introduction');
PAGES.set('docs/adr', 'decisions');
PAGES.set('docs/realities', 'realities');
PAGES.set('docs/images', null);

const pageUrl = (slug) => `${BASE}/${slug}/`;

function rewriteTarget(target, fromFile) {
	if (/^[a-z][a-z0-9+.-]*:/i.test(target) || target.startsWith('#') || target.startsWith('/')) {
		return target;
	}
	const [path, hash = ''] = target.split(/(?=#)/);
	const repoPath = posix.normalize(posix.join(posix.dirname(fromFile), path)).replace(/\/$/, '');
	if (repoPath.startsWith('docs/images/')) {
		return `${BASE}/images/${repoPath.slice('docs/images/'.length)}`;
	}
	if (PAGES.has(repoPath) && PAGES.get(repoPath)) {
		return pageUrl(PAGES.get(repoPath)) + hash;
	}
	const kind = existsSync(join(REPO, repoPath)) && !repoPath.includes('.') ? 'tree' : 'blob';
	return `${GITHUB}/${kind}/main/${repoPath}${hash}`;
}

// Rewrites links outside fenced code. Inline code that names a page by path,
// as the architecture page does with ADR 0008, becomes a link to that page.
function rewriteBody(body, fromFile) {
	let fenced = false;
	return body
		.split('\n')
		.map((line) => {
			if (/^\s*(```|~~~)/.test(line)) {
				fenced = !fenced;
				return line;
			}
			if (fenced) return line;
			return line
				.replace(/(!?\[[^\]]*\])\(([^)\s]+)\)/g, (_, text, target) => `${text}(${rewriteTarget(target, fromFile)})`)
				.replace(/(?<!\[)`((?:docs\/[\w./-]+|[A-Z]+)\.md)`(?!\])/g, (whole, path) =>
					PAGES.get(path) ? `[\`${path}\`](${pageUrl(PAGES.get(path))})` : whole,
				);
		})
		.join('\n');
}

// The first prose paragraph, stripped to plain text, for the page's meta
// description and the search result snippet.
function describe(body) {
	for (const para of body.split(/\n\s*\n/)) {
		const text = para.trim();
		if (!text || /^(#|```|\||!\[|<|- |\* |\d+\. |>|\[!)/.test(text)) continue;
		if (/^(Accepted|Rejected|Superseded|Proposed|Not claimed)\b/.test(text) && text.length < 80) continue;
		const plain = text
			.replace(/!\[[^\]]*\]\([^)]*\)/g, '')
			.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
			.replace(/[`*_]/g, '')
			.replace(/\s+/g, ' ')
			.trim();
		if (plain.length < 30) continue;
		if (plain.length <= 200) return plain;
		const cut = plain.slice(0, 200);
		return `${cut.slice(0, cut.lastIndexOf(' '))}...`;
	}
	return undefined;
}

const yaml = (value) => JSON.stringify(value);

function frontmatter({ title, description, editSrc, label, order, badge }) {
	const lines = ['---', `title: ${yaml(title)}`];
	if (description) lines.push(`description: ${yaml(description)}`);
	lines.push(`editUrl: ${yaml(`${GITHUB}/edit/main/${editSrc}`)}`);
	lines.push('sidebar:');
	if (label) lines.push(`  label: ${yaml(label)}`);
	if (order !== undefined) lines.push(`  order: ${order}`);
	if (badge) lines.push('  badge:', `    text: ${yaml(badge)}`, '    variant: caution');
	lines.push('---', '');
	return lines.join('\n');
}

function splitTitle(source) {
	const match = /^# (.+)\n+/.exec(source);
	if (!match) throw new Error('expected a leading `# ` heading');
	return { heading: match[1].trim(), body: source.slice(match[0].length) };
}

function write(slug, content) {
	const out = join(CONTENT, `${slug.includes('/') || slug === 'index' ? slug : `${slug}/index`}.md`);
	mkdirSync(dirname(out), { recursive: true });
	writeFileSync(out, content);
	return out;
}

function adrStatus(body) {
	const line = body.split('\n').find((l) => l.trim());
	const match = /^(Accepted|Rejected|Superseded|Proposed|Not claimed)\b/.exec(line ?? '');
	return match ? match[1] : undefined;
}

function syncFile(file) {
	const { heading, body } = splitTitle(readFileSync(join(REPO, file.src), 'utf8'));
	let title = file.title ?? heading;
	let label = file.label;
	let badge;
	if (file.adr !== undefined) {
		const name = heading.replace(/^\d+\.\s*/, '');
		title = `ADR ${file.adr}: ${name}`;
		label = `${file.adr}. ${name}`;
		const status = adrStatus(body);
		if (status && status !== 'Accepted') badge = status;
	}
	return write(
		file.slug,
		frontmatter({
			title,
			description: describe(body),
			editSrc: file.src,
			label,
			order: file.order,
			badge,
		}) + rewriteBody(body, file.src),
	);
}

function syncReadme() {
	const source = readFileSync(join(REPO, 'README.md'), 'utf8')
		// The registry ownership marker at the very end is for PyPI, not readers.
		.replace(/<!--[\s\S]*?-->\s*mcp-name:.*\s*$/, '')
		.trimEnd();
	const { body } = splitTitle(source);
	const sections = [];
	let current = { name: 'intro', lines: [] };
	for (const line of body.split('\n')) {
		const match = /^## (.+)$/.exec(line);
		if (match) {
			sections.push(current);
			current = { name: match[1].trim(), lines: [] };
		} else {
			current.lines.push(line);
		}
	}
	sections.push(current);

	const pages = new Map();
	for (const section of sections) {
		if (!(section.name in README_SECTIONS)) {
			throw new Error(`README section "${section.name}" is not mapped to a page in sync-docs.mjs`);
		}
		const slug = README_SECTIONS[section.name];
		if (!slug) continue;
		let text = section.lines.join('\n').trim();
		if (section.name === 'intro') {
			// Badges point at README anchors and are noise on a docs page.
			text = text.replace(/^\[!\[[^\n]*\n/gm, '').trim();
		}
		pages.set(slug, [...(pages.get(slug) ?? []), { name: section.name, text }]);
	}

	for (const [slug, sections] of pages) {
		// A page made of one section already carries it as its title, so the
		// heading would only repeat it.
		const chunks = sections.map(({ name, text }) =>
			name === 'intro' || sections.length === 1 ? text : `## ${name}\n\n${text}`,
		);
		const meta = README_PAGES[slug];
		write(
			slug,
			frontmatter({ ...meta, editSrc: 'README.md' }) + rewriteBody(chunks.join('\n\n'), 'README.md') + '\n',
		);
	}
}

export function sync() {
	for (const dir of GENERATED_DIRS) rmSync(join(CONTENT, dir), { recursive: true, force: true });
	rmSync(PUBLIC_IMAGES, { recursive: true, force: true });

	syncReadme();
	const written = FILES.map(syncFile);
	cpSync(join(REPO, 'docs/images'), PUBLIC_IMAGES, { recursive: true });
	return written.length + Object.keys(README_PAGES).length;
}

if (import.meta.url === `file://${process.argv[1]}`) {
	const count = sync();
	console.log(`sync-docs: ${count} pages from ${relative(process.cwd(), REPO) || '.'}`);
}
