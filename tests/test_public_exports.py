"""Package-level re-exports (`from scripts import bm25`) resolve and stay lazy where needed."""

import os
import pathlib
import subprocess
import sys

import pytest

import database
import models
import routers
import scripts

_BACKEND = pathlib.Path(__file__).resolve().parent.parent / "backend"


@pytest.mark.parametrize("package", [models, database, routers, scripts])
def test_every_name_in_all_resolves(package):
    for name in package.__all__:
        assert getattr(package, name) is not None, f"{package.__name__}.{name}"


def test_models_hadith_is_orm_and_schema_is_pydantic():
    from pydantic import BaseModel

    from models import orm, schemas

    assert models.Hadith is orm.Hadith
    assert models.HadithSchema is schemas.Hadith
    assert issubclass(models.HadithSchema, BaseModel)


def test_exports_are_the_same_objects_as_submodules():
    from scripts import search

    assert scripts.rrf_fusion is search.rrf_fusion
    assert database.Hadith is models.Hadith


def test_unknown_names_raise_attribute_error():
    with pytest.raises(AttributeError):
        scripts.not_a_real_export
    with pytest.raises(AttributeError):
        routers.not_a_router


def test_submodule_imports_still_work():
    from scripts import data_creation  # noqa: F401  (not a re-export; falls back to the module)


@pytest.mark.skipif(
    "MUTANT_UNDER_TEST" in os.environ,
    reason="subprocess does not see the mutated copy under mutmut",
)
def test_import_scripts_and_routers_is_lazy():
    """`import scripts`/`import routers` must not pull in NLP, torch or the search stack."""
    code = (
        "import sys; import scripts, routers; "
        "heavy = [m for m in ('camel_tools', 'torch', 'nltk', 'scripts.search', 'routers.search') "
        "if m in sys.modules]; print(heavy)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=_BACKEND, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"
