# Documentation site

The [Starlight](https://starlight.astro.build) site published at
<https://emazaheri.github.io/ios-agent/>.

The Markdown under `docs/` and at the repository root is the source of truth.
`scripts/sync-docs.mjs` copies it into `src/content/docs/`, splits the README
into pages, and rewrites links that work on GitHub into links that work here.
Everything it writes is ignored by git. The landing page, `index.mdx`, is the
only page written here by hand.

```bash
npm ci
npm run dev       # sync, then serve on localhost:4321/ios-agent/
npm run build     # sync, build, and fail on any broken internal link
```

A new file in `docs/adr/` or `docs/realities/` is picked up on its own. A new
top-level document, or a new `## ` section in the README, needs one line in
`scripts/sync-docs.mjs`; the script refuses an unmapped README section rather
than dropping it.

Pushes to `main` that touch documentation are deployed by
`.github/workflows/docs.yml`.
