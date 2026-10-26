"""Export a pinned selection without disk I/O on the RX thread or a whole-file copy."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from ..artifacts.manager import get_artifact_manager
from .history import IQHistory
from .schemas import SpectrumSelectionRequest


def export_selection(history: IQHistory, request: SpectrumSelectionRequest, *, session_id: str,
                     iq_format: str, device_type: str, root: Path):
    snapshot = history.snapshot(request.time_start, request.time_end,
                                sample_rate=request.sample_rate, center_freq=request.center_frequency)
    directory = get_artifact_manager().validate_safe_path(root / 'outputs' / 'captures')
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'selection_{uuid.uuid4().hex}.{iq_format}'
    temporary = path.with_suffix(path.suffix + '.part')
    companion = Path(str(path) + '.json')
    companion_temporary = Path(str(companion) + '.part')
    result = {
        'session_id': session_id, 'filepath': path.relative_to(root).as_posix(),
        'iq_format': iq_format, 'sample_rate': snapshot.sample_rate,
        'center_frequency': snapshot.center_freq, 'target_frequency': request.target_frequency,
        'time_start': snapshot.time_start, 'time_end': snapshot.time_end,
        'duration_s': snapshot.samples_count / snapshot.sample_rate,
        'samples_count': snapshot.samples_count, 'bytes': snapshot.samples_count * snapshot.sample_bytes,
    }
    try:
        with temporary.open('xb') as stream:
            for part in snapshot.parts:
                stream.write(part)
        companion_temporary.write_text(json.dumps({
            **result, 'source': 'spectrum_selection', 'device': device_type,
            'requested_time_start': request.time_start, 'requested_time_end': request.time_end,
            'created_at': datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        companion_temporary.replace(companion)
        temporary.replace(path)
    except BaseException:
        for candidate in (temporary, companion_temporary, path, companion):
            candidate.unlink(missing_ok=True)
        raise
    return result
