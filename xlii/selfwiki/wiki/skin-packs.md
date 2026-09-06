---
sources: file://xlii/skin_packs.py, file://xlii/face_assets/js/skins.js, file://xlii/face_assets/css/face.css, file://xlii/cmds/skin.py, file://xlii/serve_face/http.py, file://xlii/face_assets/skins/y2k/skin.css
verified: false
---
# skin-packs

Graphical skins for the [[desktop-face]] — Winamp-grade chrome on an undecorated
window, as plain files. Same `html[data-skin]` contract the four compiled skins
already use; packs only *fill more variables*. See [[command-surface]] for `xlii
skin`.

## The contract

Every piece of chrome reads CSS custom properties. Compiled skins
(`dark` / `light` / `slate` / `mojo`) set colors and leave image slots `none` —
pixel-equivalent empty. A pack sets `data-skin="pack:<name>"` and fills:

| Slot | Role | Builtin default |
|------|------|-----------------|
| `--bg` `--bg-raised` `--bg-input` `--fg` `--fg-dim` `--edge` `--edge-btn` `--mojo` `--code` `--warn` `--error` `--mono` | color / type (already shipped) | the four palettes |
| `--font-ui` `--font-mono` | UI + mono stacks; local/packaged fonts only | system-ui / `--mono` |
| `--chrome-menubar-img` `--chrome-pane-img` `--chrome-input-img` `--chrome-inputbar-img` `--chrome-status-img` `--chrome-slot-img` | region bitmaps (`background-image`) | `none` |
| `--chrome-btn-img` `--chrome-btn-hover-img` `--chrome-btn-active-img` | cbuttons up / hover / down | `none` |
| `--chrome-win-img` `--chrome-win-close-img` | titlebar min/max and close | `none` |
| `--texture-bg` `--texture-transcript` | tiled window body + LCD/transcript | `none` |
| `--slice-menubar` `--slice-pane` `--slice-dock` `--slice-btn` `--slice-flip` `--slice-scroll` | `border-image` 9-slice shorthand | `none` |

Optional size/repeat knobs (not forwarded to form iframes; unset = `100% 100%` / `no-repeat`): `--chrome-menubar-size` / `--chrome-menubar-repeat`, and the same suffix on `pane`, `btn`, `input`, `inputbar`, `status`, `slot`. Titlebars want `auto 100%` + `repeat-x`; pledit wells want `auto` + `repeat`.

## Winamp region map

The face is not 275×116, but the *files* map 1:1 onto Winamp's. Attach bitmaps to slots; 9-slice only the frame.

| Winamp | Slot | Face region |
|--------|------|-------------|
| `main.bmp` | `--texture-bg` | window body (tiles) |
| `titlebar.bmp` | `--chrome-menubar-img` + `--slice-menubar` | `#menubar` |
| `cbuttons.bmp` | `--chrome-btn-img` + hover/active | `#send` `#stop` `#flip` `.pane-action` … |
| `pledit.bmp` | `--chrome-pane-img` + `--slice-pane` / `--slice-dock` | side dock, `#panedeck` |
| pledit title | `--chrome-slot-img` | `.slot-chrome` |
| LCD / `text.bmp` | `--texture-transcript` + `--chrome-input-img` | `#transcript` `#input` |
| bottom of main | `--chrome-inputbar-img` | `#inputbar` |
| (eq / posbar strip) | `--chrome-status-img` | `#statusbar` `#fkeybar` |
| min/max in titlebar | `--chrome-win-img` | `.win-btn` |
| `close.bmp` | `--chrome-win-close-img` | `.win-btn[title=close]` |

Omit `fill` on `border-image` when a chrome bitmap is set — `fill` paints the 9-slice center and hides the BMP. Buttons are sprites (`--slice-btn: none`); hover/active layer with `background-image: var(--hover), var(--up)` so a pack that only ships the up state still works. Shipped examples: `y2k` (bronze Fireworks) and `classic` (Winamp 2 visor).

Sandboxed form iframes receive the same list via `embedCssForSkin` (absolute
`/skins/<name>/…` urls survive the iframe). Do not skin form *contents* beyond
that forwarding contract.

## Pack layout

A pack is a directory named with `^[a-z][a-z0-9_-]{0,31}$`. It cannot be
`dark`, `light`, `slate`, or `mojo`.

```
<name>/
  skin.toml     # label, blurb, scheme = "dark"|"light"
  skin.css      # ONLY selectors under html[data-skin="pack:<name>"]
  *.svg *.png … # images / woff2; url() paths must be /skins/<name>/file.ext
```

Shipped packs: `xlii/face_assets/skins/y2k/`, `xlii/face_assets/skins/classic/`. User packs:
`~/.config/xlii/skins/<name>/`. Ids are `pack:<name>` so a folder can never
shadow a compiled skin. Builtin packs win on name collision.

`url()` must be absolute (`/skins/y2k/btn.svg`) — relative urls die inside
form iframes and fail `xlii skin check`. No `https://`, no `@import`, no
script-like CSS. Extension allowlist: `css png jpg jpeg webp gif svg woff2 toml`.
Caps: 2 MiB per file, 8 MiB per pack.

## 9-slice recipe

The face is responsive; Winamp was 275×116. Skin *chrome*, not layout.

1. Draw a square (24×24 is plenty) with a distinct corner, edge, and center.
2. Put it in the pack (y2k uses `menubar.svg` / `pane.svg` as frames;
   `btn.svg` is the cbuttons sprite, not a slice).
3. Set the slot to the `border-image` shorthand, slice = corner size in px:

```
html[data-skin="pack:y2k"] {
  --chrome-menubar-img: url("/skins/y2k/titlebar.svg");
  --slice-menubar: url("/skins/y2k/menubar.svg") 8 / 8px / 0 stretch;
  --chrome-btn-img: url("/skins/y2k/btn.svg");
}
```

`8` is `border-image-slice` (the inset that carves corners). `/ 8px` is the
border width those slices paint into. Skip `fill` so the center stays
transparent and `--chrome-*-img` shows through. `stretch` (not `repeat` on
the corners) is what keeps a 400px and a 1400px window looking like the
same skin. Tiled textures (`--texture-bg`) *do* repeat — that's the
wallpaper, not the frame.

y2k and classic are the annotated examples: open `skin.css` next to the
SVGs (titlebar, cbuttons up/hover/down, pledit fill) and resize the
undecorated window. Scrollbar styling is pack-scoped
(`html[data-skin="pack:y2k"] ::-webkit-scrollbar`) — compiled skins never
touch the UA scrollbar.

## The loop

```
xlii skin check ./my-pack     # lint: scope, urls, size, extensions
xlii skin install ./my-pack   # copy into ~/.config/xlii/skins/  (local only)
xlii skin list                # compiled + discovered
xlii skin check y2k           # the showcase, by name
```

The face picker (`skins://`) merges compiled skins with `GET /skins/catalog`.
Selecting a pack injects `<link id="xlii-pack-css" href="/skins/<name>/skin.css">`
and sets `data-skin`. Switching back to dark removes the link — no residue.
A missing pack falls back to `dark`. The TailnetDoor **door** tier does not
serve `/skins/`; the **glass** tier (`/remote-control open --glass`) does,
after WhoIs, under the same CSP as the rest of the glass.

`install` copies a local directory. There is no network fetch, no gallery,
no scriptable skins. CSP already forbids remote origins; a malicious pack
can look ugly or deceptive, not phone home.
