from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable

from tqdm import tqdm


@dataclass(frozen=True)
class ProgressState:
    title: str
    current: float
    total: float | None
    unit: str = ''
    finished: bool = False


ProgressSink = Callable[[ProgressState], None]

_progress_sink: ContextVar[ProgressSink | None] = ContextVar(
    'progress_sink',
    default=None,
)


def set_progress_sink(sink: ProgressSink | None):
    return _progress_sink.set(sink)


def reset_progress_sink(token) -> None:
    _progress_sink.reset(token)


def emit_progress(
    title: str,
    current: float = 0,
    total: float | None = None,
    unit: str = '',
    finished: bool = False,
) -> None:
    sink = _progress_sink.get()
    if sink:
        sink(ProgressState(title, current, total, unit, finished))


class ProgressTqdm(tqdm):
    """tqdm wrapper that also emits GUI-friendly progress events."""

    def __init__(self, *args, title: str | None = None, unit_label: str = '', **kwargs):
        self.progress_title = title or kwargs.get('desc') or 'Progress'
        self.progress_unit_label = unit_label or kwargs.get('unit') or ''
        super().__init__(*args, **kwargs)
        self._emit_state()

    def refresh(self, *args, **kwargs):
        result = super().refresh(*args, **kwargs)
        self._emit_state()
        return result

    def close(self):
        super().close()
        self._emit_state(finished=True)

    def _emit_state(self, finished: bool = False) -> None:
        emit_progress(
            self.progress_title,
            float(getattr(self, 'n', 0) or 0),
            getattr(self, 'total', None),
            self.progress_unit_label,
            finished,
        )
