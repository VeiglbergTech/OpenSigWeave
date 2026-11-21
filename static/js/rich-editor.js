(function () {
    "use strict";

    const allowedPlugins = [
        "Essentials", "Paragraph", "Heading", "Bold", "Italic", "Underline",
        "Font", "Link", "List", "Alignment", "Table", "TableToolbar",
        "GeneralHtmlSupport", "PasteFromOffice", "BlockQuote", "HorizontalLine", "Image", "ImageInline", "ImageBlock", "ImageToolbar", "ImageResize"
    ];

    // Keep the editor's General HTML Support aligned with the server sanitizer.
    // This is an editing-fidelity setting only; signature_sanitizer.py remains
    // the authoritative security boundary for preview and delivery.
    const allowedElements = [
        "p", "div", "center", "span", "br", "strong", "b", "em", "i", "u", "s",
        "small", "sub", "sup", "code", "pre", "a", "img", "font",
        "table", "thead", "tbody", "tfoot", "tr", "td", "th",
        "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
        "blockquote", "hr"
    ];

    class SignatureImageUploadAdapter {
        constructor(loader, config) {
            this.loader = loader;
            this.config = config || {};
            this.xhr = null;
        }

        upload() {
            return this.loader.file.then(file => new Promise((resolve, reject) => {
                const xhr = this.xhr = new XMLHttpRequest();
                xhr.open("POST", this.config.uploadUrl || "/api/admin/signature-assets", true);
                xhr.responseType = "json";
                xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
                const csrfToken = this.config.csrfToken || document.body.dataset.csrfToken || "";
                if (csrfToken) xhr.setRequestHeader("X-CSRF-Token", csrfToken);
                xhr.setRequestHeader("Accept", "application/json");

                xhr.addEventListener("error", () => reject("Image upload failed."));
                xhr.addEventListener("abort", () => reject());
                xhr.addEventListener("load", () => {
                    const response = xhr.response;
                    if (xhr.status < 200 || xhr.status >= 300 || !response || !response.url) {
                        const detail = response && response.detail ? response.detail : "Image upload failed.";
                        reject(detail);
                        return;
                    }
                    resolve({ default: new URL(response.url, window.location.origin).href });
                });
                if (xhr.upload) {
                    xhr.upload.addEventListener("progress", event => {
                        if (event.lengthComputable) {
                            this.loader.uploadTotal = event.total;
                            this.loader.uploaded = event.loaded;
                        }
                    });
                }
                xhr.send(file);
            }));
        }

        abort() {
            if (this.xhr) this.xhr.abort();
        }
    }

    function signatureImageUploadPlugin(config) {
        return function (editor) {
            editor.plugins.get("FileRepository").createUploadAdapter = loader =>
                new SignatureImageUploadAdapter(loader, config);
        };
    }

    async function create(element, options) {
        const config = options || {};
        const library = window.CKEDITOR;
        if (!library || !library.ClassicEditor) {
            throw new Error("CKEditor 5 failed to load.");
        }

        const pluginNames = config.imageUpload ? [...allowedPlugins, "ImageUpload"] : allowedPlugins;
        const missingPlugins = pluginNames.filter(name => !library[name]);
        if (missingPlugins.length) {
            throw new Error("CKEditor plugins missing: " + missingPlugins.join(", "));
        }
        const plugins = pluginNames.map(name => library[name]);
        const toolbar = [
            "undo", "redo", "|", "heading", "|",
            "fontFamily", "fontSize", "fontColor", "fontBackgroundColor", "|",
            "bold", "italic", "underline", "link", "|",
            "bulletedList", "numberedList", "|", "alignment", "insertTable"
        ];
        if (config.imageUpload) toolbar.push("uploadImage");
        toolbar.push("|", "blockQuote", "horizontalLine");
        const editorConfig = {
            licenseKey: "GPL",
            plugins,
            toolbar: config.imageUpload ? { items: toolbar, shouldNotGroupWhenFull: true } : toolbar,
            // Numeric values produce inline font-size: Npx (not CKEditor text-size classes).
            // Keep other safe sizes and fonts when loading existing HTML signatures.
            fontSize: {
                options: ["default", 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24, 28, 32, 36, 40, 48, 60, 72],
                supportAllValues: true
            },
            fontFamily: {
                options: [
                    "default",
                    "Arial, Helvetica, sans-serif",
                    "Calibri, Arial, sans-serif",
                    "Verdana, Geneva, sans-serif",
                    "Tahoma, Geneva, sans-serif",
                    "Trebuchet MS, Helvetica, sans-serif",
                    "Georgia, serif",
                    "Times New Roman, Times, serif",
                    "Courier New, Courier, monospace"
                ],
                supportAllValues: true
            },
            table: {
                contentToolbar: ["tableColumn", "tableRow", "mergeTableCells"]
            },
            htmlSupport: {
                allow: [{
                    name: new RegExp("^(" + allowedElements.join("|") + ")$"),
                    attributes: [
                        "style", "class", "href", "src", "alt", "title", "width", "height",
                        "align", "valign", "border", "cellpadding", "cellspacing",
                        "colspan", "rowspan", "target", "rel", "dir", "role", "id",
                        "face", "color", "size", "start", "type", "value"
                    ],
                    classes: true,
                    styles: true
                }]
            },
            link: { addTargetToExternalLinks: false },
            image: {
                insert: { type: "inline" },
                toolbar: ["resizeImage"],
                resizeUnit: "px",
                resizeOptions: [
                    { name: "resizeImage:original", value: null, label: "Original" },
                    { name: "resizeImage:custom", value: "custom", label: "Custom width" },
                    { name: "resizeImage:120", value: "120", label: "120 px" },
                    { name: "resizeImage:240", value: "240", label: "240 px" },
                    { name: "resizeImage:480", value: "480", label: "480 px" }
                ],
                upload: { types: ["png", "jpeg", "webp", "gif"] }
            },
            initialData: typeof config.initialData === "string" ? config.initialData : "",
            placeholder: config.placeholder || "Create your email signature…"
        };
        if (config.imageUpload) {
            editorConfig.extraPlugins = [signatureImageUploadPlugin(config)];
        }
        const editor = await library.ClassicEditor.create(element, editorConfig);

        let onChange = typeof config.onChange === "function" ? config.onChange : null;
        editor.model.document.on("change:data", function () {
            if (onChange) onChange(editor.getData());
        });

        let readOnlyLock = false;
        if (config.readOnly) {
            editor.enableReadOnlyMode("opensigweave");
            readOnlyLock = true;
        }

        return {
            getData: function () { return editor.getData(); },
            setOnChange: function (callback) { onChange = typeof callback === "function" ? callback : null; },
            setData: function (html) { editor.setData(typeof html === "string" ? html : ""); },
            focus: function () { editor.editing.view.focus(); },
            insertImage: function (url) {
                if (typeof url !== "string" || !url.startsWith("https://")) {
                    throw new Error("Only HTTPS images may be inserted.");
                }
                editor.editing.view.focus();
                editor.execute("insertImage", { source: url });
            },
            insertText: function (value) {
                editor.editing.view.focus();
                editor.execute("insertText", { text: String(value || "") });
            },
            setReadOnly: function (value) {
                if (value && !readOnlyLock) {
                    editor.enableReadOnlyMode("opensigweave");
                    readOnlyLock = true;
                } else if (!value && readOnlyLock) {
                    editor.disableReadOnlyMode("opensigweave");
                    readOnlyLock = false;
                }
            },
            destroy: function () { return editor.destroy(); }
        };
    }

    window.OpenSigWeaveEditor = { create: create };
})();
