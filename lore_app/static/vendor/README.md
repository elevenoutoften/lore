# Vendored front-end libraries

These files are third-party libraries vendored locally so the browser surface
works offline and under Lore's `script-src 'self'` Content-Security-Policy
(no CDN calls). Do not edit them by hand.

## vis-network.min.js

- Library: [vis-network](https://visjs.github.io/vis-network/) (standalone UMD bundle)
- Version: 9.1.9
- License: MIT / Apache-2.0 (dual-licensed)
- Used by: `templates/graph.html` for the interactive context graph.

To update, replace the file with a newer standalone UMD build from the same
project and update the version above. The file is allowlisted in
`scripts/scan_secrets_baseline.txt` because minified bundles contain library
URLs and XML namespaces that the privacy scanner would otherwise flag.
