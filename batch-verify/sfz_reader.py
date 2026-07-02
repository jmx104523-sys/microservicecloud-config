"""从 sfz.txt 文件读取身份证号（单文件版，无外部依赖）。"""

import re
from datetime import datetime
from pathlib import Path
from typing import List, Union

# 身份证号校验
_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_CHECK_CODES = "10X98765432"
_ID_PATTERN = re.compile(
    r"^[1-9]\d{5}"
    r"(18|19|20)\d{2}"
    r"(0[1-9]|1[0-2])"
    r"(0[1-9]|[12]\d|3[01])"
    r"\d{3}"
    r"[\dXx]$"
)


def validate_id_card(id_card: str):
    value = (id_card or "").strip().upper()
    if not value:
        return False, "身份证号为空"
    if not _ID_PATTERN.match(value):
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


def read_ids_from_sfz(
    file_path: Union[str, Path] = "sfz.txt",
    encoding: str = "utf-8",
    validate: bool = True,
    deduplicate: bool = True,
):
    """
    从 sfz.txt 逐行读取身份证号（生成器）。

    用法：
        for id_card in read_ids_from_sfz("sfz.txt"):
            print(id_card)
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError("身份证文件不存在: {}".format(path.resolve()))

    seen = set()

    with path.open("r", encoding=encoding) as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split()
            id_card = parts[-1].strip().upper()

            if deduplicate and id_card in seen:
                continue

            if validate:
                ok, _reason = validate_id_card(id_card)
                if not ok:
                    continue

            if deduplicate:
                seen.add(id_card)

            yield id_card


def load_ids_from_sfz(
    file_path: Union[str, Path] = "sfz.txt",
    encoding: str = "utf-8",
    validate: bool = True,
    deduplicate: bool = True,
) -> List[str]:
    """一次性读取全部身份证号并返回列表。"""
    return list(
        read_ids_from_sfz(
            file_path=file_path,
            encoding=encoding,
            validate=validate,
            deduplicate=deduplicate,
        )
    )
