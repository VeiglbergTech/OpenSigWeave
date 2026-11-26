"""Background monitor writes healthy status only after a real probe."""
import unittest
from unittest.mock import patch, Mock
from core.monitor_scheduler import check_once


class SchedulerTests(unittest.TestCase):
    def test_monitor_disabled_never_probes(self):
        row = Mock(monitoring_enabled=False)
        session = Mock()
        session.get.return_value = row
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=None)
        with patch("core.monitor_scheduler.probe") as p:
            check_once(lambda: session)
            p.assert_not_called()
        session.commit.assert_not_called()

    def test_monitor_enabled_persists_unknown(self):
        row = Mock(monitoring_enabled=True, last_monitored_at=None,
                   mail_server="rspamd", controller_url="http://10.0.0.2:11334")
        session = Mock()
        session.get.return_value = row
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=None)
        with patch("core.monitor_scheduler.probe",
                   return_value={"status": "unknown", "detail": "Connection refused"}):
            check_once(lambda: session)
        self.assertEqual(row.last_monitor_status, "unknown")
        session.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
