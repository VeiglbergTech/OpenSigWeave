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

    // A single CKEditor View avoids LabeledFieldView's second floating label.
    // An explicit listbox replaces the browser datalist (which cannot be styled
    // or reliably scrolled across platforms).
    function signatureFontToolbarPlugin(editor) {
        const ui = window.CKEDITOR;

        // CKEditor can collapse its model selection when an ordinary text
        // input receives focus. Remember the last range observed while the
        // editable itself was focused, before the toolbar steals focus.
        let lastEditableRange = null;
        const rememberEditableRange = () => {
            if (editor.editing.view.document.isFocused) {
                lastEditableRange = editor.model.document.selection.getFirstRange();
            }
        };
        editor.model.document.selection.on("change:range", rememberEditableRange);
        editor.editing.view.document.on("change:isFocused", (event, name, isFocused) => {
            if (isFocused) rememberEditableRange();
        });

        function createInputControl(locale, { commandName, label, className, placeholder, formatValue, parseInput, options }) {
            const command = editor.commands.get(commandName);
            const hasOptions = Array.isArray(options);

            class FontInputView extends ui.View {
                constructor(locale) {
                    super(locale);
                    this.set("isEnabled", true);
                    this.setTemplate({
                        tag: "div",
                        attributes: { class: ["ck", "osw-ckeditor-font-input", className] },
                        children: [{
                            tag: "input",
                            attributes: {
                                type: "text",
                                // Plain input avoids CKEditor's duplicate floating label.
                                class: ["osw-font-text-field"],
                                "aria-label": label,
                                placeholder,
                                autocomplete: "off",
                                spellcheck: "false"
                            }
                        }]
                    });
                }

                render() {
                    super.render();
                    const input = this.element.querySelector("input");
                    if (commandName === "fontSize") input.setAttribute("inputmode", "decimal");

                    let selectedRange = null;
                    let activeIndex = -1;
                    let matches = [];
                    let listbox = null;

                    if (hasOptions) {
                        listbox = document.createElement("div");
                        listbox.className = "osw-font-listbox";
                        listbox.id = "osw-font-options-" + (++fontListSerial);
                        listbox.setAttribute("role", "listbox");
                        listbox.setAttribute("aria-label", label + " options");
                        listbox.hidden = true;
                        // Keep the search field focused while selecting items;
                        // only individual option pointerdowns are suppressed so
                        // the native scrollbar still supports pointer dragging.
                        // A body-level portal prevents the listbox from disabling
                        // the scrollable signature dialog or being clipped by it.
                        listbox.classList.add("osw-font-listbox-portal");
                        document.body.appendChild(listbox);
                        input.setAttribute("role", "combobox");
                        input.setAttribute("aria-controls", listbox.id);
                        input.setAttribute("aria-autocomplete", "list");
                        input.setAttribute("aria-expanded", "false");
                    }

                    const captureSelection = () => {
                        // Prefer the last selection made inside the editable.
                        // A range read after focus transitions may be collapsed
                        // and would only format subsequent typed text.
                        selectedRange = lastEditableRange ||
                            editor.model.document.selection.getFirstRange();
                    };
                    const positionMenu = () => {
                        if (!listbox || listbox.hidden) return;
                        const rect = input.getBoundingClientRect();
                        const availableBelow = window.innerHeight - rect.bottom - 12;
                        const openAbove = availableBelow < 150 && rect.top > availableBelow;
                        const maxHeight = Math.min(210, Math.max(64,
                            (openAbove ? rect.top : availableBelow) - 8));
                        const width = Math.min(Math.max(rect.width, 210), window.innerWidth - 16);
                        listbox.style.position = "fixed";
                        listbox.style.width = width + "px";
                        listbox.style.maxHeight = maxHeight + "px";
                        listbox.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)) + "px";
                        listbox.style.top = (openAbove ?
                            Math.max(8, rect.top - maxHeight - 4) : Math.min(window.innerHeight - 8, rect.bottom + 4)) + "px";
                    };
                    const closeMenu = () => {
                        if (!listbox) return;
                        listbox.hidden = true;
                        activeIndex = -1;
                        input.setAttribute("aria-expanded", "false");
                        input.removeAttribute("aria-activedescendant");
                    };
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
                            return false;
                        }
                        input.setCustomValidity("");
                        if (selectedRange) {
                            try { editor.model.change(writer => writer.setSelection(selectedRange)); }
                            catch (_) { selectedRange = null; }
                        }
                        if (!parsed) editor.execute(commandName);
                        else editor.execute(commandName, { value: parsed });
                        closeMenu();
                        if (focusEditor) editor.editing.view.focus();
                        update();
                        return true;
                    };
                    const syncActive = () => {
                        if (!listbox) return;
                        const choices = [...listbox.querySelectorAll('[role="option"]')];
                        choices.forEach((choice, index) => {
                            choice.classList.toggle("osw-font-option-active", index === activeIndex);
                            choice.setAttribute("aria-selected", String(index === activeIndex));
                        });
                        if (activeIndex >= 0 && choices[activeIndex]) {
                            input.setAttribute("aria-activedescendant", choices[activeIndex].id);
                            choices[activeIndex].scrollIntoView({ block: "nearest" });
                        } else {
                            input.removeAttribute("aria-activedescendant");
                        }
                    };
                    const renderOptions = (rawQuery = "") => {
                        if (!listbox) return;
                        const query = rawQuery.trim().toLowerCase();
                        matches = options.filter(name => name.toLowerCase().includes(query));
                        activeIndex = -1;
                        listbox.replaceChildren();
                        matches.forEach((name, index) => {
                            const choice = document.createElement("button");
                            choice.type = "button";
                            choice.className = "osw-font-option";
                            choice.id = listbox.id + "-option-" + index;
                            choice.setAttribute("role", "option");
                            choice.setAttribute("aria-selected", "false");
                            choice.textContent = name;
                            choice.addEventListener("pointerdown", event => event.preventDefault());
                            choice.addEventListener("click", () => {
                                input.value = name;
                                apply(true);
                            });
                            listbox.appendChild(choice);
                        });
                        if (!matches.length) {
                            const empty = document.createElement("div");
                            empty.className = "osw-font-empty";
                            empty.setAttribute("role", "status");
                            empty.textContent = "No matching fonts";
                            listbox.appendChild(empty);
                        }
                        syncActive();
                    };
                    const openMenu = (filterTypedValue = false) => {
                        if (!listbox || !this.isEnabled) return;
                        // A chosen family is not a search: show all fonts on focus.
                        renderOptions(filterTypedValue ? input.value : "");
                        listbox.hidden = false;
                        positionMenu();
                        input.setAttribute("aria-expanded", "true");
                    };

                    // Save the editor selection before a pointer changes focus.
                    // Focus can run after CKEditor has already collapsed that
                    // selection; do not overwrite the pre-blur range.
                    let selectionCapturedByPointer = false;
                    input.addEventListener("pointerdown", () => {
                        // Clicking an already-focused field will not trigger
                        // another focus event. Do not leave this flag set for
                        // a later keyboard focus transition.
                        selectionCapturedByPointer = document.activeElement !== input;
                        captureSelection();
                    });
                    input.addEventListener("focus", () => {
                        if (!selectionCapturedByPointer) captureSelection();
                        selectionCapturedByPointer = false;
                        openMenu();
                    });
                    // Escape keeps focus on the input. A second click must still
                    // reopen the list even though no new focus event is fired.
                    input.addEventListener("click", () => {
                        if (listbox && listbox.hidden) openMenu();
                    });
                    input.addEventListener("input", () => {
                        input.setCustomValidity("");
                        openMenu(true);
                    });
                    input.addEventListener("change", () => {
                        // Never validate a partially typed search when choosing an option.
                        if (!hasOptions || !input.value.trim() || parseInput(input.value.trim())) {
                            apply(false);
                        }
                    });
                    input.addEventListener("keydown", event => {
                        if (event.key === "Escape") {
                            event.preventDefault();
                            event.stopPropagation();
                            closeMenu();
                            input.value = formatValue(command.value);
                            input.setCustomValidity("");
                            return;
                        }
                        if (listbox && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
                            event.preventDefault();
                            event.stopPropagation();
                            if (listbox.hidden) openMenu();
                            if (matches.length) {
                                const delta = event.key === "ArrowDown" ? 1 : -1;
                                activeIndex = activeIndex < 0
                                    ? (delta > 0 ? 0 : matches.length - 1)
                                    : (activeIndex + delta + matches.length) % matches.length;
                                syncActive();
                            }
                            return;
                        }
                        if (event.key !== "Enter") return;
                        event.preventDefault();
                        event.stopPropagation();
                        if (listbox && !listbox.hidden && activeIndex >= 0) {
                            input.value = matches[activeIndex];
                        }
                        apply(true);
                    });
                    input.addEventListener("blur", event => {
                        // Moving focus to a list option should not discard the
                        // selection before its click handler executes.
                        if (listbox && event.relatedTarget && listbox.contains(event.relatedTarget)) return;
                        closeMenu();
                        update();
                    });
                    this.on("change:isEnabled", () => {
                        input.disabled = !this.isEnabled;
                        if (!this.isEnabled) closeMenu();
                    });
                    input.disabled = !this.isEnabled;
                    this.listenTo(command, "change:value", update);
                    const dismissOnViewportChange = event => {
                        // Scrolling inside the font list must not dismiss the list.
                        if (listbox && event?.type === "scroll" &&
                            (event.target === listbox || listbox.contains(event.target))) return;
                        closeMenu();
                    };
                    if (listbox) {
                        window.addEventListener("scroll", dismissOnViewportChange, true);
                        window.addEventListener("resize", dismissOnViewportChange);
                        this._oswListboxCleanup = () => {
                            window.removeEventListener("scroll", dismissOnViewportChange, true);
                            window.removeEventListener("resize", dismissOnViewportChange);
                            listbox.remove();
                        };
                    }
                    update();
                }

                destroy() {
                    if (this._oswListboxCleanup) this._oswListboxCleanup();
                    super.destroy();
                }
            }

            const view = new FontInputView(locale);
            view.bind("isEnabled").to(command, "isEnabled");
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
