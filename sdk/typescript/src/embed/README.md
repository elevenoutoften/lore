# Lore Embed Widget

The Lore embed widget is a lightweight iframe loader for placing Lore pages,
search, or capture review content inside another HTML page. Rendering stays on
the Lore server at `/embed`.

## Drop-in Script Tag

The Lore server does not ship this bundle, so host it yourself: copy the built
`lore-embed.js` from the `axis-lore-sdk` package (`dist/esm/embed/lore-embed.js`)
to a path your own server serves and point the `src` at it.

```html
<div id="lore-embed"></div>
<script src="https://your-app.example.com/assets/lore-embed.js"></script>
<script>
  LoreEmbed.mount({
    baseUrl: "https://lore.example.com",
    mode: "page",
    pageId: "projects/example-project",
    theme: "auto"
  });
</script>
```

Use a custom container with `container`:

```html
<div class="project-notes"></div>
<script>
  LoreEmbed.mount({
    baseUrl: "https://lore.example.com",
    mode: "search",
    container: ".project-notes",
    theme: "dark"
  });
</script>
```

## NPM Module Usage

```ts
import { mountLoreEmbed } from "axis-lore-sdk/embed/lore-embed";

mountLoreEmbed({
  baseUrl: "https://lore.example.com",
  mode: "page",
  pageId: "services/workflow-engine",
  container: "#lore-embed",
  authToken: sessionStorage.getItem("lore_token") ?? undefined,
});
```

## Config Options

| Option | Required | Description |
| --- | --- | --- |
| `baseUrl` | Yes | Lore server origin, without a trailing path. |
| `mode` | Yes | `page`, `search`, or `capture`. |
| `pageId` | For `page` | Lore page ID to render. |
| `query` | No | Initial search term for `mode: "search"` embeds. |
| `container` | No | CSS selector for the mount target. Defaults to `#lore-embed`. |
| `theme` | No | `light`, `dark`, or `auto`. Defaults to `auto`. |
| `authToken` | No | Bearer token passed to the iframe with `postMessage`. |

## Auth Strategies

- Public embeds: omit `authToken` and expose only public Lore content through
  server-side auth rules.
- Session token: read a short-lived token from your application session and pass
  it as `authToken`.
- Same-origin gateway: host the embedding page behind the same auth gateway as
  Lore, then rely on cookies or gateway headers and omit `authToken`.

The widget sends auth with `postMessage` after the iframe loads. The iframe also
sends resize messages so the parent page can fit the embedded content without
nested scrolling.
