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

    // Font formatting is a single CKEditor component-factory integration.
    // Both controls execute CKEditor's native fontFamily/fontSize commands.
    // Use system fonts with CSS fallbacks; no webfont downloads are required.
    // CKEditor uses the first family name as the command value and serializes
    // the full configured stack as an inline font-family declaration.
    const SIGNATURE_FONT_FAMILIES = [
        ["Arial", "Arial, Helvetica, sans-serif"],
        ["Arial Black", "Arial Black, Arial, sans-serif"],
        ["Calibri", "Calibri, Arial, sans-serif"],
        ["Cambria", "Cambria, Georgia, Times New Roman, serif"],
        ["Courier New", "Courier New, Courier, monospace"],
        ["Garamond", "Garamond, Georgia, Times New Roman, serif"],
        ["Georgia", "Georgia, serif"],
        ["Helvetica", "Helvetica, Arial, sans-serif"],
        ["Lucida Sans Unicode", "Lucida Sans Unicode, Lucida Grande, sans-serif"],
        ["Palatino Linotype", "Palatino Linotype, Palatino, Georgia, serif"],
        ["Segoe UI", "Segoe UI, Tahoma, Arial, sans-serif"],
        ["Tahoma", "Tahoma, Geneva, sans-serif"],
        ["Times New Roman", "Times New Roman, Times, serif"],
        ["Trebuchet MS", "Trebuchet MS, Helvetica, sans-serif"],
        ["Verdana", "Verdana, Geneva, sans-serif"]
    ];
    const SIGNATURE_FONT_SIZES_PT = [8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36];

    function fontSizeLabel(value) {
        if (value === undefined || value === null || value === "") return "Font size";
        const size = String(value).trim();
        const match = /^(\d+(?:\.\d+)?)(px|pt)$/i.exec(size);
        if (match) return Number(match[1]) + " " + match[2].toLowerCase();
        if (/^\d+(?:\.\d+)?$/.test(size)) return size + " px";
        return size;
    }

    // Unit-less custom sizes default to pt; explicit px remains supported for
    // existing signature content. Validate before touching CKEditor's model.
    function parseFontSizeInput(raw) {
        const match = /^(\d{1,3}(?:[.,]\d{1,2})?)\s*(pt|px)?$/i.exec(String(raw).trim());
        if (!match) return null;
        const size = Number(match[1].replace(",", "."));
        if (!Number.isFinite(size) || size < 6 || size > 96) return null;
        return String(size) + (match[2] || "pt").toLowerCase();
    }

    function signatureFontToolbarPlugin(editor) {
        const ui = window.CKEDITOR;

        function makeDropdown(locale, className, caption, command) {
            const dropdown = ui.createDropdown(locale);
            dropdown.class = className;
            dropdown.buttonView.set({ label: caption, withText: true, tooltip: caption });
            dropdown.bind("isEnabled").to(command, "isEnabled");
            return dropdown;
        }

        function addOptions(dropdown, labels) {
            const items = new ui.Collection();
            for (const label of labels) {
                items.add({ type: "button", model: new ui.UIModel({ label, withText: true }) });
            }
            ui.addListToDropdown(dropdown, items, { ariaLabel: "Font formatting options" });
        }

        editor.ui.componentFactory.add("signatureFontFamily", locale => {
            const command = editor.commands.get("fontFamily");
            const dropdown = makeDropdown(locale, "osw-signature-family-dropdown", "Font family", command);
            addOptions(dropdown, ["Default font", ...SIGNATURE_FONT_FAMILIES.map(([name]) => name)]);

            function update() {
                const value = typeof command.value === "string" ? command.value.trim() : "";
                // Imported HTML may quote a family containing spaces. Match
                // the first face without discarding unknown legacy families.
                const firstFamily = value.split(",")[0].trim().replace(/^(['"])(.*)\1$/, "$2");
                const option = SIGNATURE_FONT_FAMILIES.find(([name, css]) =>
                    firstFamily.toLowerCase() === name.toLowerCase() ||
                    value.toLowerCase() === css.toLowerCase()
                );
                dropdown.buttonView.label = option ? option[0] : (value || "Font family");
            }

            dropdown.on("execute", event => {
                const name = event.source.label;
                if (name === "Default font") editor.execute("fontFamily");
                else {
                    const option = SIGNATURE_FONT_FAMILIES.find(([label]) => label === name);
                    if (!option) return;
                    editor.execute("fontFamily", { value: option[0] });
                }
                editor.editing.view.focus();
                update();
            });
            command.on("change:value", update);
            update();
            return dropdown;
        });

        editor.ui.componentFactory.add("signatureFontSize", locale => {
            const command = editor.commands.get("fontSize");
            const dropdown = makeDropdown(locale, "osw-signature-size-dropdown", "Font size", command);
            const input = new ui.LabeledFieldView(locale, ui.createLabeledInputText);
            input.set({ label: "Custom font size (pt or px)", value: "" });
            // Presets and free-form values share this one CKEditor dropdown.
            dropdown.panelView.children.add(input);
            addOptions(dropdown, ["Default size", ...SIGNATURE_FONT_SIZES_PT.map(size => size + " pt")]);

            function update() {
                dropdown.buttonView.label = fontSizeLabel(command.value);
                const element = input.fieldView.element;
                if (element && document.activeElement !== element) {
                    element.value = command.value == null ? "" : String(command.value);
                }
            }

            function apply(value) {
                if (value === null) editor.execute("fontSize");
                else editor.execute("fontSize", { value });
                dropdown.isOpen = false;
                editor.editing.view.focus();
                update();
            }

            dropdown.on("execute", event => {
                const label = event.source.label;
                if (label === "Default size") apply(null);
                else if (SIGNATURE_FONT_SIZES_PT.some(size => label === size + " pt")) {
                    apply(label.replace(/\s+/g, ""));
                }
            });

            // CKEditor renders and owns this input inside its dropdown panel.
            // Only wire DOM keyboard/validation events after its first opening.
            let inputBound = false;
            dropdown.on("change:isOpen", () => {
                if (!dropdown.isOpen) return;
                const element = input.fieldView.element;
                if (!element) return;
                if (!inputBound) {
                    inputBound = true;
                    element.setAttribute("inputmode", "decimal");
                    element.setAttribute("placeholder", "e.g. 13 or 17,5 pt");
                    element.addEventListener("input", () => element.setCustomValidity(""));
                    element.addEventListener("keydown", event => {
                        if (event.key !== "Enter") return;
                        event.preventDefault();
                        event.stopPropagation();
                        const value = parseFontSizeInput(element.value);
                        if (!value) {
                            element.setCustomValidity("Enter a size from 6 to 96 pt (or px), with up to two decimals.");
                            element.reportValidity();
                            return;
                        }
                        element.setCustomValidity("");
                        apply(value);
                    });
                }
                update();
            });
            command.on("change:value", update);
            update();
            return dropdown;
        });
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
            "signatureFontFamily", "signatureFontSize", "fontColor", "fontBackgroundColor", "|",
            "bold", "italic", "underline", "link", "|",
            "bulletedList", "numberedList", "|", "alignment", "insertTable"
        ];
        if (config.imageUpload) toolbar.push("uploadImage");
        toolbar.push("|", "blockQuote", "horizontalLine");
        const editorConfig = {
            licenseKey: "GPL",
            plugins,
            toolbar: { items: toolbar, shouldNotGroupWhenFull: true },
            // Numeric options plus supportAllValues preserve imported px/pt
            // formatting, even when the value is absent from the preset UI.
            fontSize: {
                options: ["default", ...Array.from({ length: 91 }, (_, index) => index + 6)],
                supportAllValues: true
            },
            fontFamily: {
                options: ["default", ...SIGNATURE_FONT_FAMILIES.map(([, css]) => css)],
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
        editorConfig.extraPlugins = [signatureFontToolbarPlugin];
        if (config.imageUpload) {
            editorConfig.extraPlugins.push(signatureImageUploadPlugin(config));
        }
        const editor = await library.ClassicEditor.create(element, editorConfig);
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
        let sourceHtml = editorConfig.initialData;
        let normalizedBaseline = editor.getData();
        let settingData = false;

        // CKEditor normalizes or drops unsupported markup (including Outlook
        // comments/VML) during conversion. Merely opening, selecting or saving
        // must not overwrite the exact source in the database or version file.
        function getSourceOrEditedData() {
            const normalized = editor.getData();
            return normalized === normalizedBaseline ? sourceHtml : normalized;
        }
        editor.model.document.on("change:data", function () {
            if (!settingData && onChange) onChange(getSourceOrEditedData());
        });
        function loadSource(html) {
            sourceHtml = typeof html === "string" ? html : "";
            settingData = true;
            try {
                editor.setData(sourceHtml);
                normalizedBaseline = editor.getData();
            } finally {
                settingData = false;
            }
        }

        let readOnlyLock = false;
        if (config.readOnly) {
            editor.enableReadOnlyMode("opensigweave");
            readOnlyLock = true;
        }

        return {
            getData: getSourceOrEditedData,
            setOnChange: function (callback) { onChange = typeof callback === "function" ? callback : null; },
            setData: loadSource,
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
