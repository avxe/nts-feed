# Frontend Architecture

The frontend is intentionally simple: server-rendered HTML plus page-specific JavaScript modules and a small shared SPA shell.

## Principles

- Keep global UI infrastructure small and explicit.
- Load page assets only on the pages that need them.
- Let the router own page lifecycle.
- Keep selectors and data attributes stable when they are part of the JS contract.

## Shared vs Page-Owned Code

### Global

Global assets should handle only shared concerns:

- shell layout and navigation
- notifications
- router/lifecycle coordination
- persistent audio or media players
- shared drawers and overlays

### Page-owned

Each page module should own its own DOM binding and cleanup:

- home feed
- shows list
- show detail
- search
- discover
- likes/playlists
- admin
- stats

## SPA Lifecycle Contract

The supported contract is:

- the router decides which page module is active
- each page module exposes `init()` and optional `cleanup()`
- page modules do not rely on duplicate one-off event names and global rebinding tricks

This keeps navigation predictable and prevents duplicate listeners on persistent UI elements.

## Markup Contracts

When server-rendered markup is also manipulated by JavaScript:

- keep one explicit HTML contract for selectors and data attributes
- add regression tests for those hooks
- avoid duplicating the same card structure in multiple unrelated modules without tests protecting parity

## Discover Naming

Use “Discover” as the primary frontend term.

- `/discover` is the primary route
- `/mixtape` exists only as a compatibility alias
- internal asset and module naming should prefer `discover` over `mixtape`

## Contributor Checklist

When adding a new page or shared component:

1. Decide whether it is global or page-owned.
2. Add only the assets that page needs.
3. Register the module through the router lifecycle.
4. Document any new selectors or shared DOM contracts.
5. Add JS tests for initialization, cleanup, and key interactions.
