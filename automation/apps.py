from django.apps import AppConfig


class AutomationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "automation"

    def ready(self) -> None:
        """Import the signal receivers so the trigger bindings are live.

        Imported for the side effect of registering receivers, which is why it is a bare
        `import automation.registry` with an explicit `# noqa: F401`: without it the module is never
        executed and no trigger fires, which looks exactly like a dispatcher bug.
        """
        import automation.registry  # noqa: F401
