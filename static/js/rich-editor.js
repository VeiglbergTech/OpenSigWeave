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

    let fontListSerial = 0;

    function signatureFontToolbarPlugin(editor) {
        const ui = window.CKEDITOR;

        function createInputControl(locale, { commandName, label, className, placeholder, formatValue, parseInput, options }) {
            const command = editor.commands.get(commandName);
            // LabeledFieldView is a native CKEditor UI view, not a detached
            // toolbar. Its input remains accessible by keyboard and screen readers.
            class FontInputView extends ui.LabeledFieldView {
                render() {
                    super.render();
                    this.element.classList.add("osw-ckeditor-font-input", className);
                    const input = this.fieldView.element;
                    input.setAttribute("aria-label", label);
                    input.setAttribute("placeholder", placeholder);
                    input.setAttribute("autocomplete", "off");
                    input.setAttribute("spellcheck", "false");
                    if (commandName === "fontSize") input.setAttribute("inputmode", "decimal");
                    if (options) {
                        const list = document.createElement("datalist");
                        list.id = "osw-font-options-" + (++fontListSerial);
                        options.forEach(name => {
                            const option = document.createElement("option");
                            option.value = name;
                            list.appendChild(option);
                        });
                        input.setAttribute("list", list.id);
                        this.element.appendChild(list);
                    }
                    let selectedRange = null;
                    const captureSelection = () => {
                        selectedRange = editor.model.document.selection.getFirstRange();
                    };
                    input.addEventListener("pointerdown", captureSelection);
                    input.addEventListener("focus", captureSelection);
                    const update = () => {
                        if (document.activeElement !== input) {
                            input.value = formatValue(command.value);
                            input.setCustomValidity("");
                        }
                    };
                    const apply = focusEditor => {
                        const raw = input.value.trim();
                        const parsed = raw ? parseInput(raw) : "";
                        if (raw && !parsed) {
                            input.setCustomValidity("Enter a valid " + label.toLowerCase() + ".");
                            input.reportValidity();
                            return;
                        }
                        input.setCustomValidity("");
                        if (selectedRange) {
                            try { editor.model.change(writer => writer.setSelection(selectedRange)); }
                            catch (_) { selectedRange = null; }
                        }
                        if (!parsed) editor.execute(commandName);
                        else editor.execute(commandName, { value: parsed });
                        if (focusEditor) editor.editing.view.focus();
                        update();
                    };
                    input.addEventListener("input", () => input.setCustomValidity(""));
                    input.addEventListener("change", () => apply(false));
                    input.addEventListener("keydown", event => {
                        if (event.key === "Escape") { input.value = formatValue(command.value); input.setCustomValidity(""); return; }
                        if (event.key !== "Enter") return;
                        event.preventDefault();
                        event.stopPropagation();
                        apply(true);
                    });
                    this.listenTo(command, "change:value", update);
                    update();
                }
            }
            const view = new FontInputView(locale, ui.createLabeledInputText);
            view.set({ label });
            view.bind("isEnabled").to(command, "isEnabled");
            view.fieldView.bind("isEnabled").to(command, "isEnabled");
            return view;
        }

        editor.ui.componentFactory.add("signatureFontFamily", locale =>
            createInputControl(locale, {
                commandName: "fontFamily",
                label: "Font family",
                className: "osw-signature-family-input",
                placeholder: "Search font…",
                options: SIGNATURE_FONT_FAMILIES.map(([name]) => name),
                formatValue(value) {
                    const first = String(value || "").split(",")[0].trim().replace(/^(['"])(.*)\1$/, "$2");
                    return SIGNATURE_FONT_FAMILIES.find(([name]) => name.toLowerCase() === first.toLowerCase())?.[0] || first;
                },
                parseInput(raw) {
                    return SIGNATURE_FONT_FAMILIES.find(([name]) => name.toLowerCase() === raw.toLowerCase())?.[0] || null;
                }
            })
        );

        editor.ui.componentFactory.add("signatureFontSize", locale =>
            createInputControl(locale, {
                commandName: "fontSize",
                label: "Font size (pt)",
                className: "osw-signature-size-input",
                placeholder: "Size (pt)",
                formatValue(value) {
                    const label = fontSizeLabel(value);
                    return label === "Font size" ? "" : label.replace(/\s+(pt|px)$/i, "");
                },
                parseInput: parseFontSizeInput
            })
        );
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
            "undo", "redo", "|",
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
        let editor;
        try {
            editor = await library.ClassicEditor.create(element, editorConfig);
        } catch (customToolbarError) {
            // Never let the optional custom font dropdowns take the entire
            // signature editor offline. The native CKEditor font commands remain
            // available while the failing integration is diagnosed.
            console.error("OpenSigWeave: custom CKEditor toolbar failed; retrying with native font controls.", customToolbarError);
            const fallbackConfig = {
                ...editorConfig,
                toolbar: {
                    items: toolbar.map(item => item === "signatureFontFamily" ? "fontFamily"
                        : item === "signatureFontSize" ? "fontSize" : item),
                    shouldNotGroupWhenFull: true
                },
                extraPlugins: config.imageUpload ? [signatureImageUploadPlugin(config)] : []
            };
            // CKEditor can leave a partially constructed view after a plugin
            // initialization exception; reset the host before retrying.
            element.replaceChildren();
            editor = await library.ClassicEditor.create(element, fallbackConfig);
        }
        let imageImportSelection = null;
        if (config.imageUpload && typeof config.onImportImageFromUrl === "function") {
            try {
                installSignatureImageUrlButton(editor, () => {
                    // Retain the model range while the URL dialog is focused and the
                    // download is in progress; inserting must not jump to the end.
                    imageImportSelection = editor.model.document.selection.getFirstRange();
                    config.onImportImageFromUrl();
                });
            } catch (imageButtonError) {
                // A missing optional button must not discard the loaded editor.
                console.error("OpenSigWeave: image URL toolbar button could not be mounted.", imageButtonError);
            }
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
