# Crucible dashboard

A web UI over the Crucible HTTP API (`crucible serve`). Vite + React +
TypeScript, no external calls — everything is bundled. Mostly read-only; the
**+ New run** button (side nav / Runs page) is the one write action — it opens a
modal for a repo + optional ref and `POST /runs`s it (needs a write token).

## Develop

```bash
npm install
npm run dev          # Vite on http://localhost:5173, proxies the API to :8787
# in another shell:
crucible serve       # the API on :8787
```

## Build

```bash
npm run build        # type-check + bundle -> dist/
npm run test         # vitest unit tests
npm run typecheck
```

`crucible serve` serves `ui/dist` at `/` when it exists. Point it elsewhere with
`CRUCIBLE_API_UI_DIR=/path/to/dist`, or run API-only with `crucible serve --no-ui`.

## Auth

The **Settings** page (gear, bottom of the side nav) has an **API base URL** and
a **token** field (stored in `localStorage`). When the API requires a bearer
token, enter it there; it is sent on every request. Write actions (resolve a
wish) need a full token, not the read-only one.

## Layout

```
src/
  api/       client (fetch wrapper + bearer token), types (mirror schemas.py), react-query hooks
  components/ Shell (persistent side nav + New-run modal host), states (Loading/Empty/ErrorState/Q), badges, DiffView, Markdown, JsonView, CoverageMatrix
  lib/       formatting, url-state helper, theme/collapse persistence, the New-run-modal context (so any page can open it, prefilled)
  pages/     Runs, RunLayout+Overview, Findings+FindingDetail, Report, Recon, Coverage, RunState, Wishlist, Artifacts, Settings
```

The side nav (brand · **+ New run** · Runs · Wishlist · Settings) is global and
persistent; it collapses to icon-only on narrow viewports or via its own
toggle (state kept in `localStorage`, like the theme). `Settings` is
deliberately minimal for now — issue #72 will expand it into a full settings
page (a Models section needs the endpoint from issue #73).

Types in `src/api/types.ts` mirror `crucible/api/schemas.py`; the API side
snapshot-tests its OpenAPI schema (`tests/data/openapi.json`), so drift is
caught there — keep the two in sync on a schema change.
