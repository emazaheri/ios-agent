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
const GENERATED_DIRS = ['start', 'guides', 'concepts', 'reference', 'built', 'evals', 'realities', 'decisions', 'project'];

// README.md is one long page on GitHub. Here it is split by its `## `
// headings, and each section is sent to the page that answers its question.
// `intro` is everything above the first heading.
const README_SECTIONS = {
	intro: 'start/introduction',
	'Contents': null,
	'What you can do with it': 'start/introduction',
	'Features': 'start/features',
	'Who it is for': 'start/introduction',
	'Why it is built this way': 'concepts/design',
	"Why only Apple's public APIs": 'concepts/design',
	'Requirements': 'start/installation',
	'Setup': 'start/installation',
	'The terminal app': 'guides/terminal-app',
	'Connecting your own agent over MCP': 'guides/mcp',
	'What the model sees': 'concepts/what-the-model-sees',
	'Safety': null,
	'Measured on real hardware': 'built/results',
	'Development': 'project/development',
	'Why the automation runs on a host, not on the phone': 'concepts/design',
	'Contributing': null,
	'License': null,
};

const README_PAGES = {
	'start/introduction': {
		title: 'Introduction',
		description: 'Give an AI agent an iPhone or an iOS Simulator, and it checks every step it takes. What you can do with it, and who it is for.',
		order: 1,
	},
	'start/features': {
		title: 'Features',
		description: 'Knows when an action did not work, runs on a real iPhone over a cable or Wi-Fi, stays cheap on tokens, and asks before anything risky.',
		order: 2,
	},
	'start/installation': {
		title: 'Installation',
		description: 'What a Mac needs to drive a simulator or a phone, and the three commands that set it up.',
		order: 5,
	},
	'concepts/design': {
		title: 'Design decisions',
		description: 'Why the design follows from a 200-row list costing 37,000 tokens, why only Apple\'s public APIs, and why the engine runs on a Mac.',
		order: 3,
	},
	'guides/terminal-app': {
		title: 'Run a goal in the terminal',
		description: 'Give the agent a goal, drive the device by hand, switch phones mid-session, and read the run as it happens.',
		order: 1,
	},
	'guides/mcp': {
		title: 'Connecting over MCP',
		description: 'Point Claude Code, Cursor or any MCP client at the server, over stdio or HTTP.',
		order: 2,
	},
	'concepts/what-the-model-sees': {
		title: 'What the model sees',
		description: 'A screen as the model reads it: a few hundred tokens of refs, roles and labels instead of accessibility XML.',
		order: 2,
	},
	'built/results': {
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
	{ src: 'docs/comparison.md', slug: 'start/comparison', order: 3 },
	{ src: 'docs/quickstart.md', slug: 'start/quickstart', order: 4 },
	{ src: 'docs/check-your-app.md', slug: 'guides/check-your-app', order: 3 },
	{ src: 'docs/approvals-and-secrets.md', slug: 'guides/approvals-and-secrets', order: 5 },
	{ src: 'docs/library.md', slug: 'guides/library', title: 'Build on the library', order: 6 },
	{ src: 'docs/troubleshooting.md', slug: 'guides/troubleshooting', order: 8 },
	{ src: 'docs/real-device-setup.md', slug: 'guides/physical-device', title: 'Use a physical iPhone', order: 4 },
	{ src: 'agent/README.md', slug: 'guides/agent', title: 'Choose a model', order: 7 },
	{ src: 'ARCHITECTURE.md', slug: 'concepts/architecture', order: 1 },
	{ src: 'docs/how-it-was-built.md', slug: 'built/agent', title: 'The agent: kept and rejected', order: 2 },
	{ src: 'SAFETY.md', slug: 'concepts/safety', order: 4 },
	{ src: 'docs/threat-model.md', slug: 'concepts/threat-model', order: 5 },
	{ src: 'docs/tool-reference.md', slug: 'reference/tools', title: 'MCP tools and resources', order: 1 },
	{ src: 'docs/cli.md', slug: 'reference/cli', order: 2 },
	{ src: 'CONTRIBUTING.md', slug: 'project/contributing', order: 2 },
	{ src: 'SECURITY.md', slug: 'project/security', order: 3 },
	{ src: 'tui/README.md', slug: 'project/ios-tui', title: 'Inside ios-tui', order: 4 },
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
PAGES.set('.env.example', 'reference/configuration');

const pageUrl = (slug) => `${BASE}/${slug}/`;

// README section anchors, mapped to wherever each section lands on the site.
// Filled by syncReadme, which runs before any other file is rewritten, so a
// link to `README.md#measured-on-real-hardware` from another page lands on
// the measurements rather than on the introduction.
const README_ANCHORS = new Map();

function rewriteTarget(target, fromFile) {
	if (/^[a-z][a-z0-9+.-]*:/i.test(target) || target.startsWith('#') || target.startsWith('/')) {
		return target;
	}
	const [path, hash = ''] = target.split(/(?=#)/);
	const repoPath = posix.normalize(posix.join(posix.dirname(fromFile), path)).replace(/\/$/, '');
	if (repoPath.startsWith('docs/images/')) {
		return `${BASE}/images/${repoPath.slice('docs/images/'.length)}`;
	}
	if (repoPath === 'README.md' && README_ANCHORS.has(hash.slice(1))) {
		return README_ANCHORS.get(hash.slice(1));
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

	// A README anchor names a section that, here, may live on another page.
	// GitHub's anchor for a heading is its lowercased text with punctuation
	// dropped and spaces as hyphens; a section that is a page's only one has
	// no heading of its own, so its anchor becomes the page itself.
	const anchor = (name) => name.toLowerCase().replace(/[^\w\s-]/g, '').replace(/\s/g, '-');
	for (const [slug, list] of pages) {
		for (const { name } of list) {
			if (name !== 'intro') README_ANCHORS.set(anchor(name), list.length === 1 ? pageUrl(slug) : `${pageUrl(slug)}#${anchor(name)}`);
		}
	}
	const rewriteAnchors = (text) =>
		text.replace(/\]\(#([\w-]+)\)/g, (whole, id) => (README_ANCHORS.has(id) ? `](${README_ANCHORS.get(id)})` : whole));

	for (const [slug, sections] of pages) {
		// A page made of one section already carries it as its title, so the
		// heading would only repeat it.
		const chunks = sections.map(({ name, text }) =>
			name === 'intro' || sections.length === 1 ? text : `## ${name}\n\n${text}`,
		);
		const meta = README_PAGES[slug];
		write(
			slug,
			frontmatter({ ...meta, editSrc: 'README.md' }) + rewriteBody(rewriteAnchors(chunks.join('\n\n')), 'README.md') + '\n',
		);
	}
}

// The configuration reference is `.env.example` itself, read rather than
// restated: `tests/unit/test_dotenv.py` already fails when that file and the
// settings models disagree, so a page built from it inherits the guard. Each
// `# ---` banner opens a section, the comment above a setting describes it, and
// a commented-out setting is one that is off, or shown only as an example.
function syncEnvExample() {
	const lines = readFileSync(join(REPO, '.env.example'), 'utf8').split('\n');
	const intro = [];
	const sections = [];
	let section = null;
	let comment = [];
	let inBanner = false;
	const setting = /^(#\s*)?([A-Z][A-Z0-9_]*)=(.*)$/;
	const prose = (text) =>
		text
			.map((l) => (/^ {2,}\S/.test(l) ? `\`${l.trim()}\`` : l.trim()))
			.join(' ')
			.replace(/\s+/g, ' ')
			.replace(/\|/g, '\\|')
			.trim();

	for (const raw of lines) {
		const line = raw.trimEnd();
		if (/^# -{10,}$/.test(line)) {
			if (!inBanner) {
				section = { title: '', text: [], rows: [] };
				sections.push(section);
			}
			inBanner = !inBanner;
			comment = [];
			continue;
		}
		const match = setting.exec(line);
		if (match && match[2] !== 'IOS_MCP_SECRET_' && !/^#\s{2,}/.test(line)) {
			const [, off, name, value] = match;
			section?.rows.push({ name, value, off: Boolean(off), notes: prose(comment) });
			comment = [];
			continue;
		}
		if (line.startsWith('#')) {
			const text = line.replace(/^# ?/, '');
			if (inBanner) {
				if (!section.title) section.title = text.replace(/\.$/, '').split('. ')[0];
				section.text.push(text);
			} else if (!section) {
				intro.push(text);
			} else {
				comment.push(text);
			}
			continue;
		}
		if (!line && section && comment.length) {
			// A comment with no setting under it belongs to the section.
			section.rows.push({ note: prose(comment) });
			comment = [];
		}
	}

	const body = [
		'Every setting the server and the agent read, generated from `.env.example`, which a test holds to the settings models.',
		'',
		prose(intro.filter((l) => !l.startsWith('Copy to'))),
		'',
		'Copy `.env.example` to `.env` to change any of them. A value marked *not set* is commented out there: either the default shown for reference, or an example to fill in.',
	];
	for (const { title, text, rows } of sections) {
		const rest = text.join('\n').slice(text[0].split('. ')[0].length).replace(/^\.?\s*/, '');
		body.push('', `## ${title}`, '');
		if (rest.trim()) body.push(prose(rest.split('\n')), '');
		const table = rows.filter((r) => r.name);
		if (table.length) {
			body.push('| Setting | Value | Notes |', '|---|---|---|');
			for (const { name, value, off, notes } of table) {
				const shown = value ? `\`${value.replace(/\|/g, '\\|')}\`` : '';
				body.push(`| \`${name}\` | ${off ? `${shown} *not set*`.trim() : shown} | ${notes} |`);
			}
		}
		for (const { note } of rows.filter((r) => r.note)) body.push('', note);
	}
	return write(
		'reference/configuration',
		frontmatter({
			title: 'Configuration',
			description: 'Every IOS_MCP_* and IOS_AGENT_* setting, its value, and what it changes, generated from .env.example.',
			editSrc: '.env.example',
			order: 3,
		}) +
			body.join('\n') +
			'\n',
	);
}

export function sync() {
	for (const dir of GENERATED_DIRS) rmSync(join(CONTENT, dir), { recursive: true, force: true });
	rmSync(PUBLIC_IMAGES, { recursive: true, force: true });

	syncReadme();
	syncEnvExample();
	const written = FILES.map(syncFile);
	cpSync(join(REPO, 'docs/images'), PUBLIC_IMAGES, { recursive: true });
	return written.length + Object.keys(README_PAGES).length + 1;
}

if (import.meta.url === `file://${process.argv[1]}`) {
	const count = sync();
	console.log(`sync-docs: ${count} pages from ${relative(process.cwd(), REPO) || '.'}`);
}
