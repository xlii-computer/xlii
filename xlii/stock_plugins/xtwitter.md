---
id: xtwitter
name: X (Twitter v2 API)
description: Tweet search, trends, user/timeline lookups via the X v2 API. Requires a paid tier.
categories: [social, news]
effect: read-only
trust: subscription
auth_type: bearer
auth_env_vars:
  - X_BEARER_TOKEN
actions:
  - id: set
    description: Store the X bearer token in the vault
    params:
      X_BEARER_TOKEN: {secret: true, store: true, required: true, description: "X API v2 bearer token"}
    output: raw
  - id: recent_search
    description: Search tweets from the last 7 days
    method: GET
    url: https://api.twitter.com/2/tweets/search/recent
    params:
      query: {required: true, description: "Search query (supports from:, to:, lang:, -is:retweet, has:images)"}
      max_results: {default: "25"}
      tweet.fields: {default: "created_at,public_metrics,author_id"}
    headers:
      Authorization: "Bearer ${X_BEARER_TOKEN}"
    response_shape: ".data[] → {id, text, created_at, public_metrics, author_id}"
    output_schema:
      type: object
      additionalProperties: false
      required: [tweets]
      properties:
        tweets:
          type: array
          description: "Up to 25 entries from response.data[]"
          items:
            type: object
            additionalProperties: false
            required: [id, text, created_at, likes, retweets, replies, author_id]
            properties:
              id: {type: string, description: "From data[i].id"}
              text: {type: string, description: "From data[i].text — preserve newlines, do not summarize"}
              created_at: {type: string, description: "From data[i].created_at"}
              likes: {type: integer, description: "From data[i].public_metrics.like_count, or 0"}
              retweets: {type: integer, description: "From data[i].public_metrics.retweet_count, or 0"}
              replies: {type: integer, description: "From data[i].public_metrics.reply_count, or 0"}
              author_id: {type: string, description: "From data[i].author_id"}
    output: schema
    output_transforms:
      - {op: rename, list: data, from: public_metrics.like_count, to: likes}
      - {op: rename, list: data, from: public_metrics.retweet_count, to: retweets}
      - {op: rename, list: data, from: public_metrics.reply_count, to: replies}
    output_renderer: message_list
    output_renderer_args:
      header: "X — recent search"
      list_key: data
      item_template: "  · {text}\n      ♡ {likes:,}  ⟲ {retweets:,}  ↩ {replies:,}  · {created_at}\n      https://twitter.com/i/web/status/{id}"
      empty_message: "(no tweets)"
  - id: user_by_handle
    description: Look up an X user by handle
    method: GET
    url: https://api.twitter.com/2/users/by/username/{username}
    params:
      username: {required: true, description: "X handle (without @)"}
      user.fields: {default: "created_at,public_metrics,verified,description"}
    headers:
      Authorization: "Bearer ${X_BEARER_TOKEN}"
    response_shape: ".data → {id, name, username, description, public_metrics}"
    output_schema:
      type: object
      additionalProperties: false
      required: [id, name, username, description, followers, following, tweet_count, verified, created_at]
      properties:
        id: {type: string, description: "From data.id (numeric user ID — needed for user_tweets)"}
        name: {type: string, description: "From data.name (display name)"}
        username: {type: string, description: "From data.username (handle without @)"}
        description: {type: string, description: "From data.description (bio), or empty string"}
        followers: {type: integer, description: "From data.public_metrics.followers_count"}
        following: {type: integer, description: "From data.public_metrics.following_count"}
        tweet_count: {type: integer, description: "From data.public_metrics.tweet_count"}
        verified: {type: boolean, description: "From data.verified, default false"}
        created_at: {type: string, description: "From data.created_at"}
    output: schema
    output_transforms:
      - {op: lift, from: data}
      - {op: rename, from: public_metrics.followers_count, to: followers}
      - {op: rename, from: public_metrics.following_count, to: following}
      - {op: rename, from: public_metrics.tweet_count, to: tweet_count}
    output_renderer: text_template
    output_renderer_args:
      template: |-
        {name} (@{username})  ·  id: {id}

        {description}

        followers:   {followers:,}
        following:   {following:,}
        tweets:      {tweet_count:,}
        verified:    {verified}
        joined:      {created_at}
  - id: user_tweets
    description: Recent tweets from a user (need their numeric user ID)
    method: GET
    url: https://api.twitter.com/2/users/{user_id}/tweets
    params:
      user_id: {required: true, description: "Numeric user ID (from user_by_handle)"}
      max_results: {default: "25"}
      tweet.fields: {default: "created_at,public_metrics"}
    headers:
      Authorization: "Bearer ${X_BEARER_TOKEN}"
    output_schema:
      type: object
      additionalProperties: false
      required: [tweets]
      properties:
        tweets:
          type: array
          description: "Up to 25 entries from response.data[]"
          items:
            type: object
            additionalProperties: false
            required: [id, text, created_at, likes, retweets, replies]
            properties:
              id: {type: string, description: "From data[i].id"}
              text: {type: string, description: "From data[i].text — preserve newlines"}
              created_at: {type: string, description: "From data[i].created_at"}
              likes: {type: integer, description: "From data[i].public_metrics.like_count, or 0"}
              retweets: {type: integer, description: "From data[i].public_metrics.retweet_count, or 0"}
              replies: {type: integer, description: "From data[i].public_metrics.reply_count, or 0"}
    output: schema
    output_transforms:
      - {op: rename, list: data, from: public_metrics.like_count, to: likes}
      - {op: rename, list: data, from: public_metrics.retweet_count, to: retweets}
      - {op: rename, list: data, from: public_metrics.reply_count, to: replies}
    output_renderer: message_list
    output_renderer_args:
      header: "X — user tweets"
      list_key: data
      item_template: "  · {text}\n      ♡ {likes:,}  ⟲ {retweets:,}  ↩ {replies:,}  · {created_at}\n      https://twitter.com/i/web/status/{id}"
      empty_message: "(no tweets)"
  - id: get_tweet
    description: Get a single tweet by ID
    method: GET
    url: https://api.twitter.com/2/tweets/{tweet_id}
    params:
      tweet_id: {required: true, description: "Tweet ID"}
      tweet.fields: {default: "created_at,public_metrics,author_id"}
      expansions: {default: "author_id"}
      user.fields: {default: "username"}
    headers:
      Authorization: "Bearer ${X_BEARER_TOKEN}"
    output_schema:
      type: object
      additionalProperties: false
      required: [id, text, created_at, likes, retweets, replies, author_username]
      properties:
        id: {type: string, description: "From data.id"}
        text: {type: string, description: "From data.text"}
        created_at: {type: string, description: "From data.created_at"}
        likes: {type: integer, description: "From data.public_metrics.like_count, or 0"}
        retweets: {type: integer, description: "From data.public_metrics.retweet_count, or 0"}
        replies: {type: integer, description: "From data.public_metrics.reply_count, or 0"}
        author_username: {type: string, description: "From includes.users[0].username (the expanded author)"}
    output: schema
    output_transforms:
      - {op: rename, from: includes.users.0.username, to: author_username}   # includes sits beside data
      - {op: lift, from: data}
      - {op: rename, from: public_metrics.like_count, to: likes}
      - {op: rename, from: public_metrics.retweet_count, to: retweets}
      - {op: rename, from: public_metrics.reply_count, to: replies}
    output_renderer: text_template
    output_renderer_args:
      template: |-
        @{author_username}  ·  {created_at}

        {text}

        ♡ {likes:,}  ⟲ {retweets:,}  ↩ {replies:,}
        https://twitter.com/i/web/status/{id}
