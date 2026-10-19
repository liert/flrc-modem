"""标准输入 JSONL 报文；与硬件和调制调度分离，便于上层复用。"""
import json


def iter_json_records(stream, *, sync_word=None, repeat=1):
    line_number = 0
    while True:
        line = stream.readline(4097)
        if not line:
            return
        line_number += 1
        try:
            if len(line) > 4096:
                raise ValueError("单行 JSON 不能超过 4096 字符")
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict) or set(record) - {"payload_hex", "sync", "repeat"}:
                raise ValueError("报文必须是仅含 payload_hex、sync、repeat 的 JSON 对象")
            if not isinstance(record.get("payload_hex"), str):
                raise ValueError("payload_hex 必须为十六进制字符串")
            payload = bytes.fromhex(record["payload_hex"])
            sync = bytes.fromhex(record["sync"]) if "sync" in record else sync_word
            count = record.get("repeat", repeat)
            if not 6 <= len(payload) <= 127:
                raise ValueError("物理载荷必须为 6..127B")
            if sync is None or len(sync) != 4:
                raise ValueError("必须指定 4B 同步字，可在命令行或每条报文中提供")
            if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                raise ValueError("每条报文的 repeat 必须为正整数")
            yield payload, sync, count
        except (ValueError, TypeError) as error:
            raise ValueError(f"标准输入第 {line_number} 行：{error}") from error
