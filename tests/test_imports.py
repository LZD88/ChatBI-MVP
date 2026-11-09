from importlib import import_module


def test_application_packages_are_importable() -> None:
    modules = [
        "chatbi",
        "chatbi.api",
        "chatbi.application",
        "chatbi.domain",
        "chatbi.infrastructure",
        "chatbi.web",
    ]

    for module in modules:
        assert import_module(module) is not None
