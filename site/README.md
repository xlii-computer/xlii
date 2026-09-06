# Landing page — source for xlii.computer

`index.html` + `style.css`. No build step. The palette mirrors the desktop
client (`xlii/face_assets/css/face.css`). The only script on the page rewrites
documentation and source links from a single `REPO` constant.

## Preview

```bash
python3 -m http.server -d site 8080     # http://localhost:8080
```

## Publish

The live site is served from `xlii-computer/xlii-computer.github.io`, which
already carries `CNAME` (`xlii.computer`) and `.nojekyll`. Copy the two files
in and push:

```bash
cp site/index.html site/style.css ../xlii-computer.github.io/
cd ../xlii-computer.github.io && git add -A && git commit -m "site: landing page" && git push
```

## After the repository transfer

Set `REPO` in `index.html` to the repository's new URL, e.g.
`https://github.com/xlii-computer/xlii`. Every link marked `data-repo-path`
resolves against it. Until then the links fall back to the organization page.

Links assume `docs/DESIGN.md`, `docs/GOLDEN-PATH.md`, `docs/HOWTO.md`, and
`SECURITY.md` exist on the default branch.
