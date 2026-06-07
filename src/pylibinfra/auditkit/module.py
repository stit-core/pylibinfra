from dishka import Provider, Scope, provide

from .audit import Recorder, Store, StoreRecorder


# Provider не provide'ит свой Store — его регистрирует backend-Provider
# (AuditPgstoreProvider). Core лишь поднимает Recorder поверх любого Store в DI.
class AuditProvider(Provider):
    scope = Scope.APP

    @provide(provides=Recorder)
    def recorder(self, store: Store) -> StoreRecorder:
        return StoreRecorder(store)
