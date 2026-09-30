"""Lazy package re-exports (PEP 562), shared by scripts/, routers/ and services/."""

from importlib import import_module


def exports_by_module(mapping: dict[str, list[str]]) -> dict[str, tuple[str, str]]:
    """{module: [names]} -> {public_name: (module, attribute)}."""
    return {name: (module, name) for module, names in mapping.items() for name in names}


def install(namespace: dict, symbols: dict[str, tuple[str, str]]) -> None:
    """Give a package `__all__` and a module-level `__getattr__` resolving `symbols` on demand."""
    package = namespace["__name__"]

    def __getattr__(name):
        target = symbols.get(name)
        if target is None:
            raise AttributeError(f"module {package!r} has no attribute {name!r}")
        module, attribute = target
        value = getattr(import_module(module), attribute)
        namespace[name] = value  # cache: resolved once per name
        return value

    namespace["__all__"] = sorted(symbols)
    namespace["__getattr__"] = __getattr__
