"""Regressions for group and backup rule table structure and responsive styles."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/portal.css").read_text(encoding="utf-8")


class AdminTableLayoutTests(unittest.TestCase):
    def test_group_table_keeps_name_header_and_full_title(self):
        self.assertIn("<th>Name</th><th>Signatures and rotation</th>", HTML)
        self.assertIn('class="osw-group-title font-medium" x-text="group.name" :title="group.name"', HTML)
        self.assertIn("table.osw-groups-table > thead th:first-child", CSS)
        self.assertIn("white-space: nowrap;\n    overflow-wrap: normal;", CSS)
        self.assertIn("max-width: clamp(8ch, 14cqi, 14rem)", CSS)
        self.assertIn("container-name: osw-groups;", CSS)

    def test_group_member_order_and_actions_are_preserved(self):
        group = HTML.split('<div class="osw-group-member">', 1)[1].split("</div>", 1)[0]
        self.assertLess(group.index("osw-group-member-name"), group.index("osw-group-member-weight"))
        self.assertLess(group.index("osw-group-member-weight"), group.index("osw-group-member-enabled"))
        self.assertLess(group.index("osw-group-member-enabled"), group.index("osw-group-member-remove"))
        for token in ("saveMember(group, member)", "removeMember(group, member)",
                      "member.weight", "member.is_enabled", 'aria-label="Remove"'):
            self.assertIn(token, group)
        self.assertIn("margin-inline-start: auto;", CSS)
        self.assertNotIn("Domain scope", HTML.split('adminTab === \'signature-groups\'', 1)[1].split('adminTab === \'rules\'', 1)[0])

    def test_backup_actions_are_nonwrapping_and_remain_accessible(self):
        self.assertIn("osw-backup-rules-table", HTML)
        self.assertIn("osw-backup-table-container", HTML)
        self.assertNotIn('min-w-[60rem] text-left text-sm osw-data-table', HTML)
        actions = HTML.split('class="osw-backup-rule-actions"', 1)[1].split("</div>", 1)[0]
        for label in ("Run Backup", "Edit", "Delete"):
            self.assertIn(">" + label + "</button>", actions)
        self.assertLess(actions.index("Run Backup"), actions.index("Edit"))
        self.assertLess(actions.index("Edit"), actions.index("Delete"))
        self.assertIn("flex-wrap: nowrap;", CSS.split("/* Backup Rules: actions", 1)[1])
        self.assertIn("container-name: osw-backup-rules;", CSS)
        self.assertIn("@container osw-backup-rules (max-width: 70rem)", CSS)
        self.assertIn("@container osw-backup-rules (max-width: 33rem)", CSS)


if __name__ == "__main__":
    unittest.main()
