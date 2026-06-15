"""中国大陆 18 位身份证号格式校验（仅本地校验，不调用外部接口）。"""

import re
from datetime import datetime

_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_CHECK_CODES = "10X98765432"
_PATTERN = re.compile(
    r"^[1-9]\d{5}"
    r"(18|19|20)\d{2}"
    r"(0[1-9]|1[0-2])"
    r"(0[1-9]|[12]\d|3[01])"
    r"\d{3}"
    r"[\dXx]$"
)


def validate_id_card(id_card: str) -> tuple[bool, str]:
    value = (id_card or "").strip().upper()
    if not value:
        return False, "身份证号为空"
    if not _PATTERN.match(value):
        return False, "身份证号格式不正确"

    try:
        datetime.strptime(value[6:14], "%Y%m%d")
    except ValueError:
        return False, "身份证号出生日期无效"

    total = sum(int(value[i]) * _WEIGHTS[i] for i in range(17))
    expected = _CHECK_CODES[total % 11]
    if value[-1] != expected:
        return False, "身份证号校验位错误"

    return True, ""
