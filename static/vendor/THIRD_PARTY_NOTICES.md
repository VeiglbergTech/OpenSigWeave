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
