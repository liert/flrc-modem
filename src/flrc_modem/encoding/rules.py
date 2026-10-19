"""有限的字段变化规则：不执行表达式，通过点路径定位对象/数组元素。"""
from math import floor, isfinite


def _integer(value, name):
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} 必须为整数")
    return value


def _value(rule, context):
    if not isinstance(rule, dict):
        raise ValueError("字段更新规则必须为对象")
    kind = rule.get("type")
    allowed = {"counter": {"type", "start", "step", "modulo"},
               "cycle": {"type", "values"}, "clock": {"type", "rate", "start", "modulo"}}
    if not isinstance(kind, str) or kind not in allowed or set(rule) - allowed[kind]:
        raise ValueError("更新规则类型或参数错误；支持 counter/cycle/clock")
    if kind == "cycle":
        values = rule.get("values")
        if not isinstance(values, list) or not values:
            raise ValueError("cycle.values 必须为非空数组")
        return values[context.frame_index % len(values)]
    start = _integer(rule.get("start", 0), "start")
    if kind == "counter":
        value = start + context.frame_index * _integer(rule.get("step", 1), "step")
    else:
        rate = rule.get("rate", 10000)
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not isfinite(rate) or rate <= 0:
            raise ValueError("clock.rate 必须为有限正数")
        value = start + floor(context.start_sample * rate / context.sample_rate)
    if "modulo" in rule:
        modulo = _integer(rule["modulo"], "modulo")
        if modulo <= 0:
            raise ValueError("modulo 必须大于 0")
        value %= modulo
    return value


def apply_updates(message, updates, context):
    if not isinstance(updates, dict):
        raise ValueError("updates 必须为路径到规则的对象")
    for path, rule in updates.items():
        if not isinstance(path, str) or not path or any(not part for part in path.split(".")):
            raise ValueError("更新路径必须为非空点路径")
        parts, target = path.split("."), message
        # 只允许更新已存在的字段，错误拼写不能悄悄增加一个无效字段。
        try:
            for part in parts[:-1]:
                target = target[int(part)] if isinstance(target, list) and part.isdecimal() else target[part]
            key = int(parts[-1]) if isinstance(target, list) and parts[-1].isdecimal() else parts[-1]
            target[key]
            target[key] = _value(rule, context)
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError(f"更新路径不存在：{path}") from error
