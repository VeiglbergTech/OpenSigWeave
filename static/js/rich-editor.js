(function () {
    "use strict";

    const allowedPlugins = [
        "Essentials", "Paragraph", "Heading", "Bold", "Italic", "Underline",
        "Font", "Link", "List", "Alignment", "Table", "TableToolbar",
        "GeneralHtmlSupport", "PasteFromOffice", "BlockQuote", "HorizontalLine"
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

    async function create(element, options) {
        const config = options || {};
        const library = window.CKEDITOR;
        if (!library || !library.ClassicEditor) {
            throw new Error("CKEditor 5 failed to load.");
        }

        const plugins = allowedPlugins.map(name => library[name]).filter(Boolean);
        const editor = await library.ClassicEditor.create(element, {
            licenseKey: "GPL",
            plugins,
            toolbar: [
                "undo", "redo", "|", "heading", "|",
                "fontFamily", "fontSize", "fontColor", "fontBackgroundColor", "|",
                "bold", "italic", "underline", "link", "|",
                "bulletedList", "numberedList", "|", "alignment", "insertTable", "|",
                "blockQuote", "horizontalLine"
            ],
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
            initialData: typeof config.initialData === "string" ? config.initialData : "",
            placeholder: config.placeholder || "Create your email signature…"
        });

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
            setData: function (html) { editor.setData(typeof html === "string" ? html : ""); },
            focus: function () { editor.editing.view.focus(); },
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
