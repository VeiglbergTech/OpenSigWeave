# Third-party frontend assets

CKEditor 5 is the only rich-text editor shipped by OpenSigWeave. Legacy Quill assets have been removed.



## CKEditor 5 49.1.0

- Files: `ckeditor5.js` (bundled from the pinned npm package), `ckeditor5.css`, `LICENSE.md`
- Upstream: https://github.com/ckeditor/ckeditor5
- Version: `49.1.0`
- License: GNU GPL version 2 or later; used with the GPL distribution and `licenseKey: 'GPL'`.
- Build: the pinned `ckeditor5@49.1.0` npm package is bundled with esbuild during the Docker image build.
- Runtime delivery: self-hosted from `/static/vendor/ckeditor5/49.1.0/`; no CKEditor CDN requests are made.
- The application must continue to use only GPL-available features; premium plugins are not included.
