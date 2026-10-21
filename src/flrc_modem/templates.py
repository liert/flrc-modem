"""模板路径兼容：旧 templates/<name> 唯一映射到分类目录，不改写已存在的文件。"""
from pathlib import Path


def resolve_template_path(path):
    path = Path(path)
    if path.exists() or path.parent.name != 'templates':
        return path
    matches = [path.parent / folder / path.name for folder in ('physical', 'rf', 'messages')
               if (path.parent / folder / path.name).is_file()]
    return matches[0] if len(matches) == 1 else path
