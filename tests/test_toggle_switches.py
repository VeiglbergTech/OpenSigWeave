"""Regression tests for accessible shared toggle switches throughout the portal."""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/portal.css").read_text(encoding="utf-8")
INPUTS = re.findall(r'<input\\b[^>]*\\btype="checkbox"[^>]*>', HTML)
MULTI = ('x-model="selected"', ':checked="form.domain_ids.includes(')


class SharedToggleSwitchTests(unittest.TestCase):
    def test_all_checkbox_inputs_share_one_switch_style(self):
        self.assertEqual(len(INPUTS), 21)
        for field in INPUTS:
            with self.subTest(field=field):
                self.assertRegex(field, r'class="[^"]*\\bosw-switch\\b')
                self.assertNotIn("sr-only", field)

    def test_accessibility_roles_distinguish_binary_and_multi_selection(self):
        binary = multi = 0
        for field in INPUTS:
            if any(marker in field for marker in MULTI):
                multi += 1
                self.assertIn("osw-switch--multi", field)
                self.assertNotIn('role="switch"', field)
            else:
                binary += 1
                self.assertIn('role="switch"', field)
                self.assertNotIn("osw-switch--multi", field)
        self.assertEqual((binary, multi), (14, 7))

    def test_legacy_tracks_are_removed_but_bindings_stay(self):
        self.assertNotIn('class="dot absolute', HTML)
        for binding in ('x-model="form.is_active"', 'x-model="form.is_enabled"',
                        'x-model="member.is_enabled"', 'x-model="locationForm.enabled"',
                        'x-model="ruleForm.enabled"', 'x-model="settings.automatic"',
                        'x-model="allowOverrides"', 'x-model="injectOnReplies"',
                        'x-model="trimWhitespace"', 'x-model="stripDeviceSignatures"',
                        'x-model="editingUserDisabled"', 'x-model="selected"',
                        '@change="saveMember(group, member)"',
                        '@change="toggleDomain(domain.id, $event.target.checked)"'):
            with self.subTest(binding=binding):
                self.assertIn(binding, HTML)

    def test_shared_styles_cover_states_and_accessibility(self):
        for selector in ('input[type="checkbox"].osw-switch',
                         'input[type="checkbox"].osw-switch::before',
                         'input[type="checkbox"].osw-switch:checked',
                         'input[type="checkbox"].osw-switch:disabled',
                         'input[type="checkbox"].osw-switch:focus-visible',
                         '@media (prefers-reduced-motion: reduce)',
                         '@media (forced-colors: active)',
                         'var(--osw-panel-raised)', 'var(--osw-action-accent)'):
            with self.subTest(selector=selector):
                self.assertIn(selector, CSS)


if __name__ == "__main__":
    unittest.main()
