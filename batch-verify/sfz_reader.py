"""从 sfz.txt 文件读取身份证号。"""

from pathlib import Path

from id_card import validate_id_card


def read_ids_from_sfz(
    file_path: str | Path = "sfz.txt",
    encoding: str = "utf-8",
    validate: bool = True,
    deduplicate: bool = True,
):
    """
    从 sfz.txt 逐行读取身份证号（生成器，可替代 generate_ids）。

    文件格式：每行一个身份证号，空行和以 # 开头的行会忽略。
    也支持「姓名 身份证号」格式，会自动取最后一个字段作为身份证号。

    用法（与原 generate_ids 替换）：
        for id_card in read_ids_from_sfz("sfz.txt"):
            print(id_card)
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"身份证文件不存在: {path.resolve()}")

    seen: set[str] = set()

    with path.open("r", encoding=encoding) as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            # 支持纯身份证号，或「姓名 身份证号」
            parts = line.split()
            id_card = parts[-1].strip().upper()

            if deduplicate and id_card in seen:
                continue

            if validate:
                ok, reason = validate_id_card(id_card)
                if not ok:
                    continue

            if deduplicate:
                seen.add(id_card)

            yield id_card


def load_ids_from_sfz(
    file_path: str | Path = "sfz.txt",
    encoding: str = "utf-8",
    validate: bool = True,
    deduplicate: bool = True,
) -> list[str]:
    """一次性读取全部身份证号并返回列表。"""
    return list(
        read_ids_from_sfz(
            file_path=file_path,
            encoding=encoding,
            validate=validate,
            deduplicate=deduplicate,
        )
    )
