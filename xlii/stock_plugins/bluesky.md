---
id: bluesky
name: Bluesky (atproto)
description: Search posts, look up profiles, browse popular feeds — public reads, no authentication
categories: [social, news]
effect: read-only
trust: subscription
auth_type: none
auth_env_vars: []
actions:
  - id: search_posts
    description: Search Bluesky posts by query
    method: GET
    url: https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts
    params:
      q: {required: true, description: "Search query"}
      limit: {default: "25"}
    response_shape: ".posts[] → {author, text, indexedAt, likeCount, repostCount}"
  - id: get_profile
    description: Look up a Bluesky profile by handle
    method: GET
    url: https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile
    params:
      actor: {required: true, description: "Handle (e.g. name.bsky.social) or DID"}
    response_shape: "{handle, displayName, description, followersCount, followsCount, postsCount}"
  - id: author_feed
    description: Recent posts from a specific author
    method: GET
    url: https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed
    params:
      actor: {required: true, description: "Handle or DID"}
      limit: {default: "25"}
  - id: search_profiles
    description: Search Bluesky profiles by name or handle
    method: GET
    url: https://public.api.bsky.app/xrpc/app.bsky.actor.searchActors
    params:
      q: {required: true, description: "Search query"}
      limit: {default: "25"}
  - id: popular_feeds
    description: Browse popular custom feed generators
    method: GET
    url: https://public.api.bsky.app/xrpc/app.bsky.unspecced.getPopularFeedGenerators
    params: {}
---

# Bluesky (atproto)

Bluesky's `public.api.bsky.app` host serves anonymous read access for the headline endpoints. Posts, profiles, feeds — all without auth. Authenticated writes (posting, follows, likes) need an app password / OAuth and are deliberately out of scope here.

## Usage

### Search posts

```bash
curl -s 'https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?q={QUERY}&limit=25'
```

Returns `posts[]` with author DID/handle, indexed timestamp, text, embeds, like/repost counts.

### Profile by handle

```bash
curl -s 'https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor={HANDLE}'
```

`{HANDLE}` is `name.bsky.social` form (or any custom domain handle).

### Author's recent posts

```bash
curl -s 'https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor={HANDLE}&limit=25'
```

### Search profiles

```bash
curl -s 'https://public.api.bsky.app/xrpc/app.bsky.actor.searchActors?q={QUERY}&limit=25'
```

### Popular custom feeds

```bash
curl -s 'https://public.api.bsky.app/xrpc/app.bsky.unspecced.getPopularFeedGenerators'
```

Each entry has a `uri` you can pass back to `app.bsky.feed.getFeed?feed=<uri>` to read its posts.

### Trending topics — EXPERIMENTAL

```bash
curl -s 'https://public.api.bsky.app/xrpc/app.bsky.unspecced.getTrendingTopics'
```

⚠ The `app.bsky.unspecced.*` namespace is explicitly experimental and can change/disappear without notice. Works at the time this plugin shipped; verify before depending on it.

## Response shape

Each XRPC endpoint returns a single JSON object. Cursor-paginated endpoints include a `cursor` field — pass it back as `&cursor=...` for the next page.

## Notes

- For private/authenticated endpoints (your own timeline, follow lists requiring permission), you need a Bluesky app password — out of scope for this plugin.
- Handles can change; DIDs (`did:plc:...`) are the stable identifier. Use `getProfile` to map handle ↔ DID.
- Rate limits are unpublished but generous for public reads.
