# Third-party frontend assets

## Quill 1.3.7

- Files: `quill.min.js`, `quill.snow.min.css`
- Upstream: https://github.com/slab/quill
- Version: `v1.3.7`
- License: MIT; see `LICENSE` in this directory
- Source distribution mirrored by cdnjs: https://github.com/cdnjs/cdnjs/tree/master/ajax/libs/quill/1.3.7
- Runtime delivery: served locally from OpenSigWeave's `/static/vendor/quill/1.3.7/` path; no third-party CDN request is made for Quill.

Do not edit vendored minified assets manually. To upgrade, review the upstream release,
replace both files and license notice together, and verify the editor UI and regression
suite before merging.

Alpine.js is still loaded from its version-pinned CDN URL. It has not been vendored yet
because the application must first migrate to Alpine's CSP build and verify that all
existing expressions are supported without `unsafe-eval`.


## CKEditor 5 49.1.0

- Files: `ckeditor5.js` (bundled from the pinned npm package), `ckeditor5.css`, `LICENSE.md`
- Upstream: https://github.com/ckeditor/ckeditor5
- Version: `49.1.0`
- License: GNU GPL version 2 or later; used with the GPL distribution and `licenseKey: 'GPL'`.
- Build: the pinned `ckeditor5@49.1.0` npm package is bundled with esbuild during the Docker image build.
- Runtime delivery: self-hosted from `/static/vendor/ckeditor5/49.1.0/`; no CKEditor CDN requests are made.
- The application must continue to use only GPL-available features; premium plugins are not included.
