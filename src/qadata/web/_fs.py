"""M8 票 01：web 存储面唯一写入口——原子写（文件即数据库的耐久性补丁）。

三处落盘（会话档 yaml、meta.yaml、上传 source.sqlite）全部收口本函数：
同目录写 `.tmp` 伴侣文件 → flush+fsync 落物理盘 → `os.replace` 原子覆盖目标
（Windows/Linux 同卷 rename 覆盖均原子，Python≥3.3 保证）。crash 在写一半
最坏留一具 .tmp 尸体，**目标文件永不出半档**——上传库覆盖坏一次＝整库损坏且
用户以为换库成功，是缩法裁决（不做 repo 仪式、不上 Redis）之外仅存的真故障面。
失败路径清 tmp：不留孤儿文件（下次写同名 tmp 也会被覆盖，清只是卫生）。
"""
from __future__ import annotations

import os
from pathlib import Path

_TMP_SUFFIX = ".tmp"


def atomic_write(path: Path, data: str | bytes) -> None:
    """str 按 utf-8 写、bytes 原样写；父目录必须已存在（调用方各持存在性检查）。"""
    tmp = path.with_name(path.name + _TMP_SUFFIX)
    binary = isinstance(data, bytes)
    try:
        with open(tmp, "wb" if binary else "w",
                  encoding=None if binary else "utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
