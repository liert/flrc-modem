"""协议模板 JSON/JSONL 输入，与原始 payload_hex 输入分开校验。"""
import json


def read_message(path):
    # 模板文件最大 64KiB，避免误读 IQ 文件。
    with path.open("r", encoding="utf-8-sig") as stream:
        text = stream.read(65537)
    if len(text) > 65536:
        raise ValueError("业务模板文件超过 64Ki 字符")
    message = json.loads(text)
    if not isinstance(message, dict):
        raise ValueError("业务模板必须为 JSON 对象")
    return message


def iter_message_records(stream, *, sync_word=None, repeat=1):
    number = 0
    while line := stream.readline(65537):
        number += 1
        try:
            if len(line) > 65536:
                raise ValueError("输入行超过 64Ki 字符")
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or set(row) - {"message", "sync", "repeat"}:
                raise ValueError("协议 JSONL 仅支持 message、sync、repeat")
            if not isinstance(row.get("message"), dict):
                raise ValueError("缺少 message 对象")
            sync = sync_word
            if "sync" in row:
                if not isinstance(row["sync"], str):
                    raise ValueError("sync 必须为十六进制字符串")
                sync = bytes.fromhex(row["sync"])
            if sync is None or len(sync) != 4:
                raise ValueError("必须指定 4B 同步字")
            count = row.get("repeat", repeat)
            if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                raise ValueError("JSONL repeat 必须为正整数")
            yield row["message"], sync, count
        except (ValueError, TypeError) as error:
            raise ValueError(f"第 {number} 行：{error}") from error
