from dishka import Provider, Scope, provide

from .clock import Clock, Real


class ClockProvider(Provider):
    scope = Scope.APP

    clock = provide(Real, provides=Clock)
