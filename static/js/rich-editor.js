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

    const SIGNATURE_FONT_FAMILIES = [
        ["Arial", "Arial"],
        ["Verdana", "Verdana"],
        ["Tahoma", "Tahoma"],
        ["Georgia", "Georgia"],
        ["Times New Roman", "Times New Roman"],
        ["Courier New", "Courier New"],
        ["Calibri", "Calibri"],
        ["Trebuchet MS", "Trebuchet MS"]
    ];
    const SIGNATURE_FONT_SIZES = [10, 12, 14, 16, 18, 20, 24, 28, 32, 36];

    // A permanent, browser-native toolbar avoids CKEditor's collapsing overflow
    // and clipped popovers inside the scrollable signature modal.
    // Commands are still CKEditor commands: no HTML mutation or parallel editor state.
    function installSignatureFontControls(editor) {
        const toolbar = editor.ui.view.toolbar.element;
        const familyCommand = editor.commands.get("fontFamily");
        const sizeCommand = editor.commands.get("fontSize");
        if (!toolbar || !toolbar.parentElement || !familyCommand || !sizeCommand) {
            throw new Error("Signature font commands or editor toolbar unavailable.");
        }

        const bar = document.createElement("div");
        bar.className = "osw-editor-font-toolbar";
        bar.setAttribute("role", "toolbar");
        bar.setAttribute("aria-label", "Signature font formatting");

        function addSelect(labelText, ariaLabel, options, className) {
            const label = document.createElement("label");
            label.className = "osw-editor-font-field";
            const caption = document.createElement("span");
            caption.textContent = labelText;
            const select = document.createElement("select");
            select.className = className;
            select.setAttribute("aria-label", ariaLabel);
            for (const [text, value] of options) {
                const option = document.createElement("option");
                option.value = value;
                option.textContent = text;
                select.appendChild(option);
            }
            label.append(caption, select);
            bar.appendChild(label);
            return select;
        }

        const familySelect = addSelect("Font", "Signature font family",
            [["Default font", "default"], ...SIGNATURE_FONT_FAMILIES],
            "osw-font-family-select");
        const sizeSelect = addSelect("Size", "Signature font size",
            [["Default size", "default"], ...SIGNATURE_FONT_SIZES.map(px => [px + " px", String(px)])],
            "osw-font-size-select");

        const customLabel = document.createElement("label");
        customLabel.className = "osw-editor-font-field osw-editor-font-custom";
        const customCaption = document.createElement("span");
        customCaption.textContent = "Custom size (px)";
        const customSize = document.createElement("input");
        customSize.type = "number";
        customSize.min = "6";
        customSize.max = "96";
        customSize.step = "1";
        customSize.placeholder = "6–96";
        customSize.className = "osw-font-px-input";
        customSize.setAttribute("aria-label", "Custom signature font size in pixels");
        customLabel.append(customCaption, customSize);
        bar.appendChild(customLabel);
        toolbar.parentElement.insertBefore(bar, toolbar);

        function selectedOption(select, value, description) {
            const previous = select.querySelector("option[data-osw-custom]");
            if (previous) previous.remove();
            const known = Array.from(select.options).some(option => option.value === value);
            if (!known && value !== "default") {
                const option = document.createElement("option");
                option.dataset.oswCustom = "true";
                option.value = value;
                option.textContent = description;
                select.appendChild(option);
            }
            select.value = value;
        }

        function syncWithSelection() {
            const rawFamily = typeof familyCommand.value === "string"
                ? familyCommand.value.trim() : "";
            // Recognized values are model names ("Georgia"), whereas imported
            // legacy HTML may expose the full CSS fallback chain.
            const configuredFamily = SIGNATURE_FONT_FAMILIES.find(
                ([, model]) => model.toLowerCase() === rawFamily.toLowerCase()
                    || rawFamily.toLowerCase().startsWith(model.toLowerCase() + ",")
            );
            const family = configuredFamily ? configuredFamily[1] : (rawFamily || "default");
            selectedOption(familySelect, family, family);
            const rawSize = String(sizeCommand.value == null ? "" : sizeCommand.value).trim();
            const match = /^(\d+(?:\.\d+)?)(?:px)?$/i.exec(rawSize);
            const px = match ? String(Number(match[1])) : "default";
            selectedOption(sizeSelect, px, px + " px");
            customSize.value = px === "default" ? "" : px;
            familySelect.disabled = editor.isReadOnly || !familyCommand.isEnabled;
            sizeSelect.disabled = editor.isReadOnly || !sizeCommand.isEnabled;
            customSize.disabled = editor.isReadOnly || !sizeCommand.isEnabled;
        }

        familySelect.addEventListener("change", () => {
            if (familySelect.disabled) return;
            if (familySelect.value === "default") editor.execute("fontFamily");
            else editor.execute("fontFamily", { value: familySelect.value });
            editor.editing.view.focus();
            syncWithSelection();
        });
        sizeSelect.addEventListener("change", () => {
            if (sizeSelect.disabled) return;
            if (sizeSelect.value === "default") editor.execute("fontSize");
            else editor.execute("fontSize", { value: Number(sizeSelect.value) + "px" });
            editor.editing.view.focus();
            syncWithSelection();
        });
        function applyCustomSize() {
            if (customSize.disabled || customSize.value === "") return;
            const value = Number(customSize.value);
            if (!Number.isInteger(value) || value < 6 || value > 96) {
                customSize.setCustomValidity("Enter an integer pixel size from 6 to 96.");
                customSize.reportValidity();
                return;
            }
            customSize.setCustomValidity("");
            editor.execute("fontSize", { value: value + "px" });
            editor.editing.view.focus();
            syncWithSelection();
        }
        customSize.addEventListener("change", applyCustomSize);
        customSize.addEventListener("keydown", event => {
            if (event.key === "Enter") {
                event.preventDefault();
                applyCustomSize();
            }
        });
        customSize.addEventListener("input", () => customSize.setCustomValidity(""));

        familyCommand.on("change:value", syncWithSelection);
        familyCommand.on("change:isEnabled", syncWithSelection);
        sizeCommand.on("change:value", syncWithSelection);
        sizeCommand.on("change:isEnabled", syncWithSelection);
        editor.on("change:isReadOnly", syncWithSelection);
        syncWithSelection();
    }


    // An additional native control lives in CKEditor's existing toolbar. Using a
    // local button instead of duplicating the editor avoids altering upload plugins.
    function installSignatureImageUrlButton(editor, onImport) {
        const toolbar = editor.ui.view.toolbar.element;
        const items = toolbar && (toolbar.querySelector(".ck-toolbar__items") || toolbar);
        if (!items || typeof onImport !== "function") {
            throw new Error("Signature image URL toolbar is unavailable.");
        }
        const button = document.createElement("button");
        button.type = "button";
        button.className = "ck ck-button ck-off osw-editor-import-url-button";
        button.setAttribute("aria-label", "Import Image from URL");
        button.title = "Import Image from URL";

        const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        for (const [name, value] of Object.entries({
            viewBox: "0 0 24 24", width: "20", height: "20",
            fill: "none", stroke: "currentColor", "stroke-width": "1.7",
            "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true"
        })) icon.setAttribute(name, value);
        const addShape = (tag, attributes) => {
            const shape = document.createElementNS("http://www.w3.org/2000/svg", tag);
            for (const [name, value] of Object.entries(attributes)) shape.setAttribute(name, value);
            icon.appendChild(shape);
        };
        addShape("rect", { x: "3", y: "4", width: "18", height: "16", rx: "2" });
        addShape("circle", { cx: "8", cy: "9", r: "1.5" });
        addShape("path", { d: "m4 17 5-5 4 4 2-2 5 4" });
        const label = document.createElement("span");
        label.className = "ck ck-button__label";
        label.textContent = "Import Image from URL";
        button.append(icon, label);
        const update = () => { button.disabled = editor.isReadOnly; };
        editor.on("change:isReadOnly", update);
        button.addEventListener("click", () => {
            if (!editor.isReadOnly) onImport();
        });

        const uploadButton = [...items.querySelectorAll("button")].find(candidate =>
            /upload (?:image|from computer)/i.test(
                (candidate.getAttribute("aria-label") || "") + " " +
                (candidate.getAttribute("data-cke-tooltip-text") || "")
            )
        );
        if (uploadButton) uploadButton.after(button);
        else items.appendChild(button);
        update();
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
            ...(config.signatureFontControls ? [] : ["fontFamily", "fontSize"]),
            "fontColor", "fontBackgroundColor", "|",
            "bold", "italic", "underline", "link", "|",
            "bulletedList", "numberedList", "|", "alignment", "insertTable"
        ];
        if (config.imageUpload) toolbar.push("uploadImage");
        toolbar.push("|", "blockQuote", "horizontalLine");
        const editorConfig = {
            licenseKey: "GPL",
            plugins,
            toolbar: (config.imageUpload || config.signatureFontControls) ? { items: toolbar, shouldNotGroupWhenFull: true } : toolbar,
            // Numeric values produce inline font-size: Npx (not CKEditor text-size classes).
            // Keep other safe sizes and fonts when loading existing HTML signatures.
            fontSize: {
                // Register the full editable range for command conversion even
                // though the visible selector offers a shorter curated list.
                options: config.signatureFontControls
                    ? ["default", ...Array.from({ length: 91 }, (_, index) => index + 6)]
                    : ["default", 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24, 28, 32, 36, 40, 48, 60, 72],
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
        if (config.signatureFontControls) installSignatureFontControls(editor);
        let imageImportSelection = null;
        if (config.imageUpload && typeof config.onImportImageFromUrl === "function") {
            installSignatureImageUrlButton(editor, () => {
                // Retain the model range while the URL dialog is focused and the
                // download is in progress; inserting must not jump to the end.
                imageImportSelection = editor.model.document.selection.getFirstRange();
                config.onImportImageFromUrl();
            });
        }

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
                // Only immutable images returned from the local asset store may be
                // inserted here. Remote HTTPS URLs must use the validated import API.
                let asset;
                try { asset = new URL(url, window.location.origin); }
                catch (_) { throw new Error("Invalid managed image URL."); }
                if (asset.origin !== window.location.origin ||
                    !/^\/signature-assets\/[a-f0-9]{64}\.(?:png|jpg|webp|gif)$/.test(asset.pathname) ||
                    asset.search || asset.hash || editor.isReadOnly) {
                    throw new Error("Only local managed signature images may be inserted.");
                }
                if (imageImportSelection) {
                    editor.model.change(writer => writer.setSelection(imageImportSelection));
                }
                editor.execute("insertImage", { source: asset.href });
                imageImportSelection = null;
                editor.editing.view.focus();
            },
            saveImageImportSelection: function () {
                imageImportSelection = editor.model.document.selection.getFirstRange();
            },
            clearImageImportSelection: function () { imageImportSelection = null; },
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
