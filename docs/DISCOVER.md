# Discover: Next Up + Explore

This document explains the current Discover page. The old track-radio and vector-search design is gone.

## Product Shape

Discover now has two complementary modes:

- `Next Up`: an actionable listening inbox for what to resume, play, save, snooze, or dismiss right now
- `Explore`: the broader episode-first shelves built from subscriptions, likes, and genre overlap

It does not:

- build track playlists
- use embeddings or vector search
- depend on Qdrant
- maintain a session-based radio queue

It does:

- rank whole episodes from your subscribed shows and listening history
- use deterministic scoring from your library data
- preserve user state such as saved, snoozed, and dismissed episodes by stable `episode_url`
- expose shelves that are easy to reason about and test

## Next Up

`Next Up` answers the narrow question: what should you do next?

It currently renders four sections:

1. `Continue Listening`
2. `Play Next`
3. `Curiosity Bridges`
4. `Saved For Later`

Each card links back into the episode/show page and supports lightweight inbox actions such as:

- `Save`
- `Remove`
- `Snooze`
- `Dismiss`

`Continue Listening` cards use `Resume` as the primary affordance.

## Explore

`Explore` keeps the broader deterministic shelves:

1. `New From Your Shows`
2. `Because You Like`
3. `By Genre`
4. `Surprise Episode`

Each item is an episode card with:

- episode title
- show title
- episode date
- artwork
- matched genres
- a short reason label

## Ranking Inputs

Discover uses relational data already stored in SQLite.

Primary inputs:

- recent episode listening
- completed vs. in-progress sessions
- saved, snoozed, and dismissed inbox state
- liked artist overlap
- liked track overlap
- genre overlap
- show affinity
- recency boost
- repeat show penalty

Default scoring shape:

```text
episode_score =
  4.0 * liked_artist_overlap +
  3.0 * liked_track_overlap +
  2.5 * genre_overlap +
  2.0 * show_affinity +
  1.0 * recency_boost -
  1.5 * repeat_show_penalty
```

Only `Surprise Episode` introduces randomness, and even there the candidate pool is constrained so results stay relevant.

## Data Flow

```text
Subscriptions + likes + listening sessions + inbox state
                     |
                     v
SQLite queries gather candidate episodes
                     |
                     v
Python scoring builds Next Up and Explore views
                     |
                     v
Frontend renders episode cards and mutation actions
```

## Why This Is Better

- smaller runtime footprint
- no vector service to build, migrate, or repair
- deterministic output that can be tested reliably
- easier explanations in the UI
- better fit for an episode library product

## API Surface

Current endpoints:

- `GET /api/discover`
- `POST /api/discover/surprise`
- `GET /api/discover/genre/<genre_slug>`
- `GET /api/discover/next-up`
- `POST /api/discover/next-up/state`

The frontend should not depend on any track-radio or vector endpoint.

## Smoke Test

1. Play part of an episode and confirm it appears in `Continue Listening`.
2. Save one item and confirm it moves into `Saved For Later`.
3. Dismiss one recommendation and confirm it disappears.
4. Finish an episode and confirm it leaves `Continue Listening`.