---

# X (Twitter v2 API)

Read endpoints (search, trends, public timelines, user lookups) require **at minimum** the **Pay-As-You-Go** tier (released April 2026, billed per request) or any paid plan above it (Basic/Pro/Enterprise). Free tier provides only post-your-own + read-your-own-user, which won't help here.

## Auth setup

1. Sign up at https://developer.x.com/en/portal/dashboard. Subscribe to **Pay-As-You-Go** (cheapest read access; ~cents per request) or higher.
2. In the developer portal: Project → Keys & Tokens → generate the **App-Only Bearer Token**.
3. Run `xtwitter.set`. The token is a form secret.

App-Only Bearer is sufficient for everything below. User-context OAuth2 (PKCE) is only needed for posting / private follows; out of scope for this plugin.

## Usage

### Recent search (last 7 days)

```bash
curl -s -H "Authorization: Bearer ${X_BEARER_TOKEN}" \
  'https://api.twitter.com/2/tweets/search/recent?query={QUERY}&max_results=25&tweet.fields=created_at,public_metrics,author_id'
```

Query language supports operators: `from:user`, `to:user`, `lang:en`, `-is:retweet`, `has:images`, etc. See the X docs for the full grammar.

### User by handle

```bash
curl -s -H "Authorization: Bearer ${X_BEARER_TOKEN}" \
  'https://api.twitter.com/2/users/by/username/{USERNAME}?user.fields=created_at,public_metrics,verified,description'
```

### A user's recent tweets (need their `id` from above)

```bash
curl -s -H "Authorization: Bearer ${X_BEARER_TOKEN}" \
  'https://api.twitter.com/2/users/{USER_ID}/tweets?max_results=25&tweet.fields=created_at,public_metrics'
```

### Single tweet by id

```bash
curl -s -H "Authorization: Bearer ${X_BEARER_TOKEN}" \
  'https://api.twitter.com/2/tweets/{TWEET_ID}?tweet.fields=created_at,public_metrics,author_id&expansions=author_id&user.fields=username'
```

### Trends (location-scoped — WOEID)

```bash
curl -s -H "Authorization: Bearer ${X_BEARER_TOKEN}" \
  'https://api.twitter.com/2/trends/by/woeid/{WOEID}'
```

Common WOEIDs: `1` global, `23424977` USA, `23424975` UK, `23424856` Japan, `2487956` San Francisco. Verify the trends endpoint path against the current X API docs — it has moved between revisions.

## Response shape

JSON with a top-level `data` (the requested resource) and `includes` (expanded entities like users, media). Most endpoints support `?expansions=author_id&user.fields=username` to inline author info into the response.

## Notes

- **Cost is per-request**, billed monthly to the developer account. Set a monthly cap in the dashboard before turning the agent loose. ~$0.01 per user-lookup, $0.08 per tweet-batch is a realistic baseline (your tier may differ).
- The endpoint host is `api.twitter.com` — `api.x.com` is also accepted for some endpoints. Stick with `api.twitter.com` unless docs say otherwise.
- Rate limits per endpoint are tighter than they look on PAYG — write your queries narrowly.
