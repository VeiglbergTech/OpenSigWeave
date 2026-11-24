#!/usr/bin/env python3
"""Read-only audit of Postfix's effective authenticated-submission Milter fallback.

This is a configuration gate, NOT proof that Rspamd successfully loads the
OpenSigWeave Lua plugin or that an SMTP 4xx reaches the sending client.
"""
from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys

# Besides the named 587/465 services, Mailcow uses an authenticated SOGo
# sender listener on 588 and HAProxy-facing listeners on 10587/10465.
# Do NOT include 590 (quarantine), 591 (BCC) or 589 (watchdog): those
# deliberately bypass Milters and are not ordinary user submission.
SUBMISSION_SERVICES = ("submission", "submissions", "smtps", "588", "10587", "10465")
VARIABLE = re.compile(r"^\$(?:\{([A-Za-z_][A-Za-z_0-9]*)\}|([A-Za-z_][A-Za-z_0-9]*))$")


def parse_settings(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def parse_services(output: str) -> set[str]:
    """Parse both genuine 'postconf -M' table output and its -F key=value form.

    Postfix normally prints "submission inet ... smtpd" (no '='), unlike
    'postconf -n'/'-P'. Never treat missing parsed listeners as evidence that
    Mailcow has no active submission service.
    """
    services: set[str] = set()
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key = line.split("=", 1)[0].strip()
            if re.fullmatch(r"[^/\\s=]+/(?:inet|pass|unix|fifo)", key):
                services.add(key)
            continue
        fields = line.split()
        if len(fields) >= 3 and fields[1] in {"inet", "pass", "unix", "fifo"}:
            services.add(f"{fields[0]}/{fields[1]}")
    return services


def effective_setting(main: dict[str, str], overrides: dict[str, str],
                      service: str, name: str) -> str | None:
    key = f"{service}/inet/{name}"
    value = overrides.get(key, main.get(name, ""))
    # A service override like -o smtpd_milters=$smtpd_milters inherits main.cf.
    # Reject other unevaluated Postfix expressions rather than guessing.
    if "$" in value:
        matched = VARIABLE.fullmatch(value)
        if not matched or (matched.group(1) or matched.group(2)) != name:
            return None
        value = main.get(name, "")
        if "$" in value:
            return None
    return value.strip()


def audit(main_text: str, services_text: str, overrides_text: str,
          milter_hint: str = "rspamd") -> list[tuple[str, str]]:
    main = parse_settings(main_text)
    overrides = parse_settings(overrides_text)
    services = parse_services(services_text)
    results: list[tuple[str, str]] = []
    found = [service for service in SUBMISSION_SERVICES if f"{service}/inet" in services]
    if not found:
        results.append(("FAIL",
                        "No recognized authenticated submission listener found "
                        "(submission/submissions/smtps, Mailcow 588/10587/10465). "
                        "Inspect postconf -M and active SMTP entrypoints."))
    for service in found:
        milters = effective_setting(main, overrides, service, "smtpd_milters")
        failure_action = effective_setting(main, overrides, service, "milter_default_action")
        if not milters:
            results.append(("FAIL", f"{service}: smtpd_milters missing or cannot be resolved"))
        elif milter_hint.casefold() not in milters.casefold():
            results.append(("FAIL", f"{service}: expected Rspamd Milter endpoint not found"))
        else:
            results.append(("PASS", f"{service}: expected Milter endpoint referenced"))
        if failure_action != "tempfail":
            results.append(("FAIL", f"{service}: milter_default_action must be tempfail (effective: {failure_action or 'unset/unknown'})"))
        else:
            results.append(("PASS", f"{service}: Milter outage yields tempfail"))
    if "smtp/inet" in services:
        inbound_action = effective_setting(main, overrides, "smtp", "milter_default_action")
        if inbound_action == "tempfail":
            results.append(("WARN", "Port-25 smtp/inet also uses tempfail; verify this is an intentional inbound policy"))
    results.append(("INFO", "This does not test plugin loading, live SMTP DATA rejection, sender AUTH, DKIM or retry"))
    return results


def read_postconf(command: list[str], flag: str) -> str:
    try:
        result = subprocess.run(
            [*command, flag], check=False, capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"postconf {flag} could not run ({type(exc).__name__})") from None
    if result.returncode != 0:
        # stderr may contain sensitive paths/configuration; never reproduce it.
        raise RuntimeError(f"postconf {flag} exited with status {result.returncode}")
    return result.stdout


def main_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postconf-command", default="postconf",
                        help='e.g. "docker compose exec -T postfix-mailcow postconf"')
    parser.add_argument("--expected-milter", default="rspamd",
                        help="Case-insensitive substring of configured Rspamd Milter endpoint")
    args = parser.parse_args(argv)
    try:
        command = shlex.split(args.postconf_command)
        if not command or not args.expected_milter.strip():
            parser.error("postconf command and expected Milter must be nonempty")
        main_text = read_postconf(command, "-n")
        services_text = read_postconf(command, "-M")
        overrides_text = read_postconf(command, "-P")
    except RuntimeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    findings = audit(main_text, services_text, overrides_text, args.expected_milter)
    for level, message in findings:
        print(f"[{level}] {message}")
    return 1 if any(level == "FAIL" for level, _ in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
