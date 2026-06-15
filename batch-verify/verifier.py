from __future__ import annotations

import csv
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

import requests
import yaml

from id_card import validate_id_card

logger = logging.getLogger(__name__)


@dataclass
class TaskItem:
    row_index: int
    data: dict[str, str]


@dataclass
class TaskResult:
    row_index: int
    real_name: str
    id_card: str
    success: bool
    status_code: int | None
    response_text: str
    error: str
    skipped: bool = False


class RateLimiter:
    def __init__(self, qps: float) -> None:
        self._interval = 1.0 / qps if qps > 0 else 0.0
        self._lock = Lock()
        self._last = 0.0

    def wait(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            if elapsed < self._interval:
                time.sleep(self._interval - elapsed)
            self._last = time.monotonic()


class BatchVerifier:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.api = config["api"]
        self.input_cfg = config["input"]
        self.output_cfg = config["output"]
        self.concurrency = config["concurrency"]
        self.validate_id_card = config.get("validation", {}).get(
            "validate_id_card", True
        )
        self.rate_limiter = RateLimiter(float(self.concurrency.get("qps", 0)))

    def _parse_txt_line(self, line: str) -> dict[str, str] | None:
        line = line.strip()
        if not line or line.startswith("#"):
            return None

        delimiter = self.input_cfg.get("delimiter", "auto")
        if delimiter == "auto":
            parts = [part for part in re.split(r"[\s,，\t]+", line) if part]
        elif delimiter == "comma":
            parts = [part.strip() for part in line.split(",") if part.strip()]
        elif delimiter == "tab":
            parts = [part.strip() for part in line.split("\t") if part.strip()]
        elif delimiter == "space":
            parts = [part for part in line.split() if part]
        else:
            parts = [part.strip() for part in line.split(delimiter) if part.strip()]

        if not parts:
            return None
        if len(parts) == 1:
            return {"realName": "", "idCard": parts[0]}
        return {"realName": " ".join(parts[:-1]), "idCard": parts[-1]}

    def load_tasks(self) -> list[TaskItem]:
        txt_path = Path(self.input_cfg["txt_path"])
        encoding = self.input_cfg.get("encoding", "utf-8")
        skip_header = bool(self.input_cfg.get("skip_header", False))

        tasks: list[TaskItem] = []
        with txt_path.open("r", encoding=encoding) as f:
            for idx, raw_line in enumerate(f, start=1):
                if skip_header and idx == 1:
                    continue
                data = self._parse_txt_line(raw_line)
                if data:
                    tasks.append(TaskItem(row_index=idx, data=data))
        return tasks

    def _build_payload(self, data: dict[str, str]) -> dict[str, str]:
        payload = dict(self.api.get("extra_payload") or {})
        for api_field, field_name in self.api["payload_fields"].items():
            payload[api_field] = data.get(field_name, "")
        return payload

    def _request_once(self, payload: dict[str, str]) -> tuple[int, str]:
        method = self.api.get("method", "POST").upper()
        url = self.api["url"]
        headers = self.api.get("headers") or {}
        timeout = float(self.api.get("timeout", 15))

        if url.startswith("https://your-internal-api.example.com"):
            raise ValueError("请先在 config.yaml 中配置真实的内部 api.url")

        response = requests.request(
            method=method,
            url=url,
            data=payload if method in {"POST", "PUT", "PATCH"} else None,
            params=payload if method == "GET" else None,
            headers=headers,
            timeout=timeout,
        )
        return response.status_code, response.text

    def _process_one(self, task: TaskItem) -> TaskResult:
        data = task.data
        real_name = data.get("realName", "")
        id_card = data.get("idCard", "")

        if self.validate_id_card:
            ok, reason = validate_id_card(id_card)
            if not ok:
                return TaskResult(
                    row_index=task.row_index,
                    real_name=real_name,
                    id_card=id_card,
                    success=False,
                    status_code=None,
                    response_text="",
                    error=f"本地校验失败: {reason}",
                    skipped=True,
                )

        payload = self._build_payload(data)
        max_retries = int(self.concurrency.get("max_retries", 0))
        retry_delay = float(self.concurrency.get("retry_delay", 1.0))

        last_error = ""
        for attempt in range(max_retries + 1):
            try:
                self.rate_limiter.wait()
                status_code, response_text = self._request_once(payload)
                success = 200 <= status_code < 300
                return TaskResult(
                    row_index=task.row_index,
                    real_name=real_name,
                    id_card=id_card,
                    success=success,
                    status_code=status_code,
                    response_text=response_text[:2000],
                    error="" if success else f"HTTP {status_code}",
                )
            except Exception as exc:
                last_error = str(exc)
                if attempt < max_retries:
                    time.sleep(retry_delay)

        return TaskResult(
            row_index=task.row_index,
            real_name=real_name,
            id_card=id_card,
            success=False,
            status_code=None,
            response_text="",
            error=last_error,
        )

    def run(self) -> Path:
        tasks = self.load_tasks()
        if not tasks:
            raise ValueError("TXT 中没有可处理的数据")

        workers = int(self.concurrency.get("workers", 4))
        results: list[TaskResult] = []

        logger.info("共 %s 条，线程数 %s", len(tasks), workers)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(self._process_one, task): task for task in tasks}
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                status = "跳过" if result.skipped else ("成功" if result.success else "失败")
                logger.info(
                    "第 %s 行 %s | %s | %s",
                    result.row_index,
                    status,
                    result.real_name,
                    result.error or result.status_code,
                )

        return self._write_results(sorted(results, key=lambda r: r.row_index))

    def _write_results(self, results: list[TaskResult]) -> Path:
        out_dir = Path(self.output_cfg.get("dir", "output"))
        out_dir.mkdir(parents=True, exist_ok=True)
        prefix = self.output_cfg.get("prefix", "result")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"{prefix}_{ts}.csv"

        with out_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "row_index",
                    "realName",
                    "idCard",
                    "success",
                    "skipped",
                    "status_code",
                    "error",
                    "response_text",
                ]
            )
            for item in results:
                writer.writerow(
                    [
                        item.row_index,
                        item.real_name,
                        item.id_card,
                        item.success,
                        item.skipped,
                        item.status_code if item.status_code is not None else "",
                        item.error,
                        item.response_text,
                    ]
                )

        summary = {
            "total": len(results),
            "success": sum(1 for r in results if r.success),
            "failed": sum(1 for r in results if not r.success and not r.skipped),
            "skipped": sum(1 for r in results if r.skipped),
            "output": str(out_path),
        }
        summary_path = out_dir / f"{prefix}_{ts}_summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("结果已写入 %s", out_path)
        logger.info("汇总: %s", summary)
        return out_path


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"未找到配置文件 {config_path}，请先复制 config.yaml.example 为 config.yaml"
        )
    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)
