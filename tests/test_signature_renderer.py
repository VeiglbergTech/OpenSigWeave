import unittest

from jinja2 import TemplateError

from signature_renderer import render_signature_template


class SignatureRendererTests(unittest.TestCase):
    def test_dynamic_html_is_escaped(self):
        rendered = render_signature_template(
            "<p>{{ title }}</p>",
            {"title": '<img src=x onerror="alert(1)">'},
        )
        self.assertEqual(
            rendered,
            "<p>&lt;img src=x onerror=&#34;alert(1)&#34;&gt;</p>",
        )

    def test_safe_filter_cannot_disable_autoescaping(self):
        with self.assertRaises(TemplateError):
            render_signature_template("{{ title|safe }}", {"title": "<script>"})

    def test_private_object_attributes_are_not_exposed(self):
        class Profile:
            def __init__(self):
                self.name = "Alice"
                self._secret = "not for templates"

        rendered = render_signature_template(
            "{{ profile.name }}|{{ profile._secret }}",
            {"profile": Profile()},
        )
        self.assertEqual(rendered, "Alice|")


if __name__ == "__main__":
    unittest.main()
