"""语法校验与安全静态扫描。"""

from __future__ import annotations

from rpa_builder.code_validator import security_scan, validate_python


def test_valid_code():
    ok, err = validate_python('from rpa_tools import *\nprint("hi")\n')
    assert ok is True
    assert err == ""


def test_syntax_error_reports_line():
    ok, err = validate_python("def foo(:\n")
    assert ok is False
    assert "行" in err


def test_security_scan_flags_dangerous_operations():
    code = 'import os\nos.system("whoami")\nimport subprocess\nsubprocess.run(["x"])\nprint("ok")\n'
    findings = security_scan(code)
    messages = " | ".join(f.message for f in findings)
    assert any(f.severity == "error" for f in findings)
    assert "os.system" in messages
    assert "subprocess" in messages


def test_security_scan_clean_code():
    code = 'from rpa_tools import *\nimport time\nwrite_text_file("a.txt", "x")\ntime.sleep(1)\n'
    assert security_scan(code) == []


def test_security_scan_line_numbers():
    code = 'import os\n\nos.system("x")\n'
    findings = security_scan(code)
    assert findings and findings[0].line == 3
