from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Callable, Iterable

from tqdm import tqdm


@dataclass(frozen=True)
class ProgressState:
    title: str
    current: float
    total: float | None
    unit: str = ''
    finished: bool = False
    # Keep the legacy default for callers that construct ProgressState directly;
    # BatchProgressModel canonicalizes it to the current-course metric.
    kind: str = 'course'
    scope: str = 'default'


@dataclass(frozen=True)
class AggregateProgress:
    current: float
    total: float | None
    known_tasks: int
    expected_tasks: int
    finished_tasks: int
    scope_count: int


def canonical_progress_kind(kind: str) -> str:
    """Keep legacy video events compatible with the three GUI metrics."""
    normalized = str(kind or '').strip().lower()
    if normalized in {'course', 'video', 'current'}:
        return 'current_course'
    return normalized or 'current_course'


class BatchProgressModel:
    """Aggregate replaceable per-task progress scopes without Qt dependencies."""

    def __init__(self, task_ids: Iterable[str] = ()):
        self.reset(task_ids)

    def reset(self, task_ids: Iterable[str]) -> None:
        self.task_ids = tuple(dict.fromkeys(str(item) for item in task_ids))
        self._states: dict[tuple[str, str, str], ProgressState] = {}
        self._latest_current_course: tuple[str, ProgressState] | None = None

    def update(self, task_id: str, state: ProgressState) -> ProgressState:
        kind = canonical_progress_kind(state.kind)
        scope = str(state.scope or 'default')
        normalized = replace(state, kind=kind, scope=scope)
        task_id = str(task_id)
        self._states[(task_id, kind, scope)] = normalized
        if kind == 'current_course':
            self._latest_current_course = (task_id, normalized)
        return normalized

    def aggregate(self, kind: str) -> AggregateProgress:
        kind = canonical_progress_kind(kind)
        states_by_task: dict[str, list[ProgressState]] = {}
        for (task_id, state_kind, _scope), state in self._states.items():
            if state_kind == kind:
                states_by_task.setdefault(task_id, []).append(state)

        known_tasks: set[str] = set()
        finished_tasks = 0
        current = 0.0
        total = 0.0
        known_scope_count = 0
        for task_id, states in states_by_task.items():
            known_states = [
                state for state in states
                if state.total is not None and state.total >= 0
            ]
            if not known_states:
                continue
            known_tasks.add(task_id)
            known_scope_count += len(known_states)
            if all(state.finished for state in known_states):
                finished_tasks += 1
            for state in known_states:
                scope_total = float(state.total or 0)
                scope_current = max(0.0, float(state.current or 0))
                current += min(scope_current, scope_total) if scope_total else 0.0
                total += scope_total

        return AggregateProgress(
            current=current,
            total=total if known_tasks else None,
            known_tasks=len(known_tasks),
            expected_tasks=len(self.task_ids),
            finished_tasks=finished_tasks,
            scope_count=known_scope_count,
        )

    def latest_current_course(self) -> tuple[str, ProgressState] | None:
        return self._latest_current_course

    def submodel(self, task_ids: Iterable[str]) -> 'BatchProgressModel':
        """Build an isolated view for one account without duplicating updates."""
        selected = tuple(dict.fromkeys(str(item) for item in task_ids))
        selected_set = set(selected)
        model = BatchProgressModel(selected)
        model._states = {
            key: state
            for key, state in self._states.items()
            if key[0] in selected_set
        }
        latest = self._latest_current_course
        if latest is not None and latest[0] in selected_set:
            model._latest_current_course = latest
        return model


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
    kind: str = 'course',
    scope: str = 'default',
) -> None:
    sink = _progress_sink.get()
    if sink:
        sink(ProgressState(title, current, total, unit, finished, kind, scope))


class ProgressTqdm(tqdm):
    """tqdm wrapper that also emits GUI-friendly progress events."""

    def __init__(
        self,
        *args,
        title: str | None = None,
        unit_label: str = '',
        progress_scope: str = 'active',
        **kwargs,
    ):
        self.progress_title = title or kwargs.get('desc') or 'Progress'
        self.progress_unit_label = unit_label or kwargs.get('unit') or ''
        self.progress_scope = progress_scope
        self._progress_closed = False
        super().__init__(*args, **kwargs)
        self._emit_state()

    def refresh(self, *args, **kwargs):
        result = super().refresh(*args, **kwargs)
        self._emit_state()
        return result

    def close(self):
        if self._progress_closed:
            return
        self._progress_closed = True
        super().close()
        self._emit_state(finished=True)

    def _emit_state(self, finished: bool = False) -> None:
        emit_progress(
            self.progress_title,
            float(getattr(self, 'n', 0) or 0),
            getattr(self, 'total', None),
            self.progress_unit_label,
            finished,
            'current_course',
            self.progress_scope,
        )
