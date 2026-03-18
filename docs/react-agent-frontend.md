# React Agent Frontend

This repository originally shipped as a FastAPI + Jinja application and did not include the minimum frontend stack needed for a shadcn-style React component.

The React scaffold added at the repo root now provides:

- `Next.js`
- `TypeScript`
- `Tailwind CSS`
- `shadcn`-style structure with `components.json`
- standard component path at `components/ui`
- standard global styles path at `app/globals.css`

## Why `components/ui` matters

`components/ui` is the convention used by shadcn to keep reusable UI primitives in one predictable location. Keeping generated or copied UI there reduces alias drift, makes future `shadcn` CLI additions consistent, and avoids mixing page code with shared components.

## Suggested shadcn CLI bootstrap

If you want to re-initialize the frontend with the CLI instead of the manual scaffold in this repo:

```bash
npx shadcn@latest init
```

Recommended answers:

- framework: `Next.js`
- TypeScript: `yes`
- Tailwind CSS: `yes`
- components path: `@/components`
- utils path: `@/lib/utils`
- global CSS path: `app/globals.css`

## Run locally

```bash
npm.cmd install
npm.cmd run dev
```

Then open:

- `/agent` for the tailored learning-agent version
- `/demo` for the default component demo
