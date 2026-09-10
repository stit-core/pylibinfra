"""ClockProvider — Clock как Real в dishka-контейнере."""

from dishka import Provider, Scope, provide

from .clock import Clock, Real


class ClockProvider(Provider):
    """Provide'ит `Clock` = `Real`; фейки подставляются в тестах напрямую."""

    scope = Scope.APP

    clock = provide(Real, provides=Clock)
